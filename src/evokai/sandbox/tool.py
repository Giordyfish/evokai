"""The ``code_run`` tool: Python for analysis in an isolated sandbox, on data the agent has.

The model sends the code and, optionally, files (CSV, JSON, text it read from the conversation or
from tools); they appear under ``input/``. What the code prints comes back as the tool result,
with the text files it saved under ``output/``; the PNG and JPEG images it saved there reach the
person as ``chart`` events of type ``image`` (browser turns). Every call is a fresh sandbox.

On the way back, the output passes the guard like any tool output: secrets are removed (as
``output.secrets`` says), instruction-like text is handled by ``untrusted.injection``, and personal
data is tokenised or masked for the model by ``pii.model`` like every tool result. Images cannot be
scanned; they are shown as the code drew them.
"""

from __future__ import annotations

import base64
import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from evokai.guard import pii, secrets
from evokai.sandbox.interpreter import IMAGE_TYPES, Execution, Interpreter, eu_region

if TYPE_CHECKING:
    from evokai.guard.engine import TurnGuard

TOOL_NAME = "code_run"
USAGE_KIND = "code"
USAGE_MODEL = "agentcore:code-interpreter"
MAX_CODE_CHARS = 20_000
MAX_FILES = 5
MAX_FILE_CHARS = 1_000_000
MAX_INPUT_CHARS = 2_000_000
MAX_STREAM_CHARS = 6_000
"""Characters of stdout (and of stderr) the model gets back."""
MAX_FILE_RESULT_CHARS = 4_000
"""Characters of each text file of ``output/`` the model gets back."""
_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,79}$")

NOTE = (
    "Code: code_run runs Python 3 in an isolated sandbox with no internet access, for analysis "
    "of data you already have. Pass the data as files; never invent data. Print the numbers you "
    "need, save charts as PNG files under output/ (the person sees them) and tables as CSV under "
    "output/. Each call starts from an empty sandbox."
)
"""Added to the stable part of the system prompt when the agent has the tool."""


def tool_def() -> dict[str, Any]:
    """The tool's definition.

    Returns:
        name, description, schema.
    """
    return {
        "name": TOOL_NAME,
        "description": (  # model-facing, English like every tool description
            "Run Python 3 in an isolated sandbox (no internet, nothing kept between calls) to "
            "analyse data from the conversation or from other tools: statistics, tables, charts. "
            "Files you pass are under input/ (input/<name>). pandas, numpy and matplotlib are "
            "installed. Print results; save charts as PNG and tables as CSV under output/."
        ),
        "schema": {
            "type": "object",
            "properties": {
                "code": {"type": "string", "description": "The Python source to run"},
                "files": {
                    "type": "array",
                    "maxItems": MAX_FILES,
                    "description": "Text files to put under input/ first (CSV, JSON, text)",
                    "items": {
                        "type": "object",
                        "properties": {
                            "name": {"type": "string", "description": "File name, like data.csv"},
                            "content": {"type": "string", "description": "The file's text"},
                        },
                        "required": ["name", "content"],
                    },
                },
            },
            "required": ["code"],
        },
    }


@dataclass
class CodeOutcome:
    """What a call gives back: the text for the model, images for the person, span and usage."""

    ok: bool
    text: str
    charts: list[dict[str, Any]] = field(default_factory=list)
    attrs: dict[str, Any] = field(default_factory=dict)
    session_ms: int = 0
    code_chars: int = 0
    ran: bool = False
    """Whether a sandbox session was used (and is billed)."""


def parse_args(args: dict[str, Any]) -> tuple[str, list[tuple[str, str]]] | str:
    """Check a call's arguments.

    Returns:
        (code, files), or the problem to tell the model.
    """
    code = args.get("code")
    if not isinstance(code, str) or not code.strip():
        return "Give the Python code to run."
    if len(code) > MAX_CODE_CHARS:
        return f"The code is too long (at most {MAX_CODE_CHARS} characters)."
    raw = args.get("files") or []
    if not isinstance(raw, list) or len(raw) > MAX_FILES:
        return f"files must be a list of at most {MAX_FILES} files."
    files: list[tuple[str, str]] = []
    total = 0
    for f in raw:
        if not isinstance(f, dict):
            return "Each file needs a name and a content."
        name, content = f.get("name"), f.get("content")
        if not isinstance(name, str) or not _NAME.match(name) or ".." in name:
            return "A file name has letters, digits, dots, - and _ only (like data.csv)."
        if not isinstance(content, str) or len(content) > MAX_FILE_CHARS:
            return f"A file's content is text of at most {MAX_FILE_CHARS} characters."
        if name in {n for n, _ in files}:
            return f"Two files are named {name}."
        total += len(content)
        files.append((name, content))
    if total > MAX_INPUT_CHARS:
        return f"The files are too large together (at most {MAX_INPUT_CHARS} characters)."
    return code, files


