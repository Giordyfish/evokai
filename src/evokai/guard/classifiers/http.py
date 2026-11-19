"""Guard classifiers over HTTP: Azure, Google, Mistral, OpenAI and a self-hosted Llama Guard.

Each is a pluggable :class:`~evokai.guard.classifiers.base.InjectionClassifier` with an entry in the
provider catalog (``processors``: where it runs, who controls it, its price), so an application's
data protection profile decides whether its texts may reach it (``evokai.protection``). Two jobs,
kept apart:

- **injection** (``injection.classifier``): Azure AI Content Safety Prompt Shields
  (``azure_prompt_shields``: user prompt attacks and attacks hidden in documents), Google Model
  Armor's prompt injection and jailbreak filter (``model_armor``), and Mistral's moderation model's
  ``jailbreaking`` category (``mistral_moderation``).
- **moderation** (``moderation.classifier``, the ``input.moderation`` control): harmful content,
  which is *not* injection detection: a prompt injection is usually polite text. Azure Content
  Safety's text analysis (``azure_content_safety``), OpenAI's moderation endpoint
  (``openai_moderation``), Mistral's moderation categories (``mistral_moderation``), Model Armor's
  responsible AI filters (``model_armor``) and a Llama Guard served by the deployment behind an
  OpenAI-compatible chat endpoint (``llama_guard``).

Every classifier fails open like the built-in ones: a timeout, an error or a missing key never
blocks a turn; the turn records ``classifier_unavailable`` and goes on with the rules. Requests
carry no retries and a short timeout; texts go in pieces no longer than each API accepts, at most
the turn's character budget in all. Scores follow one scale: 0.95 for an attack or harmful content
the service is sure of, 0.8 for a medium confidence, 0.5 for a low one.
"""

from __future__ import annotations

import asyncio
import math
import os
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any, ClassVar

import httpx

from evokai.guard.classifiers.base import Result, Source, Status, budgeted, windows

OVERLAP = 100
CONCURRENCY = 4
LEVELS = {"HIGH": 0.95, "MEDIUM_AND_ABOVE": 0.8, "MEDIUM": 0.8, "LOW_AND_ABOVE": 0.5, "LOW": 0.5}
"""Model Armor's confidence levels as scores."""
AZURE_SEVERITY = {0: 0.0, 1: 0.3, 2: 0.5, 3: 0.6, 4: 0.8, 5: 0.85, 6: 0.95, 7: 0.95}
"""Azure Content Safety severities (0, 2, 4, 6 with four levels) as scores."""
MISTRAL_HARMFUL = (
    "sexual",
    "hate_and_discrimination",
    "violence_and_threats",
    "dangerous_and_criminal_content",
    "dangerous",
    "criminal",
    "selfharm",
)
"""Mistral's categories of harmful content (``health``, ``financial``, ``law`` and ``pii`` flag
advice and personal data, not harm, and are left to Evokai's own controls)."""


@dataclass
class Call:
    """One piece's answer: its score and what it was billed."""

    score: float
    units: int = 0


