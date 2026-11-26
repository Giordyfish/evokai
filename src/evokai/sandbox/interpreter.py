"""Code interpreters: where ``code_run`` executes Python, isolated from Evokai and the network.

Two implementations, both with a session per call (nothing survives between calls, so one person's
data never meets another's):

- Amazon Bedrock AgentCore Code Interpreter (``EVOKAI_CODE_SANDBOX=agentcore``, the default): start,
  write the input files, execute, list and read ``output/``, stop. The AWS-managed interpreter
  ``aws.codeinterpreter.v1`` runs in sandbox network mode (no internet; the session has no
  execution role, so no AWS resource either).
- Azure Container Apps dynamic sessions, a Python code interpreter session pool
  (``EVOKAI_CODE_SANDBOX=azure``, :class:`ContainerAppsInterpreter`): each session is isolated by a
  Hyper-V boundary; Evokai reaches the pool's management endpoint with a Microsoft Entra ID token of
  its managed identity (Azure ContainerApps Session Executor role).

boto3 is synchronous: a call runs in a thread. Past the timeout the session is stopped from
another thread, which ends the execution server side; what the sandbox printed until then is lost.

No local fallback is shipped: running untrusted model-written code in Evokai's own container, or a
container next to it, is not made safe by this module (see ``docs/code.md``).
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import threading
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Protocol
from urllib.parse import urlsplit

log = logging.getLogger("evokai.sandbox")

EU_PREFIX = "eu-"
"""Only regions whose name starts with this are used."""
OUTPUT_DIR = "output"
INPUT_DIR = "input"
IMAGE_TYPES = frozenset({"image/png", "image/jpeg"})
TEXT_TYPES = frozenset({
    "text/csv",
    "text/plain",
    "text/markdown",
    "application/json",
    "text/tab-separated-values",
})
TEXT_SUFFIXES = (".csv", ".txt", ".md", ".json", ".tsv")
MAX_IMAGES = 3
MAX_IMAGE_BYTES = 512 * 1024
MAX_TEXT_FILES = 4


@dataclass
class OutputFile:
    """A file the code left in ``output/``."""

    name: str
    media_type: str
    text: str | None = None
    data: bytes | None = None

    @property
    def size(self) -> int:
        """Bytes (or characters of a text).

        Returns:
            The size.
        """
        return len(self.data) if self.data is not None else len(self.text or "")


@dataclass
class Execution:
    """What one call did."""

    stdout: str = ""
    stderr: str = ""
    exit_code: int | None = None
    error: bool = False
    timed_out: bool = False
    files: list[OutputFile] = field(default_factory=list)
    """Images and text files read back (within the caps)."""
    skipped: list[str] = field(default_factory=list)
    """Output files listed but not read (another type, or over the caps)."""
    session_ms: int = 0
    """From the session's start to its stop: what is billed."""
    execution_ms: int = 0
    session_id: str = ""


class Interpreter(Protocol):
    """A place to run code.

    An interpreter may also name its entry in the provider catalog (``processor``, for the data
    protection profile) and the model its usage records carry (``usage_model``); without them it
    is taken for AgentCore.
    """

    kind: str
    region: str
    identifier: str

    async def run(self, code: str, files: list[tuple[str, str]], *, timeout: float) -> Execution:
        """Run Python with some input files; collect stdout, stderr and ``output/``."""
        ...


def eu_region(region: str | None) -> bool:
    """Whether a region is in the EU (the only ones the sandbox runs in).

    Returns:
        True for ``eu-*``.
    """
    return bool(region) and str(region).startswith(EU_PREFIX)


def _media_type(name: str, given: str | None) -> str:
    if given:
        return given
    lower = name.lower()
    if lower.endswith(".png"):
        return "image/png"
    if lower.endswith((".jpg", ".jpeg")):
        return "image/jpeg"
    if lower.endswith(".csv"):
        return "text/csv"
    if lower.endswith(".json"):
        return "application/json"
    return "text/plain" if lower.endswith(TEXT_SUFFIXES) else "application/octet-stream"