def _clip(text: str, limit: int) -> str:
    return text if len(text) <= limit else f"{text[:limit]}\n[... {len(text) - limit} more]"


def _scrub(text: str, guard: TurnGuard | None, hits: list[str]) -> str:
    """Remove secrets from an output, as ``output.secrets`` says.

    Returns:
        The text.
    """
    if not text or (guard is not None and not guard.policy.on("output.secrets")):
        return text
    known = guard.guard.known_secrets if guard is not None else ()
    found = secrets.find(text, known)
    if not found:
        return text
    hits.extend(f.rule for f in found)
    if guard is None or guard.policy.enforced("output.secrets"):
        return secrets.redact(text, found)
    return text


def compose(ex: Execution, guard: TurnGuard | None, tool: str = TOOL_NAME) -> CodeOutcome:
    """Turn an execution into the tool result, the image charts and the span's attributes.

    Args:
        ex: The execution.
        guard: The turn's guard (secrets in the output).
        tool: The tool that ran (``code_run`` or ``skill_run``), for the security event.

    Returns:
        The outcome.
    """
    hits: list[str] = []
    parts: list[str] = []
    if ex.timed_out:
        parts.append("The code did not finish in time and was stopped. Make it faster or smaller.")
    stdout = _scrub(ex.stdout, guard, hits)
    stderr = _scrub(ex.stderr, guard, hits)
    if stdout:
        parts.append(f"stdout:\n{_clip(stdout, MAX_STREAM_CHARS)}")
    if stderr:
        parts.append(f"stderr:\n{_clip(stderr, MAX_STREAM_CHARS)}")
    charts: list[dict[str, Any]] = []
    texts = 0
    for f in ex.files:
        if f.media_type in IMAGE_TYPES and f.data is not None:
            charts.append({
                "type": "image",
                "title": f.name.rsplit(".", 1)[0].replace("_", " ").replace("-", " ")[:80],
                "subtitle": "",
                "mediaType": f.media_type,
                "data": base64.b64encode(f.data).decode("ascii"),
            })
        elif f.text is not None:
            texts += 1
            body = _clip(_scrub(f.text, guard, hits), MAX_FILE_RESULT_CHARS)
            parts.append(f"output/{f.name}:\n{body}")
    if charts:
        names = ", ".join(c["title"] for c in charts)
        parts.append(f"Charts shown to the person: {names}. Do not describe them as missing.")
    if ex.skipped:
        parts.append(f"Not returned (type or size): {', '.join(ex.skipped[:10])}.")
    if not parts:
        parts.append("The code ran and printed nothing.")
    if hits and guard is not None:
        action = "redacted" if guard.policy.enforced("output.secrets") else "logged"
        guard.record(
            "output.secrets",
            hits[0],
            action,
            details={"count": len(hits), "source": tool},
            tool=tool,
            direction="output",
        )
    text = "\n\n".join(parts)
    personal = pii.counts(pii.find(text))
    ok = not ex.error and not ex.timed_out and (ex.exit_code in (None, 0))
    attrs = {
        "type": "code",
        "outcome": "ok" if ok else "error",
        "exitCode": ex.exit_code,
        "timedOut": ex.timed_out,
        "sessionMs": ex.session_ms,
        "executionMs": ex.execution_ms,
        "filesOut": len(ex.files),
        "images": len(charts),
        "textFiles": texts,
        "outputChars": len(text),
        "secretsFound": len(hits),
        "personalDataFound": sum(personal.values()),
    }
    return CodeOutcome(ok=ok, text=text, charts=charts, attrs=attrs, session_ms=ex.session_ms)


