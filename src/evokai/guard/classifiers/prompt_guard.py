"""Meta Llama Prompt Guard 2 as a local prompt-injection classifier (``prompt_guard``).

Llama Prompt Guard 2 86M is a multilingual mDeBERTa-v3 classifier (benign or malicious) with a
512-token window, small enough for the service's CPU: no text leaves the deployment and nothing
is billed. It is optional: install the ``prompt-guard`` extra and point
``EVOKAI_PROMPT_GUARD_MODEL`` at the model. The model is gated on Hugging Face (Llama 4 Community
License): Evokai loads a local folder, or the official repository and nothing else, with the
operator's own token; never a mirror.

Two backends, chosen by what the model folder holds:

- **ONNX** (the production one, and the only one in the image): a folder with ``model.onnx`` and
  ``tokenizer.json`` (``scripts/export-prompt-guard.py`` makes it from the official weights), run
  with ONNX Runtime and the ``tokenizers`` library, both in the extra;
- **Transformers**: the official folder (safetensors) or the repo id
  ``meta-llama/Llama-Prompt-Guard-2-86M`` with ``HF_TOKEN`` set, when ``transformers`` and
  ``torch`` are installed (not part of the extra: they are large; useful on a workstation).

A text is tokenised once and cut into windows of 512 tokens (special tokens included) that overlap
by 64; the text's score is the highest probability of the malicious class over its windows. Windows
run one at a time on a dedicated thread (ONNX Runtime uses its own intra-op threads,
``EVOKAI_PROMPT_GUARD_THREADS``), never on the event loop. A full window takes about a second on
two CPU threads (less with the int8 export), so a classification stops starting windows at its
deadline (``EVOKAI_PROMPT_GUARD_TIMEOUT_MS``, 3 s): what was scored counts, the rest is reported as
a timeout, and a call that waited in the queue past its deadline does no work.
"""

from __future__ import annotations

import asyncio
import importlib
import importlib.util
import json
import logging
import math
import os
import threading
import time
from collections.abc import Callable, Sequence
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Protocol

from evokai.guard.classifiers.base import Result, Source, Status

log = logging.getLogger("evokai.security")

OFFICIAL_REPO = "meta-llama/Llama-Prompt-Guard-2-86M"
MAX_TOKENS = 512
"""The model's window, special tokens included."""
OVERLAP_TOKENS = 64
BATCH = 1
"""Windows scored in one run: one, so the deadline is checked between windows (batching gains
little on a CPU and pads short windows)."""
MAX_WINDOWS = 32
"""Windows of one text at most (about 15,000 tokens); the per-turn character cap comes first."""
_MALICIOUS_WORDS = ("malicious", "injection", "jailbreak", "unsafe")


class Backend(Protocol):
    """A loaded model: text in, the malicious probability of each window out."""

    name: str

    def scores(self, text: str, deadline: float | None = None) -> tuple[list[float], int]:
        """Score the windows of a text in order, until a deadline.

        Args:
            text: The text.
            deadline: ``time.monotonic()`` after which no further window starts.

        Returns:
            The probability of each window scored, and how many windows the text has.
        """
        ...


def _late(deadline: float | None) -> bool:
    return deadline is not None and time.monotonic() >= deadline


def softmax_at(logits: Sequence[float], index: int) -> float:
    """The softmax probability of one class.

    Returns:
        0..1.
    """
    top = max(logits)
    exps = [math.exp(x - top) for x in logits]
    return exps[index] / sum(exps)


def malicious_index(config: dict[str, Any] | None, labels: int = 2) -> int:
    """Which output is the malicious class, from the model's ``id2label``.

    Prompt Guard 2 has two classes, benign (0) and malicious (1); a label naming malice wins when
    the config says so.

    Returns:
        The index.
    """
    id2label = (config or {}).get("id2label") or {}
    for k, v in id2label.items():
        if any(w in str(v).lower() for w in _MALICIOUS_WORDS):
            return int(k)
    return min(1, labels - 1)