class HttpClassifier:
    """The shared loop: pieces, concurrency, the overall timeout, billing and failures."""

    id: ClassVar[str]
    processor: ClassVar[str]
    """Its id in the provider catalog (``processors``)."""
    detects: ClassVar[str]
    """``injection``, ``moderation`` or ``both``."""
    piece_chars: ClassVar[int] = 10_000
    billed: ClassVar[bool] = True

    def __init__(
        self,
        *,
        timeout: float = 1.5,
        http: httpx.AsyncClient | None = None,
        mode: str = "injection",
    ) -> None:
        """Configure the classifier.

        Args:
            timeout: Seconds a whole classification may take.
            http: The HTTP client (tests pass a mock transport).
            mode: ``injection`` or ``moderation``: what to score, for a classifier that does both.
        """
        self.timeout = timeout
        self._http = http
        self.mode = mode
        self.last_error: str | None = None

    @property
    def http(self) -> httpx.AsyncClient:
        """The HTTP client (made on first use).

        Returns:
            The client.
        """
        if self._http is None:
            self._http = httpx.AsyncClient(timeout=httpx.Timeout(self.timeout, connect=1.0))
        return self._http

    def missing(self) -> str | None:
        """What the deployment has not configured.

        Returns:
            The reason it is unavailable, or None.
        """
        return None

    def status(self) -> Status:
        """Available when configured.

        Returns:
            The status (with the last failure, if any).
        """
        if reason := self.missing():
            return Status(self.id, False, reason)
        return Status(
            self.id, True, f"last call failed: {self.last_error}" if self.last_error else None
        )

    async def call(self, piece: str, source: Source) -> Call:
        """Classify one piece.

        Returns:
            Its score and billed units.
        """
        raise NotImplementedError

    async def classify(self, text: str, *, source: Source, budget: int) -> Result:
        """Check a text in pieces, at most ``budget`` characters in all.

        Returns:
            The highest score, characters sent, units billed and calls made; a failure or a timeout
            is in ``Result.error``.

        Raises:
            RuntimeError: The classifier is not configured.
        """
        if reason := self.missing():
            raise RuntimeError(reason)
        pieces, capped = budgeted(windows(text, self.piece_chars, OVERLAP), budget)
        result = Result(capped=capped)
        if not pieces:
            return result
        t0 = time.perf_counter()
        gate = asyncio.Semaphore(CONCURRENCY)
        sent: set[int] = set()

        async def one(i: int, piece: str) -> Call:
            async with gate:
                sent.add(i)
                return await self.call(piece, source)

        tasks = [asyncio.create_task(one(i, p)) for i, p in enumerate(pieces)]
        _done, pending = await asyncio.wait(tasks, timeout=self.timeout)
        for t in pending:
            t.cancel()
        errors: list[str] = []
        for i, (piece, task) in enumerate(zip(pieces, tasks, strict=True)):
            if task in pending:
                if i in sent:
                    result.units += self.units(piece)
                    result.chars += len(piece)
                    result.calls += 1
                continue
            if (e := task.exception()) is not None:
                errors.append(_error_name(e))
                continue
            c = task.result()
            result.score = max(result.score, c.score)
            result.units += c.units
            result.chars += len(piece)
            result.calls += 1
        if pending:
            errors.append("timeout")
        result.error = errors[0] if errors else None
        self.last_error = result.error
        result.latency_ms = round((time.perf_counter() - t0) * 1000)
        return result

    def units(self, piece: str) -> int:
        """The units a piece is billed for (in the catalog price's unit).

        Returns:
            Units (0 for a free service).
        """
        del piece
        return 0

    async def warm_up(self) -> None:
        """Nothing to load (a call would be billed)."""

    async def _post(
        self, url: str, body: dict[str, Any], headers: dict[str, str]
    ) -> dict[str, Any]:
        r = await self.http.post(url, json=body, headers=headers, timeout=self.timeout)
        if r.status_code >= 400:
            raise ClassifierHttpError(f"HTTP {r.status_code}")
        return r.json()


class ClassifierHttpError(Exception):
    """The service refused or failed a request."""


def _error_name(e: BaseException) -> str:
    return (str(e) if isinstance(e, ClassifierHttpError) else type(e).__name__)[:80]


def _env(name: str | None) -> str | None:
    return os.environ.get(name) if name else None


