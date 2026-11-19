"""Prompt injection and jailbreak heuristics, English and Italian, deterministic.

Each rule has a weight; a text is flagged when the weights of the rules it matches reach the
threshold (3 by default, so one strong signal or two weaker ones). Rules run on the normalised
text (``guard.text``) and on readable texts hidden in base64 blobs, and obfuscation adds its own
weight: a jailbreak spelled with zero-width characters or Cyrillic letters is still a jailbreak, and
the trick makes it more suspicious, not less.

A classifier model can be chosen by the policy (``evokai.guard.classifiers``, off by default): a
score of 0.8 or more adds one strong rule. :class:`Classifier` is the protocol of a third-party one.
"""

from __future__ import annotations

import importlib
import re
from dataclasses import dataclass, field
from typing import Protocol

from evokai.guard.text import Normalized, normalize


@dataclass(frozen=True)
class Rule:
    """A heuristic."""

    id: str
    weight: int
    pattern: re.Pattern[str]
    description: str


def _r(rule_id: str, weight: int, pattern: str, description: str) -> Rule:
    return Rule(rule_id, weight, re.compile(pattern, re.I), description)


_IGNORE_EN = r"\b(?:ignore|disregard|forget|override|bypass)\b"
_PRIOR_EN = r"(?:all|any|every|your|previous|prior|above|earlier|preceding|initial|original|system|developer)"
_RULES_EN = r"(?:instructions?|rules|prompts?|directions|directives|guidelines|guardrails|context|constraints|programming|policies)"
_IGNORE_IT = r"\b(?:ignora|ignorate|dimentica|dimenticate|trascura|scarta|non (?:considerare|seguire|rispettare|tenere conto d\w*))\b"
_RULES_IT = r"(?:istruzioni|indicazioni|direttive|prompt|(?:regole|vincoli|limitazioni) (?:precedenti|di sistema|iniziali|ricevute|imposte|che (?:ti|hai)))"

