"""Inline markup in answers: cues for the application, speech tags for the voice.

An application (and an agent, overriding it) declares two kinds of markup the model may write in
its answers:

- **cues** (``{wave}``, ``{point:left}``): taken out of what is spoken and shown as subtitles, and
  delivered on the ``say`` segment as ``marks`` (``{name, arg?, at}``, ``at`` being the character
  offset in the segment's ``text`` where the cue sat), so a page can raise a gesture, a highlight
  or a sound exactly where the words are. An optional list of names (static, or a parameter of a
  session's prompt module) keeps only the known ones; the others are dropped and counted.
- **speech tags** (``[laughs]``): kept for the speech engines that perform them (the ``say``
  segment's ``tagged`` form, written ``[words]`` whatever the configured delimiters) and taken out
  everywhere else.

The answer's ``text`` events and the stored messages keep the markup as the model wrote it, so it
sees its own cues on the next turn. Markup in a user's message is neutralised before the model
sees it (:meth:`Markup.neutralise`), so a person cannot write cues for the agent.
"""

from __future__ import annotations

import itertools
import logging
import re
from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from evokai.audio.speech.numbers import spell_numbers
from evokai.audio.speech.speakable import speakable
from evokai.i18n import SpeechRules

log = logging.getLogger("evokai.markup")

NAME = r"[A-Za-z][A-Za-z0-9_-]{0,39}"
"""A cue's name (ASCII, as the clients read it)."""
WORDS = r"[^\W\d_][\w' -]{1,39}"
"""A speech tag's words (two characters at least: ``[x]`` is a task list)."""
MAX_TOKENS = 200
"""Markup tokens read in one segment; more are removed without being delivered."""
_SENTINEL = 0xE000
"""Private-use characters stand for the tokens while the text is made speakable."""
_PUNCT = set(".,;:!?…)]»\"'")
_NUMBER_SEPS = set(".,:/\u00a0\u202f")
_NUMBER_UNITS = set("%°ºª€")


def _delimiter(value: str) -> str:
    if not 1 <= len(value) <= 3 or not all(
        c.isascii() and not c.isalnum() and c not in "_ \t\n\r" for c in value
    ):
        raise ValueError("a delimiter is 1 to 3 ASCII punctuation characters")
    return value


class ModuleParam(BaseModel):
    """A parameter of a session's prompt module (a list of strings, or one string)."""

    model_config = ConfigDict(extra="forbid")
    module: str = Field(min_length=1, max_length=80)
    param: str = Field(min_length=1, max_length=80)


class CueDef(BaseModel):
    """Cues: ``{name}`` or ``{name:arg}`` with the default delimiters."""

    model_config = ConfigDict(extra="forbid")
    enabled: bool = True
    open: str = "{"
    close: str = "}"
    names: list[str] | None = Field(default=None, max_length=200)
    """The names delivered; others are dropped (None = every name)."""
    names_from: ModuleParam | None = None
    """A session's module parameter that lists the names (it narrows ``names`` when both)."""

    @field_validator("open", "close")
    @classmethod
    def _delims(cls, v: str) -> str:
        return _delimiter(v)

    @field_validator("names")
    @classmethod
    def _names(cls, v: list[str] | None) -> list[str] | None:
        if v is not None and any(not re.fullmatch(NAME, n) for n in v):
            raise ValueError(f"a cue name matches {NAME}")
        return v


class SpeechTagDef(BaseModel):
    """Speech tags: ``[laughs]`` with the default delimiters."""

    model_config = ConfigDict(extra="forbid")
    enabled: bool = True
    open: str = "["
    close: str = "]"
    names: list[str] | None = Field(default=None, max_length=200)
    """The tags kept for the voice; others are removed (None = every tag)."""

    @field_validator("open", "close")
    @classmethod
    def _delims(cls, v: str) -> str:
        return _delimiter(v)

    @field_validator("names")
    @classmethod
    def _names(cls, v: list[str] | None) -> list[str] | None:
        if v is not None and any(not re.fullmatch(WORDS, n) for n in v):
            raise ValueError(
                "a speech tag is a letter then letters, spaces, hyphens or apostrophes"
            )
        return v


class MarkupDef(BaseModel):
    """The markup an application or an agent declares (``markup:`` in YAML)."""

    model_config = ConfigDict(extra="forbid")
    cue: CueDef | None = None
    speech: SpeechTagDef | None = None

    @model_validator(mode="after")
    def _distinct(self) -> MarkupDef:
        if self.cue and self.speech and self.cue.open == self.speech.open:
            raise ValueError("cues and speech tags need different opening delimiters")
        return self


@dataclass
class SayForms:
    """One spoken segment in its forms."""

    text: str
    """What is shown (subtitles): no markup."""
    spoken: str
    """What the voice says: numbers in words, no markup."""
    tagged: str | None
    """``spoken`` with its speech tags (``[laughs]``), when it has any."""
    marks: list[dict[str, Any]] = field(default_factory=list)
    """The cues: ``{name, arg?, at, spokenAt?}``."""
    dropped: int = 0
    """Cues and tags left out because their name is not allowed."""