class AzurePromptShields(HttpClassifier):
    """Azure AI Content Safety Prompt Shields (``text:shieldPrompt``)."""

    id = "azure_prompt_shields"
    processor = "azure-prompt-shields"
    detects = "injection"
    API_VERSION = "2024-09-01"

    def __init__(self, endpoint: str | None, key: str | None, **kw: Any) -> None:
        """Bind the resource.

        Args:
            endpoint: The Content Safety resource (``EVOKAI_AZURE_CONTENT_SAFETY_ENDPOINT``).
            key: Its key (``EVOKAI_AZURE_CONTENT_SAFETY_KEY``).
            **kw: ``timeout``, ``http``.
        """
        super().__init__(**kw)
        self.endpoint = (endpoint or "").rstrip("/")
        self.key = key

    def missing(self) -> str | None:
        """What is not configured.

        Returns:
            The reason, or None.
        """
        if not self.endpoint or not self.key:
            return "EVOKAI_AZURE_CONTENT_SAFETY_ENDPOINT and _KEY are not set"
        return None

    def units(self, piece: str) -> int:
        """Text records of up to 1,000 characters.

        Returns:
            Records.
        """
        return math.ceil(len(piece) / 1000)

    async def call(self, piece: str, source: Source) -> Call:
        """Ask Prompt Shields: the message as the user prompt, untrusted content as a document.

        Returns:
            0.95 when an attack is detected, else 0.
        """
        body = (
            {"userPrompt": piece, "documents": []}
            if source == "user"
            else {"userPrompt": "", "documents": [piece]}
        )
        r = await self._post(
            f"{self.endpoint}/contentsafety/text:shieldPrompt?api-version={self.API_VERSION}",
            body,
            {"Ocp-Apim-Subscription-Key": str(self.key)},
        )
        hit = bool((r.get("userPromptAnalysis") or {}).get("attackDetected")) or any(
            bool(d.get("attackDetected")) for d in r.get("documentsAnalysis") or []
        )
        return Call(0.95 if hit else 0.0, self.units(piece))


class AzureContentSafety(AzurePromptShields):
    """Azure AI Content Safety text analysis (``text:analyze``): hate, self-harm, sex, violence."""

    id = "azure_content_safety"
    processor = "azure-content-safety-text"
    detects = "moderation"

    async def call(self, piece: str, source: Source) -> Call:
        """Analyse the text in four severity levels.

        Returns:
            The highest category's severity as a score.
        """
        del source
        r = await self._post(
            f"{self.endpoint}/contentsafety/text:analyze?api-version={self.API_VERSION}",
            {"text": piece, "outputType": "FourSeverityLevels"},
            {"Ocp-Apim-Subscription-Key": str(self.key)},
        )
        worst = max(
            (int(c.get("severity") or 0) for c in r.get("categoriesAnalysis") or []), default=0
        )
        return Call(AZURE_SEVERITY.get(worst, 0.95), self.units(piece))


class ModelArmor(HttpClassifier):
    """Google Model Armor ``sanitizeUserPrompt`` with a template the deployment created."""

    id = "model_armor"
    processor = "google-model-armor"
    detects = "both"
    piece_chars = 8_000

    def __init__(
        self,
        template: str | None,
        *,
        endpoint: str | None = None,
        token: Callable[[], Awaitable[str | None]] | None = None,
        **kw: Any,
    ) -> None:
        """Bind the template.

        Args:
            template: ``projects/{p}/locations/{l}/templates/{t}``
                (``EVOKAI_MODEL_ARMOR_TEMPLATE``).
            endpoint: The API host (``EVOKAI_MODEL_ARMOR_ENDPOINT``); default the regional host of
                the template's location, ``https://modelarmor.{location}.rep.googleapis.com``.
            token: Gives an OAuth access token (default: ``EVOKAI_MODEL_ARMOR_TOKEN``, or
                Application Default Credentials when ``google-auth`` is installed).
            **kw: ``timeout``, ``http``, ``mode``.
        """
        super().__init__(**kw)
        self.template = template
        location = (template or "").split("/locations/")[-1].split("/")[0] if template else ""
        self.endpoint = (endpoint or f"https://modelarmor.{location}.rep.googleapis.com").rstrip(
            "/"
        )
        self._token = token or _google_token

    def missing(self) -> str | None:
        """What is not configured.

        Returns:
            The reason, or None.
        """
        return None if self.template else "EVOKAI_MODEL_ARMOR_TEMPLATE is not set"

    def units(self, piece: str) -> int:
        """Tokens, estimated at four characters each (Model Armor bills tokens).

        Returns:
            Tokens.
        """
        return math.ceil(len(piece) / 4)

    async def call(self, piece: str, source: Source) -> Call:
        """Sanitise the text as a user prompt.

        Returns:
            The prompt injection and jailbreak filter's confidence (injection), or the responsible
            AI filters' (moderation).

        Raises:
            ClassifierHttpError: No access token, or the service refused the request.
        """
        del source
        token = await self._token()
        if not token:
            raise ClassifierHttpError("no Google access token")
        r = await self._post(
            f"{self.endpoint}/v1/{self.template}:sanitizeUserPrompt",
            {"userPromptData": {"text": piece}},
            {"Authorization": f"Bearer {token}"},
        )
        results = (r.get("sanitizationResult") or {}).get("filterResults") or {}
        if self.mode == "injection":
            f = (results.get("pi_and_jailbreak") or {}).get("piAndJailbreakFilterResult") or {}
            hit = f.get("matchState") == "MATCH_FOUND"
            score = LEVELS.get(str(f.get("confidenceLevel") or "").upper(), 0.8) if hit else 0.0
        else:
            rai = (results.get("rai") or {}).get("raiFilterResult") or {}
            hit = rai.get("matchState") == "MATCH_FOUND"
            levels = [
                LEVELS.get(str(v.get("confidenceLevel") or "").upper(), 0.8)
                for v in (rai.get("raiFilterTypeResults") or {}).values()
                if v.get("matchState") == "MATCH_FOUND"
            ]
            score = max(levels, default=0.8) if hit else 0.0
        return Call(score, self.units(piece))