RULES: tuple[Rule, ...] = (
    _r(
        "ignore_instructions",
        3,
        rf"{_IGNORE_EN}\W+(?:\w+\W+){{0,3}}{_PRIOR_EN}\W+(?:\w+\W+){{0,2}}{_RULES_EN}",
        "Asks to ignore or override the instructions",
    ),
    _r(
        "ignore_instructions",
        3,
        rf"{_IGNORE_IT}\W+(?:\w+\W+){{0,4}}{_RULES_IT}",
        "Chiede di ignorare le istruzioni",
    ),
    _r(
        "reveal_prompt",
        3,
        r"\b(?:reveal|show|print|repeat|output|display|dump|leak|tell me|give me|write out|spell out|what (?:is|are|were))\b\W+(?:\w+\W+){0,4}(?:system prompt|initial prompt|hidden prompt|developer (?:message|prompt)|your (?:original |initial |full |exact |secret |system |hidden )?(?:instructions|prompt|rules|guidelines)|the (?:original|initial|full|exact|secret|system|hidden) (?:instructions|prompt)|instructions (?:you were|you've been) given|(?:text|words|everything) above)",
        "Asks for the system prompt",
    ),
    _r(
        "reveal_prompt",
        3,
        r"\b(?:mostra\w*|rivela\w*|ripeti|ripetimi|stampa\w*|dimmi|scrivi\w*|dammi|elenca\w*)\b\W+(?:\w+\W+){0,4}(?:prompt di sistema|prompt iniziale|(?:tue |le )?istruzioni (?:di sistema|iniziali|nascoste|segrete|originali|che hai ricevuto)|il tuo prompt|il prompt|testo (?:qui )?sopra)",
        "Chiede il prompt di sistema",
    ),
    _r(
        "jailbreak_persona",
        3,
        r"\b(?:do anything now|(?:you are|act as|become|enable|you're) dan|dan (?:mode|prompt|jailbreak)|developer mode (?:enabled|on)|enable developer mode|god mode|unfiltered mode|evil (?:mode|assistant)|aim mode)\b",
        "A known jailbreak persona",
    ),
    _r("jailbreak_word", 2, r"\bjailbr(?:eak|oken|eaking)\w*", "Talks about a jailbreak"),
    _r(
        "jailbreak_persona",
        3,
        r"\bmodalit[aà] (?:sviluppatore|sviluppo|dio|senza (?:filtri|limiti|restrizioni))\b",
        "Una persona di jailbreak nota",
    ),
    _r(
        "no_restrictions",
        2,
        r"\b(?:without|no|free (?:of|from)|remove (?:all|your)|disable (?:all|your)?)\W+(?:\w+\W+){0,2}(?:restrictions|filters|limitations|guidelines|censorship|safety|guardrails|rules)\b",
        "Asks to drop restrictions",
    ),
    _r(
        "no_restrictions",
        2,
        r"\bsenza (?:alcun[ao]? |nessun[ao]? )?(?:restrizion\w*|filtr\w*|limit\w*|censur\w*|regol\w*)\b",
        "Chiede di togliere le restrizioni",
    ),
    _r(
        "role_override",
        1,
        r"\b(?:you are now|from now on,? you|act as|pretend (?:to be|you are|that you)|roleplay as|role-play as|simulate being|you will now|switch to)\b",
        "Tries to change the assistant's role",
    ),
    _r(
        "role_override",
        1,
        r"\b(?:da ora in poi|d'ora in poi|adesso sei|ora sei|fingi di essere|fai finta di essere|comportati come|agisci come|interpreta il ruolo)\b",
        "Prova a cambiare il ruolo dell'assistente",
    ),
    _r(
        "fake_delimiter",
        3,
        r"(?:^|\s)(?:<\|?(?:im_start|im_end|system|endoftext|eot_id|start_header_id)\|?>|\[/?inst\]|<</?sys>>|</?system>|###\s*(?:system|instruction)s?\s*:?|begin (?:system|new) (?:prompt|instructions)|end of (?:system )?prompt)",
        "Fake chat-template or system delimiters",
    ),
    _r(
        "fake_delimiter",
        2,
        r"(?:^|\s)(?:system|assistant|developer)\s*:\s*(?:you|ignore|from now|new)",
        "A fake system or assistant turn",
    ),
    _r(
        "new_instructions",
        3,
        r"\b(?:new|updated|real|actual|revised|additional|important|urgent) (?:system )?(?:instructions?|directives?|orders)\s*:",
        "Introduces new instructions",
    ),
    _r(
        "new_instructions",
        3,
        r"\b(?:nuove|vere|ulteriori|importanti) (?:istruzioni|direttive|regole)\s*:",
        "Introduce nuove istruzioni",
    ),
    _r(
        "context_end",
        2,
        r"\b(?:end of (?:the )?(?:document|context|email|e-mail|data|input|conversation|text)|fine (?:del (?:documento|testo|contesto)|dell'?e-?mail))\b\W{0,10}(?:\w+\W+){0,6}(?:now|instead|ignore|you must|ora|adesso|invece)",
        "Pretends the data ended to start instructions",
    ),
    _r(
        "hidden_from_user",
        2,
        r"\b(?:do not|don't|never|without) (?:tell(?:ing)?|inform(?:ing)?|mention(?:ing)?|reveal(?:ing)?|alert(?:ing)?|notify(?:ing)?|ask(?:ing)?) (?:the |this )?(?:user|human|customer|operator)",
        "Asks to hide something from the user",
    ),
    _r(
        "hidden_from_user",
        2,
        r"\b(?:non (?:dirlo|dire nulla|informare|avvisare|chiedere)|senza (?:dirlo|avvisare|informare|chiedere(?: conferma)?))\b",
        "Chiede di nascondere qualcosa all'utente",
    ),
    _r(
        "tool_coercion",
        2,
        r"\b(?:call|use|invoke|run|execute|trigger)\b\W+(?:\w+\W+){0,4}(?:tool|function|api|command)\b\W+(?:\w+\W+){0,6}(?:without|no need|don't|do not) (?:asking|confirm\w*|permission|approval)",
        "Pushes a tool call without confirmation",
    ),
    _r(
        "exfiltration",
        3,
        r"!\[[^\]]*\]\(\s*https?://[^)\s]*(?:\{|%7b|\$\{|<)",
        "A markdown image whose URL carries data",
    ),
    _r(
        "exfiltration",
        2,
        r"\b(?:send|forward|post|upload|email|e-mail|transmit|exfiltrate|leak|copy)\b\W+(?:\w+\W+){0,6}(?:to|at|via)\W+(?:https?://|[\w.+-]+@[\w-]+\.|webhook|this url|this address)",
        "Asks to send data somewhere",
    ),
    _r(
        "exfiltration",
        2,
        r"\b(?:invia|inoltra|manda|spedisci|carica)\b\W+(?:\w+\W+){0,6}(?:a|all'?|su|tramite)\W+(?:https?://|[\w.+-]+@[\w-]+\.|webhook|questo indirizzo|questo url)",
        "Chiede di inviare dati altrove",
    ),
    _r(
        "append_to_url",
        2,
        r"\b(?:append|add|include|encode|put)\b\W+(?:\w+\W+){0,6}(?:to|in|into) (?:the |this |a )?(?:url|link|query string|image url)",
        "Asks to put data in a URL",
    ),
    _r(
        "decode_and_follow",
        3,
        r"\b(?:decode|base64|rot13|hex|reverse)\b\W+(?:\w+\W+){0,6}(?:and|then)\W+(?:\w+\W+){0,3}(?:follow|execute|run|obey|do what)",
        "Asks to decode hidden instructions and follow them",
    ),
    _r(
        "prompt_leak_trick",
        2,
        r"\b(?:repeat|print|output) (?:the )?(?:words|text) (?:above|before)|starting with \"?you are\b",
        "A known prompt-leak trick",
    ),
    _r(
        "prompt_leak_trick",
        3,
        r"\b(?:print|repeat|output|show|reveal|spell-check and print)\b\W+(?:\w+\W+){0,3}(?:above|previous|prior|preceding|initial) (?:prompt|instructions|message|text)",
        "Asks to print the prompt above",
    ),
    _r(
        "context_end",
        2,
        r"(?:={3,}|-{3,}|#{3,})\s*(?:end|fine)\b",
        "Fake end-of-data separator",
    ),
)
"""The heuristics."""