def _is_text(name: str, media: str) -> bool:
    return media in TEXT_TYPES or media.startswith("text/") or name.lower().endswith(TEXT_SUFFIXES)


class AgentCoreInterpreter:
    """Amazon Bedrock AgentCore Code Interpreter, one session per call."""

    kind = "agentcore"
    processor = "agentcore-code-interpreter"
    usage_model = "agentcore:code-interpreter"

    def __init__(
        self,
        region: str,
        identifier: str = "aws.codeinterpreter.v1",
        *,
        client_factory: Callable[[], Any] | None = None,
    ) -> None:
        """Configure the interpreter (the client is made on first use).

        Args:
            region: An EU region where AgentCore built-in tools are available.
            identifier: The code interpreter (the AWS-managed one by default).
            client_factory: Makes the ``bedrock-agentcore`` client (tests pass a fake).

        Raises:
            ValueError: A region outside the EU.
        """
        if not eu_region(region):
            raise ValueError(f"code interpreter region must be in the EU, not {region}")
        self.region = region
        self.identifier = identifier
        self._factory = client_factory
        self._client: Any = None
        self._lock = threading.Lock()

    @property
    def client(self) -> Any:
        """The ``bedrock-agentcore`` client (thread-safe, one per process).

        Returns:
            The client.
        """
        with self._lock:
            if self._client is None:
                self._client = self._factory() if self._factory else self._make_client()
            return self._client

    def _make_client(self) -> Any:
        import boto3
        from botocore.config import Config

        return boto3.client(
            "bedrock-agentcore",
            region_name=self.region,
            config=Config(
                connect_timeout=5,
                read_timeout=900,
                retries={"total_max_attempts": 2, "mode": "standard"},
                max_pool_connections=16,
            ),
        )

    async def run(self, code: str, files: list[tuple[str, str]], *, timeout: float) -> Execution:
        """Run Python in a fresh session.

        Args:
            code: The Python source.
            files: (name, text) written under ``input/`` first.
            timeout: Seconds for the whole call; past it the session is stopped.

        Returns:
            The execution (``timed_out`` when the timeout stopped it).
        """
        out = Execution()
        state: dict[str, Any] = {}
        t0 = time.perf_counter()
        session_seconds = int(timeout) + 30
        task = asyncio.ensure_future(
            asyncio.to_thread(self._run, code, files, out, state, session_seconds)
        )
        try:
            await asyncio.wait_for(asyncio.shield(task), timeout)
        except TimeoutError:
            state["cancel"] = True
            out.timed_out = True
            out.error = True
        finally:
            if "stopped" not in state and state.get("session"):
                await asyncio.to_thread(self._stop, state)
            if not task.done():
                task.add_done_callback(_consume)
        out.session_ms = round(state.get("ms", (time.perf_counter() - t0) * 1000))
        return out

    def _invoke(self, session: str, name: str, arguments: dict[str, Any]) -> list[dict[str, Any]]:
        r = self.client.invoke_code_interpreter(
            codeInterpreterIdentifier=self.identifier,
            sessionId=session,
            name=name,
            arguments=arguments,
        )
        results = []
        for event in r.get("stream") or []:
            if "result" in event:
                results.append(event["result"])
            else:
                problem = next(iter(event), "unknown")
                raise RuntimeError(f"code interpreter stream: {problem}")
        return results

    def _run(
        self,
        code: str,
        files: list[tuple[str, str]],
        out: Execution,
        state: dict[str, Any],
        session_seconds: int,
    ) -> None:
        state["t0"] = time.perf_counter()
        started = self.client.start_code_interpreter_session(
            codeInterpreterIdentifier=self.identifier,
            name="evokai-code-run",
            sessionTimeoutSeconds=session_seconds,
        )
        state["session"] = out.session_id = str(started["sessionId"])
        try:
            if state.get("cancel"):
                return  # the call timed out while the session started
            if files:
                self._invoke(
                    out.session_id,
                    "writeFiles",
                    {"content": [{"path": f"{INPUT_DIR}/{n}", "text": t} for n, t in files]},
                )
            for result in self._invoke(
                out.session_id, "executeCode", {"language": "python", "code": code}
            ):
                if state.get("cancel"):
                    return
                sc = result.get("structuredContent") or {}
                out.stdout += str(sc.get("stdout") or "")
                out.stderr += str(sc.get("stderr") or "")
                if sc.get("exitCode") is not None:
                    out.exit_code = int(sc["exitCode"])
                out.execution_ms += round(float(sc.get("executionTime") or 0) * 1000)
                if not sc:
                    out.stdout += "".join(
                        str(c.get("text") or "")
                        for c in result.get("content") or []
                        if c.get("type") == "text"
                    )
                out.error = out.error or bool(result.get("isError"))
            self._collect(out)
        finally:
            self._stop(state)

    def _collect(self, out: Execution) -> None:
        """Read back the images and text files of ``output/`` (within the caps)."""
        listed = []
        for result in self._invoke(out.session_id, "listFiles", {"directoryPath": OUTPUT_DIR}):
            if result.get("isError"):
                return  # no output directory
            for c in result.get("content") or []:
                if c.get("type") not in {"resource_link", "resource"}:
                    continue
                uri = str(c.get("uri") or (c.get("resource") or {}).get("uri") or "")
                name = str(c.get("name") or uri.rsplit("/", 1)[-1])
                if name and c.get("description") != "Directory":
                    listed.append((name, _media_type(name, c.get("mimeType"))))
        wanted: list[str] = []
        images = texts = 0
        for name, media in sorted(listed):
            if media in IMAGE_TYPES and images < MAX_IMAGES:
                images += 1
                wanted.append(name)
            elif _is_text(name, media) and texts < MAX_TEXT_FILES:
                texts += 1
                wanted.append(name)
            else:
                out.skipped.append(name)
        if not wanted:
            return
        paths = [f"{OUTPUT_DIR}/{n}" for n in wanted]
        for result in self._invoke(out.session_id, "readFiles", {"paths": paths}):
            for c in result.get("content") or []:
                res = c.get("resource") or {}
                name = str(res.get("uri") or "").rsplit("/", 1)[-1]
                media = _media_type(name, res.get("mimeType"))
                blob = res.get("blob")
                if media in IMAGE_TYPES:
                    data = blob if isinstance(blob, bytes | bytearray) else None
                    if data is None or len(data) > MAX_IMAGE_BYTES:
                        out.skipped.append(name)
                        continue
                    out.files.append(OutputFile(name, media, data=bytes(data)))
                elif res.get("text") is not None:
                    out.files.append(OutputFile(name, media, text=str(res["text"])))
                elif isinstance(blob, bytes | bytearray):
                    out.files.append(
                        OutputFile(name, media, text=bytes(blob).decode("utf-8", "replace"))
                    )

    def _stop(self, state: dict[str, Any]) -> None:
        if "stopped" in state or not state.get("session"):
            return
        state["stopped"] = True
        state["ms"] = (time.perf_counter() - state.get("t0", time.perf_counter())) * 1000
        try:
            self.client.stop_code_interpreter_session(
                codeInterpreterIdentifier=self.identifier, sessionId=state["session"]
            )
        except Exception as e:
            log.warning("code_session_not_stopped", extra={"error": type(e).__name__})