async def _google_token() -> str | None:
    if token := os.environ.get("EVOKAI_MODEL_ARMOR_TOKEN"):
        return token
    try:
        import google.auth  # ty: ignore[import-not-found]
        import google.auth.transport.requests  # ty: ignore[import-not-found]
    except ImportError:
        return None

    def fetch() -> str | None:
        creds, _ = google.auth.default(scopes=["https://www.googleapis.com/auth/cloud-platform"])
        creds.refresh(google.auth.transport.requests.Request())
        return creds.token

    return await asyncio.to_thread(fetch)


class MistralModeration(HttpClassifier):
    """Mistral's moderation model (``/v1/moderations``): harm, and ``jailbreaking`` when present."""

    id = "mistral_moderation"
    processor = "mistral-moderation"
    detects = "both"
    piece_chars = 20_000
    billed = False

    def __init__(
        self,
        key: str | None,
        *,
        base_url: str = "https://api.mistral.ai",
        model: str = "mistral-moderation-latest",
        **kw: Any,
    ) -> None:
        """Bind the account.

        Args:
            key: ``MISTRAL_API_KEY``.
            base_url: ``EVOKAI_MISTRAL_MODERATION_URL`` (``https://api.eu.mistral.ai`` for the EU
                endpoint).
            model: The moderation model.
            **kw: ``timeout``, ``http``, ``mode``.
        """
        super().__init__(**kw)
        self.key = key
        self.base_url = base_url.rstrip("/")
        self.model = model

    def missing(self) -> str | None:
        """What is not configured.

        Returns:
            The reason, or None.
        """
        return None if self.key else "MISTRAL_API_KEY is not set"

    async def call(self, piece: str, source: Source) -> Call:
        """Score the text.

        Returns:
            The ``jailbreaking`` score (injection; 0 when the model does not return that category),
            or the highest harmful category's (moderation).
        """
        del source
        r = await self._post(
            f"{self.base_url}/v1/moderations",
            {"model": self.model, "input": [piece]},
            {"Authorization": f"Bearer {self.key}"},
        )
        res = (r.get("results") or [{}])[0]
        scores = {k: _score_of(v) for k, v in (res.get("category_scores") or {}).items()}
        if self.mode == "injection":
            return Call(scores.get("jailbreaking", 0.0))
        return Call(max((scores.get(k, 0.0) for k in MISTRAL_HARMFUL), default=0.0))


def _score_of(v: Any) -> float:
    if isinstance(v, dict):
        return float(v.get("score") or 0.0)
    return float(v or 0.0)