def _missing(*modules: str) -> list[str]:
    return [m for m in modules if importlib.util.find_spec(m) is None]


class OnnxBackend:
    """``model.onnx`` and ``tokenizer.json`` with ONNX Runtime and ``tokenizers``."""

    name = "onnx"

    def __init__(self, folder: Path, threads: int) -> None:
        """Load the tokenizer and the session.

        Args:
            folder: Holds ``model.onnx`` (or ``onnx/model.onnx``), ``tokenizer.json`` and
                optionally ``config.json``.
            threads: ONNX Runtime intra-op threads.
        """
        import numpy as np
        import onnxruntime as ort
        from tokenizers import Tokenizer

        self._np = np
        model = folder / "model.onnx"
        if not model.is_file():
            model = folder / "onnx" / "model.onnx"
        self.tokenizer = Tokenizer.from_file(str(folder / "tokenizer.json"))
        self.tokenizer.no_padding()
        self.tokenizer.enable_truncation(max_length=MAX_TOKENS, stride=OVERLAP_TOKENS)
        self.pad_id = _pad_id(self.tokenizer, folder)
        opts = ort.SessionOptions()
        opts.intra_op_num_threads = max(1, threads)
        opts.inter_op_num_threads = 1
        opts.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
        self.session = ort.InferenceSession(str(model), opts, providers=["CPUExecutionProvider"])
        self.inputs = {i.name for i in self.session.get_inputs()}
        config = folder / "config.json"
        cfg = json.loads(config.read_text(encoding="utf-8")) if config.is_file() else None
        out = self.session.get_outputs()[0].shape
        labels = out[-1] if out and isinstance(out[-1], int) else 2
        self.malicious = malicious_index(cfg, labels)

    def scores(self, text: str, deadline: float | None = None) -> tuple[list[float], int]:
        """Score the windows of a text in order, until a deadline.

        Returns:
            The probability of each window scored, and how many windows the text has.
        """
        np = self._np
        enc = self.tokenizer.encode(text)
        windows = [enc, *enc.overflowing][:MAX_WINDOWS]
        out: list[float] = []
        for b in range(0, len(windows), BATCH):
            if _late(deadline):
                break
            batch = windows[b : b + BATCH]
            width = max(len(w.ids) for w in batch)
            ids = np.full((len(batch), width), self.pad_id, dtype=np.int64)
            mask = np.zeros((len(batch), width), dtype=np.int64)
            for i, w in enumerate(batch):
                ids[i, : len(w.ids)] = w.ids
                mask[i, : len(w.ids)] = 1
            feed = {"input_ids": ids, "attention_mask": mask}
            if "token_type_ids" in self.inputs:
                feed["token_type_ids"] = np.zeros_like(ids)
            feed = {k: v for k, v in feed.items() if k in self.inputs}
            logits = np.asarray(self.session.run(None, feed)[0], dtype=np.float64)
            out += [softmax_at(row.tolist(), self.malicious) for row in logits]
        return out, len(windows)


def _pad_id(tokenizer: Any, folder: Path) -> int:
    for token in ("[PAD]", "<pad>"):
        tid = tokenizer.token_to_id(token)
        if tid is not None:
            return int(tid)
    special = folder / "special_tokens_map.json"
    if special.is_file():
        pad = json.loads(special.read_text(encoding="utf-8")).get("pad_token")
        pad = pad.get("content") if isinstance(pad, dict) else pad
        if pad and (tid := tokenizer.token_to_id(pad)) is not None:
            return int(tid)
    return 0