def _consume(task: asyncio.Future[Any]) -> None:
    """Swallow the result of a call abandoned at its timeout (its session is already stopped)."""
    if not task.cancelled():
        task.exception()


AZURE_EUROPE = frozenset({
    "austriaeast", "belgiumcentral", "denmarkeast", "francecentral", "francesouth",
    "germanynorth", "germanywestcentral", "italynorth", "northeurope", "norwayeast", "norwaywest",
    "polandcentral", "spaincentral", "swedencentral", "switzerlandnorth", "switzerlandwest",
    "uksouth", "ukwest", "westeurope",
})  # fmt: skip
"""Azure regions in Europe (https://learn.microsoft.com/en-us/azure/reliability/regions-list),
the only ones the Azure sandbox runs in (like ``eu-*`` on AWS)."""

SESSIONS_API = "2025-10-02-preview"
"""Executions (``/executions``) of the code interpreter session pool."""
SESSION_API = "2025-02-02-preview"
"""Deleting a session (``DELETE /session``)."""
WORKDIR = "/mnt/data"
"""Where a session's files live; ``input/`` and ``output/`` are made under it."""

_SETUP = """
import json as _j, os as _o
_o.makedirs({work!r} + "/input", exist_ok=True)
_o.makedirs({work!r} + "/output", exist_ok=True)
_o.chdir({work!r})
for _n, _t in _j.loads({files!r}):
    with open("input/" + _n, "w", encoding="utf-8") as _f:
        _f.write(_t)
"""