class OpenAIModeration(HttpClassifier):
    """OpenAI's moderation endpoint: harmful content only (it does not detect prompt injection)."""

    id = "openai_moderation"
    processor = "openai-moderation"
    detects = "moderation"
    piece_chars = 20_000
    billed = False

    def __init__(
        self,
        key: str | None,
        *,
        base_url: str = "https://api.openai.com/v1",
        model: str = "omni-moderation-latest",
        **kw: Any,
    ) -> None:
        """Bind the account.

        Args:
            key: ``OPENAI_API_KEY``.
            base_url: The API base.
            model: The moderation model.
            **kw: ``timeout``, ``http``, ``mode``.
        """
        super().__init__(**kw)
        self.key = key
        self.base_url = base_url.rstrip("/")
        self.model = model

    def missing(self) -> str | None:
        """What is not configured.

        Returns:
            The reason, or None.
        """
        return None if self.key else "OPENAI_API_KEY is not set"

    async def call(self, piece: str, source: Source) -> Call:
        """Score the text.

        Returns:
            The highest category score (at least 0.8 when the text is flagged).
        """
        del source
        r = await self._post(
            f"{self.base_url}/moderations",
            {"model": self.model, "input": piece},
            {"Authorization": f"Bearer {self.key}"},
        )
        res = (r.get("results") or [{}])[0]
        score = max(
            (float(v or 0) for v in (res.get("category_scores") or {}).values()), default=0.0
        )
        if res.get("flagged"):
            score = max(score, 0.8)
        return Call(score)


class LlamaGuard(HttpClassifier):
    """A Llama Guard the deployment serves behind an OpenAI-compatible chat API (vLLM, Ollama)."""

    id = "llama_guard"
    processor = "llama-guard"
    detects = "moderation"
    piece_chars = 8_000
    billed = False

    def __init__(
        self, base_url: str | None, model: str | None, key: str | None = None, **kw: Any
    ) -> None:
        """Bind the server.

        Args:
            base_url: ``EVOKAI_LLAMA_GUARD_URL`` (``http://guard:8000/v1``).
            model: ``EVOKAI_LLAMA_GUARD_MODEL`` (``meta-llama/Llama-Guard-4-12B``).
            key: ``EVOKAI_LLAMA_GUARD_KEY``, when the server asks for one.
            **kw: ``timeout``, ``http``, ``mode``.
        """
        super().__init__(**kw)
        self.base_url = (base_url or "").rstrip("/")
        self.model = model
        self.key = key

    def missing(self) -> str | None:
        """What is not configured.

        Returns:
            The reason, or None.
        """
        if not self.base_url or not self.model:
            return "EVOKAI_LLAMA_GUARD_URL and EVOKAI_LLAMA_GUARD_MODEL are not set"
        return None

    async def call(self, piece: str, source: Source) -> Call:
        """Ask the model whether the text is safe (its first line says ``safe`` or ``unsafe``).

        Returns:
            0.95 when unsafe, else 0.
        """
        del source
        headers = {"Authorization": f"Bearer {self.key}"} if self.key else {}
        r = await self._post(
            f"{self.base_url}/chat/completions",
            {
                "model": self.model,
                "messages": [{"role": "user", "content": piece}],
                "max_tokens": 20,
                "temperature": 0,
            },
            headers,
        )
        text = str((((r.get("choices") or [{}])[0].get("message") or {}).get("content")) or "")
        return Call(0.95 if text.strip().lower().startswith("unsafe") else 0.0)


def from_settings(settings: Any, http: httpx.AsyncClient | None = None) -> list[HttpClassifier]:
    """The HTTP classifiers, configured from the deployment's settings and environment.

    Returns:
        Every classifier (each says itself whether it is configured).
    """
    t = getattr(settings, "guard_classifier_timeout_ms", 1500) / 1000
    endpoint = getattr(settings, "azure_content_safety_endpoint", None)
    key = getattr(settings, "azure_content_safety_key", None)
    return [
        AzurePromptShields(endpoint, key, timeout=t, http=http),
        AzureContentSafety(endpoint, key, timeout=t, http=http),
        ModelArmor(
            getattr(settings, "model_armor_template", None),
            endpoint=getattr(settings, "model_armor_endpoint", None),
            timeout=t,
            http=http,
        ),
        MistralModeration(
            _env("MISTRAL_API_KEY"),
            base_url=getattr(settings, "mistral_moderation_url", None) or "https://api.mistral.ai",
            timeout=t,
            http=http,
        ),
        OpenAIModeration(_env("OPENAI_API_KEY"), timeout=t, http=http),
        LlamaGuard(
            getattr(settings, "llama_guard_url", None),
            getattr(settings, "llama_guard_model", None),
            _env("EVOKAI_LLAMA_GUARD_KEY"),
            timeout=t,
            http=http,
        ),
    ]