class TransformersBackend:
    """The official weights with ``transformers`` and ``torch`` (a folder or the repo id)."""

    name = "transformers"

    def __init__(self, model: str, threads: int) -> None:
        """Load the tokenizer and the model (the repo id needs ``HF_TOKEN`` and the license).

        Args:
            model: A folder or a Hugging Face repo id.
            threads: Torch intra-op threads.
        """
        transformers = importlib.import_module("transformers")
        self.torch = importlib.import_module("torch")
        self.torch.set_num_threads(max(1, threads))
        token = os.environ.get("HF_TOKEN") or None
        self.tokenizer = transformers.AutoTokenizer.from_pretrained(model, token=token)
        self.model = transformers.AutoModelForSequenceClassification.from_pretrained(
            model, token=token
        )
        self.model.eval()
        cfg = self.model.config
        self.malicious = malicious_index(
            {"id2label": getattr(cfg, "id2label", None)}, int(getattr(cfg, "num_labels", 2))
        )

    def scores(self, text: str, deadline: float | None = None) -> tuple[list[float], int]:
        """Score the windows of a text in order, until a deadline.

        Returns:
            The probability of each window scored, and how many windows the text has.
        """
        enc = self.tokenizer(
            text,
            truncation=True,
            max_length=MAX_TOKENS,
            stride=OVERLAP_TOKENS,
            return_overflowing_tokens=True,
            padding=True,
            return_tensors="pt",
        )
        enc.pop("overflow_to_sample_mapping", None)
        out: list[float] = []
        n = min(int(enc["input_ids"].shape[0]), MAX_WINDOWS)
        with self.torch.inference_mode():
            for b in range(0, n, BATCH):
                if _late(deadline):
                    break
                part = {k: v[b : min(b + BATCH, n)] for k, v in enc.items()}
                probs = self.torch.softmax(self.model(**part).logits.float(), dim=-1)
                out += [float(p) for p in probs[:, self.malicious].tolist()]
        return out, n


def _looks_like_repo(model: str) -> bool:
    return model.count("/") == 1 and not model.startswith((".", "/", "~"))