async def run_code(
    interpreter: Interpreter,
    args: dict[str, Any],
    guard: TurnGuard | None,
    *,
    timeout: float,
) -> CodeOutcome:
    """Run one ``code_run`` call.

    Returns:
        The outcome (a failure is an outcome, never raised).
    """
    parsed = parse_args(args)
    base = {
        "sandbox": interpreter.kind,
        "region": interpreter.region,
        "interpreter": interpreter.identifier,
        "network": "none",
    }
    if isinstance(parsed, str):
        return CodeOutcome(
            ok=False, text=parsed, attrs={**base, "type": "code", "outcome": "error"}
        )
    code, files = parsed
    try:
        ex = await interpreter.run(code, files, timeout=timeout)
    except Exception as e:
        return CodeOutcome(
            ok=False,
            text="The sandbox is not available right now. Answer without running code.",
            attrs={**base, "type": "code", "outcome": "error", "errorType": type(e).__name__},
            code_chars=len(code),
        )
    out = compose(ex, guard)
    out.attrs = {**base, **out.attrs, "filesIn": len(files), "codeChars": len(code)}
    out.code_chars = len(code)
    out.ran = True
    return out


def cost_micro_usd(session_ms: int, usd_per_second: float) -> int:
    """The estimated cost of a sandbox session, in millionths of a dollar (never shown).

    Returns:
        The cost, rounded up to a whole second.
    """
    seconds = max(1, -(-max(session_ms, 0) // 1000))
    return round(seconds * usd_per_second * 1_000_000)


class CodeService:
    """The deployment's code execution: whether it is on, and the interpreter."""

    def __init__(self, settings: Any, interpreter: Interpreter | None = None) -> None:
        """Configure from the deployment settings.

        Args:
            settings: ``EVOKAI_CODE_*`` settings.
            interpreter: The interpreter (tests pass a fake); else AgentCore in the region, or
                the Azure Container Apps session pool (``EVOKAI_CODE_SANDBOX=azure``).
        """
        self.settings = settings
        self.region = str(getattr(settings, "code_region", None) or settings.aws_region)
        self.timeout = float(settings.code_timeout_seconds)
        self.usd_per_second = float(settings.code_usd_per_second)
        self.problem: str | None = None
        self.interpreter: Interpreter | None = interpreter
        if interpreter is not None:
            return
        if not settings.code_enabled:
            self.problem = "EVOKAI_CODE_ENABLED is off"
        elif getattr(settings, "code_sandbox", "agentcore") == "azure":
            from evokai.sandbox.interpreter import ContainerAppsInterpreter

            endpoint = getattr(settings, "code_session_pool_endpoint", None)
            if not endpoint:
                self.problem = "EVOKAI_CODE_SESSION_POOL_ENDPOINT is not set"
            else:
                try:
                    self.interpreter = ContainerAppsInterpreter(endpoint)
                    self.region = self.interpreter.region
                except ValueError as e:
                    self.problem = str(e)
        elif not eu_region(self.region):
            self.problem = f"region {self.region} is outside the EU"
        else:
            from evokai.sandbox.interpreter import AgentCoreInterpreter

            self.interpreter = AgentCoreInterpreter(self.region, settings.code_interpreter_id)

    @property
    def processor(self) -> str:
        """The sandbox's entry in the provider catalog.

        Returns:
            ``agentcore-code-interpreter`` or ``azure-container-apps-sessions``.
        """
        default = (
            "azure-container-apps-sessions"
            if getattr(self.settings, "code_sandbox", "agentcore") == "azure"
            else "agentcore-code-interpreter"
        )
        return getattr(self.interpreter, "processor", default)

    @property
    def usage_model(self) -> str:
        """The model name a sandbox session's usage record carries.

        Returns:
            ``agentcore:code-interpreter`` or ``azure:container-apps-sessions``.
        """
        return getattr(self.interpreter, "usage_model", USAGE_MODEL)

    @property
    def available(self) -> bool:
        """Whether ``code_run`` can be offered at all.

        Returns:
            True with an interpreter.
        """
        return self.interpreter is not None

    def status(self) -> dict[str, Any]:
        """What the console and the compliance pack say about it.

        Returns:
            enabled, sandbox, region, interpreter, network, timeoutSeconds, problem.
        """
        i = self.interpreter
        return {
            "enabled": i is not None,
            "sandbox": i.kind if i else None,
            "region": i.region if i else self.region,
            "interpreter": i.identifier if i else self.settings.code_interpreter_id,
            "network": "none",
            "timeoutSeconds": self.timeout,
            "problem": self.problem,
        }
