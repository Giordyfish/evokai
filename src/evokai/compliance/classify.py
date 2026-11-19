"""The AI Act risk classification of an application, from its questionnaire and its configuration.

Regulation (EU) 2024/1689, in the order a reviewer applies it:

1. **Article 5**: any prohibited practice makes the system prohibited; nothing else matters.
2. **Article 6(1)**: a safety component of a product under the Annex I legislation is high-risk.
3. **Article 6(2) and Annex III**: a purpose in one of the eight areas is high-risk, unless an
   Article 6(3) condition holds and the system does not profile natural persons (profiling is
   always high-risk). A system that relies on an exception stays documented (Article 6(4)) and
   registered (Article 49(2)).
4. **Article 50**: whatever the risk level, a system that interacts with natural persons must tell
   them (50(1)); one that generates synthetic audio, images, video or text marks it (50(2));
   emotion recognition and biometric categorisation inform the people exposed (50(3)); deep fakes
   and text published on matters of public interest are disclosed (50(4)).
5. **Article 4** (AI literacy) applies to every provider and deployer.

What the configuration answers by itself: the system interacts with natural persons when an
agent serves browser sessions or voice; it generates synthetic audio with server voice, images
with ``code_run`` charts, and text always. Everything else comes from the questionnaire. The
result is language-neutral: keys and parameters the packs render in either language, each reason
with the sources it rests on.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

PROHIBITED = "prohibited"
HIGH_RISK = "high_risk"
EXEMPTED = "annex_iii_exempted"
LIMITED = "limited_risk"
MINIMAL = "minimal_risk"
CLASSES = (PROHIBITED, HIGH_RISK, EXEMPTED, LIMITED, MINIMAL)


@dataclass
class Point:
    """A reason or an obligation: a text key, its parameters and its sources."""

    key: str
    sources: list[str]
    params: dict[str, Any] = field(default_factory=dict)
    support: str | None = None
    """What Evokai does towards it (a text key), for obligations."""
    support_params: dict[str, Any] = field(default_factory=dict)
    warn: str | None = None
    """A gap the configuration shows (a text key)."""
    warn_params: dict[str, Any] = field(default_factory=dict)


@dataclass
class Classification:
    """The outcome."""

    level: str
    reasons: list[Point]
    obligations: list[Point]
    interacts: bool
    synthetic: list[str]


def _log_days(facts: dict[str, Any]) -> int | None:
    """The shortest retention of the logs a reviewer relies on (traces, audit), in days.

    Returns:
        Days, or None when both are kept until deleted.
    """
    app, dep = facts["application"], facts["deployment"]
    values = [
        app.get("conversationTtlDays"),
        app.get("auditTtlDays") or dep.get("auditTtlDays"),
    ]
    known = [int(v) for v in values if v]
    return min(known) if known else None


def classify(facts: dict[str, Any]) -> Classification:
    """Classify an application.

    Returns:
        The level, the reasons and the obligations that follow.
    """
    q = (facts.get("profile") or {}).get("ai_act") or {}
    agents = facts.get("agents") or []
    grants = facts.get("grants") or {}
    browser = [a["name"] for a in agents if "browser" in a.get("channels", [])]
    if not grants.get("browser", True):
        browser = []
    browser += sorted({
        c["agent"]
        for c in facts.get("channels") or []
        if c.get("enabled") and c["agent"] not in browser
    })
    voice = bool(grants.get("voiceServer"))
    interacts = bool(browser)
    synthetic = ["text"]
    if voice:
        synthetic.append("audio")
    if grants.get("code") and any(a.get("code") for a in agents):
        synthetic.append("image")
    reasons: list[Point] = []
    obligations: list[Point] = []
    qs = ["profile.ai_act"]

    prohibited = list(q.get("prohibited") or [])
    annex = list(q.get("annex_iii") or [])
    exceptions = list(q.get("exceptions") or [])
    profiling = bool(q.get("profiling"))

    if prohibited:
        level = PROHIBITED
        reasons.append(Point("r.prohibited", qs, {"practices": prohibited}))
    elif q.get("safety_component"):
        level = HIGH_RISK
        reasons.append(Point("r.safetyComponent", qs))
    elif annex and (profiling or not exceptions):
        level = HIGH_RISK
        reasons.append(Point("r.annex", qs, {"areas": annex}))
        reasons.append(Point("r.profiling", qs) if profiling else Point("r.noException", qs))
    elif annex:
        level = EXEMPTED
        reasons.append(Point("r.annex", qs, {"areas": annex}))
        reasons.append(Point("r.exception", qs, {"conditions": exceptions}))
    else:
        level = (
            LIMITED if interacts or q.get("emotion_recognition") or q.get("deep_fake") else MINIMAL
        )
        reasons.append(Point("r.noAnnex", qs))

    if interacts:
        reasons.append(
            Point(
                "r.interacts",
                ["agent.channels", "grant.channel", "table.channels"],
                {"agents": browser},
            )
        )
    if level in (LIMITED, MINIMAL) and not interacts:
        reasons.append(Point("r.noInteraction", ["agent.channels", "grant.channel"]))

    # Obligations.
    if level == PROHIBITED:
        obligations.append(Point("o.stop", qs))
        return Classification(level, reasons, obligations, interacts, synthetic)

    if level == HIGH_RISK:
        days = _log_days(facts)
        obligations += [
            Point("o.art9", qs, support="s.art9", warn=None),
            Point("o.art10", qs, support="s.art10"),
            Point("o.art11", qs, support="s.art11"),
            Point(
                "o.art12",
                ["app.conversation_ttl_days", "app.audit_ttl_days", "table.traces"],
                support="s.art12",
                warn="w.logs6m" if days is not None and days < 183 else None,
                warn_params={"days": days},
            ),
            Point("o.art13", qs, support="s.art13"),
            Point(
                "o.art14",
                ["mcp.tools", "workflow.approvals", "app.redteam_gate"],
                support="s.art14",
            ),
            Point(
                "o.art15",
                ["table.redteam", "table.evals", "app.security"],
                support="s.art15",
            ),
            Point("o.art17", qs),
            Point("o.art26", qs, support="s.art26"),
            Point("o.art27", qs),
            Point("o.art43", qs),
            Point("o.art49", qs),
            Point("o.art72", ["table.security_events", "table.traces"], support="s.art72"),
            Point("o.art73", qs, support="s.art73"),
        ]
    if level == EXEMPTED:
        obligations += [Point("o.art6_4", qs), Point("o.art49_2", qs)]

    if interacts:
        shown = q.get("disclosure_shown")
        obligations.append(
            Point(
                "o.art50_1",
                ["agent.channels", "profile.ai_act"],
                support="s.art50_1",
                warn="w.disclosure" if shown is not True else None,
            )
        )
    obligations.append(
        Point(
            "o.art50_2",
            ["agent.models", "grant.voice", "grant.code"],
            {"kinds": synthetic},
            support="s.art50_2",
        )
    )
    if q.get("emotion_recognition") or q.get("biometric_categorisation"):
        obligations.append(Point("o.art50_3", qs))
    if q.get("deep_fake") or q.get("public_interest_text"):
        obligations.append(Point("o.art50_4", qs))
    obligations.append(Point("o.art4", qs, support="s.art4"))
    if level in (LIMITED, MINIMAL):
        obligations.append(Point("o.art95", qs))
    obligations.append(Point("o.gpai", ["agent.models"], support="s.gpai"))
    return Classification(level, reasons, obligations, interacts, synthetic)