class PromptGuard:
    """Llama Prompt Guard 2 on the service's CPU."""

    id = "prompt_guard"
    billed = False

    def __init__(
        self,
        model: str | None,
        *,
        threads: int = 2,
        timeout: float = 3.0,
        loader: Callable[[], Backend] | None = None,
    ) -> None:
        """Configure the classifier (the model loads at warm-up or on first use).

        Args:
            model: A folder (ONNX export or the official files) or the official repo id
                (``EVOKAI_PROMPT_GUARD_MODEL``).
            threads: Inference threads (``EVOKAI_PROMPT_GUARD_THREADS``).
            timeout: Seconds a classification may take (``EVOKAI_PROMPT_GUARD_TIMEOUT_MS``).
            loader: Builds the backend (tests pass a tiny fake model).
        """
        self.model = model
        self.threads = threads
        self.timeout = timeout
        self._loader = loader
        self._backend: Backend | None = None
        self._error: str | None = None
        self._lock = threading.Lock()
        self._pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="prompt-guard")

    @classmethod
    def from_settings(cls, settings: Any) -> PromptGuard:
        """From the deployment settings.

        Returns:
            The classifier.
        """
        return cls(
            getattr(settings, "prompt_guard_model", None),
            threads=getattr(settings, "prompt_guard_threads", 2),
            timeout=getattr(settings, "prompt_guard_timeout_ms", 3000) / 1000,
        )

    # availability

    def _problem(self) -> str | None:
        """Why the model cannot be loaded, before trying.

        Returns:
            The reason, or None when it looks loadable.
        """
        if self._loader is not None:
            return None
        if not self.model:
            return "EVOKAI_PROMPT_GUARD_MODEL is not set"
        folder = Path(self.model).expanduser()
        if folder.is_dir():
            if (folder / "model.onnx").is_file() or (folder / "onnx" / "model.onnx").is_file():
                if not (folder / "tokenizer.json").is_file():
                    return f"{folder} has model.onnx but no tokenizer.json"
                if missing := _missing("onnxruntime", "tokenizers", "numpy"):
                    return (
                        f"the prompt-guard extra is not installed (missing {', '.join(missing)}): "
                        "install evokai[prompt-guard]"
                    )
                return None
            if not any(folder.glob("*.safetensors")) and not any(folder.glob("*.bin")):
                return f"{folder} holds no model (model.onnx, or the official safetensors)"
            if missing := _missing("transformers", "torch"):
                return (
                    f"{folder} holds the official weights, which need {' and '.join(missing)}: "
                    "export them to ONNX with scripts/export-prompt-guard.py"
                )
            return None
        if _looks_like_repo(self.model):
            if self.model != OFFICIAL_REPO:
                return (
                    f"{self.model} is not the official repository ({OFFICIAL_REPO}): "
                    "use it or a local folder"
                )
            if missing := _missing("transformers", "torch"):
                return (
                    f"{self.model} is a Hugging Face repo id, which needs {' and '.join(missing)} "
                    "and HF_TOKEN: export it to ONNX with scripts/export-prompt-guard.py instead"
                )
            return None
        return f"{self.model} is not a folder"

    def status(self) -> Status:
        """Available when the model is loaded, or looks loadable and has not failed.

        Returns:
            The status, with the reason it cannot be used.
        """
        if self._backend is not None:
            return Status(self.id, True)
        if self._error:
            return Status(self.id, False, self._error)
        problem = self._problem()
        return Status(self.id, problem is None, problem)

    # loading and inference

    def _load(self) -> Backend:
        with self._lock:
            if self._backend is not None:
                return self._backend
            if self._error:
                raise RuntimeError(self._error)
            if problem := self._problem():
                raise RuntimeError(problem)
            t0 = time.perf_counter()
            try:
                if self._loader is not None:
                    backend = self._loader()
                else:
                    assert self.model  # ruff: ignore[assert] (checked by _problem)
                    folder = Path(self.model).expanduser()
                    onnx = folder.is_dir() and (
                        (folder / "model.onnx").is_file()
                        or (folder / "onnx" / "model.onnx").is_file()
                    )
                    backend = (
                        OnnxBackend(folder, self.threads)
                        if onnx
                        else TransformersBackend(
                            str(folder) if folder.is_dir() else self.model, self.threads
                        )
                    )
            except Exception as e:
                self._error = f"the model could not be loaded: {type(e).__name__}: {str(e)[:160]}"
                log.warning("prompt_guard_not_loaded", extra={"error": type(e).__name__})
                raise RuntimeError(self._error) from e
            self._backend = backend
            log.info(
                "prompt_guard_loaded",
                extra={"backend": backend.name, "ms": round((time.perf_counter() - t0) * 1000)},
            )
            return backend

    def _score(self, text: str, deadline: float | None = None) -> tuple[list[float], int]:
        if _late(deadline):
            return [], 1  # waited in the queue past its time: no work
        return self._load().scores(text, deadline)

    async def classify(self, text: str, *, source: Source, budget: int) -> Result:
        """Score a text (at most ``budget`` characters of it) on the inference thread.

        A missing model or a failed load raises ``RuntimeError`` (the caller fails open).

        Returns:
            The highest malicious probability over its windows.
        """
        del source  # the same model and threshold for the person's text and untrusted content
        sent = text[: max(budget, 0)]
        result = Result(capped=len(sent) < len(text))
        if not sent:
            return result
        t0 = time.perf_counter()
        loop = asyncio.get_running_loop()
        deadline = time.monotonic() + self.timeout
        future = loop.run_in_executor(self._pool, self._score, sent, deadline)
        result.chars = len(sent)
        try:
            # The deadline stops between windows; this bounds a window already running.
            scores, total = await asyncio.wait_for(future, self.timeout + 1.0)
        except TimeoutError:
            result.error = "timeout"
            return result
        result.score = max(scores, default=0.0)
        if len(scores) < total:
            result.error = "timeout"  # what was scored still counts
        result.latency_ms = round((time.perf_counter() - t0) * 1000)
        return result

    async def warm_up(self) -> None:
        """Load the model and run it once, when one is configured (at the service's start).

        A failed load raises ``RuntimeError``; the status then says why.
        """
        if self._loader is None and not self.model:
            return
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(self._pool, self._score, "warm up", None)

    def close(self) -> None:
        """Stop the inference thread."""
        self._pool.shutdown(wait=False, cancel_futures=True)