@dataclass(frozen=True)
class Markup:
    """The markup in effect for one turn."""

    pattern: re.Pattern[str]
    cue_prefix: re.Pattern[str] | None = None
    """What may follow an opening delimiter while a cue is still being written."""
    speech_prefix: re.Pattern[str] | None = None
    """What may follow an opening delimiter while a speech tag is still being written."""
    cue_names: frozenset[str] | None = None
    speech_names: frozenset[str] | None = None
    view: dict[str, Any] = field(default_factory=dict)
    """The delimiters, as clients need them to hide markup in what they show."""

    def clean(self, text: str) -> str:
        """The text without any markup (cues and tags, known or not).

        Returns:
            The text.
        """
        return self.pattern.sub("", text)

    def has_markup(self, text: str) -> bool:
        """Whether the text holds markup.

        Returns:
            True when it does.
        """
        return self.pattern.search(text) is not None

    def inside(self, text: str) -> bool:
        """Whether the text ends inside a token that is not closed yet (a cut must wait).

        Returns:
            True when an opening delimiter is not closed.
        """
        for kind, prefix in (("cue", self.cue_prefix), ("speech", self.speech_prefix)):
            d = self.view.get(kind)
            if not d or prefix is None:
                continue
            at = text.rfind(d["open"])
            if at > text.rfind(d["close"]) and prefix.fullmatch(text[at + len(d["open"]) :]):
                return True
        return False

    def neutralise(self, text: str) -> str:
        """Markup in a person's message, made inert: its opening character becomes fullwidth.

        Returns:
            The text (``{wave}`` becomes ``\uff5bwave}``, a fullwidth brace).
        """
        return self.pattern.sub(lambda m: chr(ord(m.group(0)[0]) + 0xFEE0) + m.group(0)[1:], text)

    def forms(self, piece: str, rules: SpeechRules) -> SayForms:
        """A segment of the answer as it is shown, spoken and tagged, with its cues.

        Args:
            piece: A sentence of the answer as the model wrote it (Markdown, markup).
            rules: The language's speech rules.

        Returns:
            The forms.
        """
        tokens: list[re.Match[str]] = []

        def hold(m: re.Match[str]) -> str:
            if len(tokens) >= MAX_TOKENS:
                return ""
            tokens.append(m)
            return chr(_SENTINEL + len(tokens) - 1)

        text, at = _place(speakable(self.pattern.sub(hold, piece), rules), len(tokens))
        marks: list[dict[str, Any]] = []
        tags: list[tuple[int, str]] = []
        dropped = 0
        for i, m in enumerate(tokens):
            if i not in at:
                continue
            groups = m.groupdict()
            if groups.get("name"):
                name = m.group("name")
                if self.cue_names is not None and name not in self.cue_names:
                    dropped += 1
                    continue
                mark: dict[str, Any] = {"name": name, "at": at[i]}
                if groups.get("arg"):
                    mark["arg"] = m.group("arg").strip()
                marks.append(mark)
            else:
                words = " ".join(m.group("words").split())
                if self.speech_names is not None and words.lower() not in self.speech_names:
                    dropped += 1
                    continue
                tags.append((at[i], words))
        spoken, moved = _spell(text, [m["at"] for m in marks] + [t[0] for t in tags], rules.locale)
        if spoken != text:
            for m in marks:
                m["spokenAt"] = moved[m["at"]]
        tagged = _tag(spoken, [(moved[a] if spoken != text else a, w) for a, w in tags])
        return SayForms(text, spoken, tagged, marks, dropped)


def _place(text: str, count: int) -> tuple[str, dict[int, int]]:
    """Remove the sentinels, tidy the spaces they leave, and say where each one was.

    Returns:
        The text and, for each token still there, its offset.
    """
    out: list[str] = []
    at: dict[int, int] = {}
    for idx, ch in enumerate(text):
        code = ord(ch) - _SENTINEL
        if 0 <= code < count:
            rest = text[idx + 1 :]
            nxt = next((c for c in rest if not 0 <= ord(c) - _SENTINEL < count), "")
            if out and out[-1] == " " and (nxt in ("", " ") or nxt in _PUNCT):
                out.pop()
            at[code] = len(out)
            continue
        if ch == " " and (not out or out[-1] == " "):
            continue  # a space at the start, or doubled where a token was
        out.append(ch)
    while out and out[-1] == " ":
        out.pop()
    s = "".join(out)
    return s, {i: min(a, len(s)) for i, a in at.items()}


def _spell(text: str, cuts: list[int], locale: str) -> tuple[str, dict[int, int]]:
    """Numbers in words, chunk by chunk between the markup, so its offsets move exactly.

    Returns:
        The spoken text and each offset of ``text`` among ``cuts`` in it.
    """
    snapped = {c: _after_number(text, c) for c in cuts}
    points = sorted({0, len(text), *snapped.values()})
    spoken: list[str] = []
    at: dict[int, int] = {}
    size = 0
    for a, b in itertools.pairwise(points):
        at[a] = size
        chunk = spell_numbers(text[a:b], locale)
        spoken.append(chunk)
        size += len(chunk)
    at[len(text)] = size
    return "".join(spoken), {c: at[p] for c, p in snapped.items()}