_COLLECT = """
import base64 as _b, json as _j, os as _o
_d, _r = {work!r} + "/output", []
for _n in sorted(_o.listdir(_d)) if _o.path.isdir(_d) else []:
    _p = _o.path.join(_d, _n)
    if not _o.path.isfile(_p):
        continue
    _s = _o.path.getsize(_p)
    _r.append([_n, _s, _b.b64encode(open(_p, "rb").read()).decode() if _s <= {cap} else None])
print({marker!r} + _j.dumps(_r))
"""
_MARKER = "EVOKAI_OUTPUT_FILES:"
_OK = ("success", "succeeded")


def azure_region_of(endpoint: str) -> str:
    """The Azure region of a session pool's management endpoint.

    ``https://<region>.dynamicsessions.io/subscriptions/.../sessionPools/<name>``.

    Returns:
        The region (lower case), or an empty string.
    """
    host = (urlsplit(endpoint).hostname or "").lower()
    return host.split(".", 1)[0] if host.endswith(".dynamicsessions.io") else ""


class ContainerAppsInterpreter:
    """Azure Container Apps dynamic sessions (Python code interpreter), one session per call.

    REST, read 26 November 2026 (not verified against a live Azure account):
    ``POST {pool}/executions?api-version=2025-10-02-preview&identifier=<id>`` with
    ``{"properties": {"codeInputType": "inline", "executionType": "synchronous", "code": ...}}``
    answers ``{"properties": {"status", "stdout", "stderr", "executionResult",
    "executionTimeInMilliseconds"}}``; ``DELETE {pool}/session?api-version=2025-02-02-preview&
    identifier=<id>`` ends the session. Bearer token for ``https://dynamicsessions.io/.default``.
    Inputs are written and ``output/`` is read back by two extra executions in the same session, so
    only documented calls are used. An execution runs at most 220 seconds.
    https://learn.microsoft.com/en-us/azure/container-apps/sessions-code-interpreter
    https://learn.microsoft.com/en-us/azure/container-apps/sessions-tutorial-nodejs
    """

    kind = "azure-sessions"
    processor = "azure-container-apps-sessions"
    usage_model = "azure:container-apps-sessions"

    def __init__(self, endpoint: str, *, http: Any = None, token: Any = None) -> None:
        """Bind the session pool.

        Args:
            endpoint: The pool's management endpoint (``poolManagementEndpoint``).
            http: An ``httpx.AsyncClient`` (tests pass a mock transport).
            token: An async callable returning a bearer token (default: the managed identity).

        Raises:
            ValueError: The endpoint is not a session pool in a European region.
        """
        self.endpoint = endpoint.rstrip("/")
        self.region = azure_region_of(self.endpoint)
        if self.region not in AZURE_EUROPE:
            raise ValueError(
                "the session pool must be in a European Azure region, not "
                f"{self.region or endpoint}"
            )
        self.identifier = self.endpoint.rsplit("/", 1)[-1]
        self._http = http
        self._token = token

    @property
    def http(self) -> Any:
        """The HTTP client (made on first use).

        Returns:
            The client.
        """
        if self._http is None:
            import httpx

            self._http = httpx.AsyncClient(timeout=httpx.Timeout(240.0, connect=10.0))
        return self._http

    async def _headers(self) -> dict[str, str]:
        if self._token is None:
            from evokai.core.azure import DYNAMIC_SESSIONS, token_provider

            self._token = token_provider(DYNAMIC_SESSIONS)
        return {"Authorization": f"Bearer {await self._token()}"}

    async def _execute(self, session: str, code: str) -> dict[str, Any]:
        r = await self.http.post(
            f"{self.endpoint}/executions",
            params={"api-version": SESSIONS_API, "identifier": session},
            json={
                "properties": {
                    "codeInputType": "inline",
                    "executionType": "synchronous",
                    "code": code,
                }
            },
            headers=await self._headers(),
        )
        if r.status_code >= 400:
            raise RuntimeError(f"session pool: HTTP {r.status_code}")
        return dict(r.json().get("properties") or {})

    async def run(self, code: str, files: list[tuple[str, str]], *, timeout: float) -> Execution:
        """Run Python in a fresh session, then delete it.

        Args:
            code: The Python source.
            files: (name, text) written under ``input/`` first.
            timeout: Seconds for the whole call; past it the session is deleted.

        Returns:
            The execution (``timed_out`` when the timeout stopped it).
        """
        out = Execution(session_id=f"evokai-{uuid.uuid4().hex}")
        t0 = time.perf_counter()
        try:
            await asyncio.wait_for(self._run(out, code, files), timeout)
        except TimeoutError:
            out.timed_out = True
            out.error = True
        except Exception as e:
            out.error = True
            out.stderr += f"\n[sandbox error: {str(e)[:200]}]"
        finally:
            await self._delete(out.session_id)
        out.session_ms = round((time.perf_counter() - t0) * 1000)
        return out

    async def _run(self, out: Execution, code: str, files: list[tuple[str, str]]) -> None:
        setup = _SETUP.format(work=WORKDIR, files=json.dumps(files))
        prepared = await self._execute(out.session_id, setup)
        if str(prepared.get("status", "")).lower() not in _OK:
            raise RuntimeError("the session could not be prepared")
        p = await self._execute(out.session_id, code)
        out.stdout = str(p.get("stdout") or "")
        result = p.get("executionResult")
        if result not in (None, ""):
            sep = "" if not out.stdout or out.stdout.endswith("\n") else "\n"
            out.stdout += sep + str(result)
        out.stderr = str(p.get("stderr") or "")
        out.execution_ms = int(p.get("executionTimeInMilliseconds") or 0)
        out.error = str(p.get("status", "")).lower() not in _OK
        collect = _COLLECT.format(work=WORKDIR, cap=MAX_IMAGE_BYTES, marker=_MARKER)
        collected = await self._execute(out.session_id, collect)
        self._collect(out, str(collected.get("stdout") or ""))

    def _collect(self, out: Execution, stdout: str) -> None:
        """Keep the images and text files of ``output/`` (within the caps)."""
        line = next((x for x in stdout.splitlines() if x.startswith(_MARKER)), None)
        if line is None:
            return
        images = texts = 0
        for name, _size, b64 in json.loads(line[len(_MARKER) :]):
            media = _media_type(str(name), None)
            data = base64.b64decode(b64) if b64 is not None else None
            if media in IMAGE_TYPES and images < MAX_IMAGES and data is not None:
                images += 1
                out.files.append(OutputFile(str(name), media, data=data))
            elif _is_text(str(name), media) and texts < MAX_TEXT_FILES and data is not None:
                texts += 1
                text = data.decode("utf-8", "replace")
                out.files.append(OutputFile(str(name), media, text=text))
            else:
                out.skipped.append(str(name))

    async def _delete(self, session: str) -> None:
        try:
            await self.http.delete(
                f"{self.endpoint}/session",
                params={"api-version": SESSION_API, "identifier": session},
                headers=await self._headers(),
            )
        except Exception as e:
            log.warning("code_session_not_stopped", extra={"error": type(e).__name__})