@dataclass
class Verdict:
    """The rules a text matched and its score."""

    score: int = 0
    rules: list[str] = field(default_factory=list)
    obfuscation: list[str] = field(default_factory=list)
    """``zero_width``, ``homoglyphs``, ``encoded`` when the text hid something."""
    snippet: str = ""
    """The normalised text around the first match (for the event, redacted later)."""
    classifier: float | None = None
    """The classifier's score, when one was asked (its 3 points are in ``score`` at 0.8 or more)."""

    def flagged(self, threshold: int) -> bool:
        """Whether the score reaches the threshold.

        Returns:
            True when flagged.
        """
        return self.score >= threshold


def _match(text: str, verdict: Verdict, seen: set[str]) -> None:
    for rule in RULES:
        m = rule.pattern.search(text)
        if m and rule.id not in seen:
            seen.add(rule.id)
            verdict.score += rule.weight
            verdict.rules.append(rule.id)
            if not verdict.snippet:
                verdict.snippet = text[max(0, m.start() - 40) : m.end() + 40]


def scan(text: str, norm: Normalized | None = None) -> Verdict:
    """Score a text against the rules.

    Args:
        text: The raw text.
        norm: Its normalisation, when already computed.

    Returns:
        The verdict.
    """
    n = norm or normalize(text)
    verdict = Verdict()
    seen: set[str] = set()
    _match(n.text, verdict, seen)
    hidden_hits = 0
    for hidden in n.decoded:
        before = verdict.score
        _match(hidden, verdict, seen)
        hidden_hits += verdict.score > before
    if n.invisible >= 3 or (n.invisible and verdict.score):
        verdict.obfuscation.append("zero_width")
    if n.mixed_scripts:
        verdict.obfuscation.append("homoglyphs")
    if hidden_hits:
        verdict.obfuscation.append("encoded")
    if verdict.score and verdict.obfuscation:
        # Hiding an instruction makes it more suspicious, not less.
        verdict.score += 1
    return verdict


class Classifier(Protocol):
    """A prompt-injection classifier model (optional, off by default)."""

    async def score(self, text: str) -> float:
        """The probability that the text is an injection.

        Returns:
            A value between 0 and 1.
        """
        ...


def load_classifier(spec: str) -> Classifier:
    """Load a classifier from ``module:factory`` (``EVOKAI_GUARD_CLASSIFIER``).

    Returns:
        The classifier the factory builds.

    Raises:
        ValueError: A malformed spec.
    """
    module, _, factory = spec.partition(":")
    if not module or not factory:
        raise ValueError("EVOKAI_GUARD_CLASSIFIER must be module:factory")
    return getattr(importlib.import_module(module), factory)()