def _after_number(text: str, p: int) -> int:
    """A cut moved past the number it would split («5{x}%» keeps «5%» whole for the speller).

    Returns:
        The offset.
    """
    while 0 < p < len(text):
        before, here = text[p - 1], text[p]
        nxt = text[p + 1] if p + 1 < len(text) else ""
        if not (before.isdigit() or before in _NUMBER_SEPS):
            break
        if here.isdigit() or here in _NUMBER_UNITS:
            p += 1
        elif here in _NUMBER_SEPS and nxt.isdigit():
            p += 1
        elif here == " " and before.isdigit() and nxt in _NUMBER_UNITS:
            p += 2
        else:
            break
    return p


def _tag(spoken: str, tags: list[tuple[int, str]]) -> str | None:
    if not tags:
        return None
    out = spoken
    # Right to left, so offsets hold; tags at one offset keep the order they were written in.
    order = sorted(enumerate(tags), key=lambda t: (t[1][0], t[0]), reverse=True)
    for _, (at, words) in order:
        left, right = out[:at], out[at:]
        pad_l = " " if left and not left.endswith(" ") else ""
        pad_r = " " if right and not right.startswith((" ", *_PUNCT)) else ""
        out = f"{left}{pad_l}[{words}]{pad_r}{right}"
    return out


def _compile(cue: CueDef | None, speech: SpeechTagDef | None) -> re.Pattern[str]:
    parts = []
    if cue:
        o, c = re.escape(cue.open), re.escape(cue.close)
        stop = re.escape(cue.close[0])
        parts.append(rf"{o}(?P<name>{NAME})(?::(?P<arg>[^{stop}\n]{{0,80}}))?{c}")
    if speech:
        o, c = re.escape(speech.open), re.escape(speech.close)
        parts.append(rf"{o}(?P<words>{WORDS}){c}(?!\()")
    return re.compile("|".join(parts))


def _prefixes(cue: CueDef | None, speech: SpeechTagDef | None) -> tuple[Any, Any]:
    cue_p = speech_p = None
    if cue:
        stop = re.escape(cue.close[0])
        cue_p = re.compile(rf"(?:[A-Za-z][A-Za-z0-9_-]{{0,39}}(?::[^{stop}\n]{{0,80}})?)?")
    if speech:
        speech_p = re.compile(r"(?:[^\W\d_][\w' -]{0,39})?")
    return cue_p, speech_p


def markup_of(
    app_markup: Any, agent: MarkupDef | None = None, session: Any = None
) -> Markup | None:
    """The markup of a turn: the agent's kinds over the application's.

    Args:
        app_markup: The application's ``defaults.markup`` (a dict), if any.
        agent: The agent's ``markup``, if any.
        session: The session, whose prompt modules may list the cue names.

    Returns:
        The markup, or None when no kind is on.
    """
    app: MarkupDef | None = None
    if app_markup:
        try:
            app = MarkupDef.model_validate(app_markup)
        except ValueError:
            log.warning("markup_invalid")
    cue = agent.cue if agent and agent.cue is not None else (app.cue if app else None)
    speech = agent.speech if agent and agent.speech is not None else (app.speech if app else None)
    cue = cue if cue and cue.enabled else None
    speech = speech if speech and speech.enabled else None
    if cue and speech and cue.open == speech.open:
        speech = None  # an agent's cues over an application's tags with the same delimiter
    if not (cue or speech):
        return None
    names = _cue_names(cue, session) if cue else None
    view: dict[str, Any] = {}
    if cue:
        view["cue"] = {"open": cue.open, "close": cue.close}
    if speech:
        view["speech"] = {"open": speech.open, "close": speech.close}
    cue_prefix, speech_prefix = _prefixes(cue, speech)
    return Markup(
        pattern=_compile(cue, speech),
        cue_prefix=cue_prefix,
        speech_prefix=speech_prefix,
        cue_names=names,
        speech_names=frozenset(n.lower() for n in speech.names)
        if speech and speech.names is not None
        else None,
        view=view,
    )


def _cue_names(cue: CueDef, session: Any) -> frozenset[str] | None:
    static = frozenset(cue.names) if cue.names is not None else None
    if cue.names_from is None:
        return static
    modules = (getattr(session, "prompt_options", None) or {}).get("modules") or {}
    value = ((modules.get(cue.names_from.module) or {}).get("params") or {}).get(
        cue.names_from.param
    )
    if value is None:
        # The names come from the session: without them, only the static list (if any) applies.
        return static if static is not None else frozenset()
    given = frozenset([value] if isinstance(value, str) else (str(v) for v in value))
    return given & static if static is not None else given
