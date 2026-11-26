"""The three packs, written from the facts: AI Act, GDPR and the control mapping.

Each builder is a pure function of the facts (``evokai.compliance.facts``) and a language: the
same snapshot renders the same way every time. Every statement names its sources; what only the
controller can state is marked ``human`` and left for them to complete.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from typing import Any

from evokai.compliance import classify as cls
from evokai.compliance.model import Block, Item, Pack
from evokai.compliance.texts import (
    CONTROL_MAP,
    ISO42001_MAP,
    ISO_CONTROLS,
    NIST,
    SOC2_CONTROLS,
    Tx,
)
from evokai.guard.pii import PII_TYPES

PACKS = ("ai-act", "gdpr", "controls")
GENERATOR = "evokai-compliance/1"

ORGANISATION = "organisation"
SUPPORTED = "supported"
AVAILABLE = "available"
PARTIAL = "partial"


def _p(bid: str, text: str, sources: list[str], *, human: bool = False) -> Block:
    return Block(bid, "p", text=text, sources=sources, human=human)


def _list(bid: str, items: Iterable[Item], *, title: str = "", sources: list[str] | None = None):
    return Block(bid, "list", title=title, items=list(items), sources=sources or [])


def _table(bid: str, columns: list[str], rows: Iterable[Item], *, title: str = "") -> Block:
    return Block(bid, "table", title=title, columns=columns, items=list(rows))


def _fields(bid: str, rows: Iterable[Item], *, title: str = "") -> Block:
    return Block(bid, "fields", title=title, items=list(rows))


def _new(pack: str, facts: dict[str, Any], tx: Tx, snapshot: dict[str, Any] | None) -> Pack:
    app = facts["application"]
    prof = facts.get("profile") or {}
    meta = [
        (tx("meta.system"), prof.get("system_name") or app["name"]),
        (tx("meta.application"), app["slug"]),
        (tx("meta.generated"), tx.date(facts["collectedAt"])),
    ]
    if snapshot:
        meta.append((tx("meta.snapshot"), f"{snapshot['id']} ({tx.date(snapshot['createdAt'])})"))
    if facts["deployment"].get("build"):
        meta.append((tx("meta.build"), str(facts["deployment"]["build"])[:12]))
    meta.append((tx("meta.generator"), GENERATOR))
    return Pack(
        pack=pack,
        lang=tx.lang,
        title=tx(f"pack.{pack}"),
        subtitle=tx(f"pack.{pack}.sub"),
        disclaimer=tx("disclaimer"),
        meta=meta,
        source_labels=tx.sources(),
    )


def _todo(tx: Tx, value: str) -> tuple[str, str | None]:
    return (value, None) if value.strip() else (tx("todo"), "human")


def _agents_using(agents: list[dict[str, Any]], pred: Callable[[dict[str, Any]], bool]) -> list:
    return [a["name"] for a in agents if pred(a)]


def _mode_summary(facts: dict[str, Any], control: str) -> tuple[str, list[str]]:
    """The modes a control has across the agents, with the agents that differ.

    Returns:
        (the most common mode, agents with another mode).
    """
    agents = facts["agents"]
    if not agents:
        default = next(c["default"] for c in facts["security"]["controls"] if c["id"] == control)
        return default, []
    modes = [a["security"].get(control, "off") for a in agents]
    top = max(set(modes), key=modes.count)
    return top, [a["name"] for a in agents if a["security"].get(control) != top]


# AI Act


def ai_act(facts: dict[str, Any], tx: Tx, snapshot: dict[str, Any] | None = None) -> Pack:
    """The AI Act pack.

    Returns:
        The pack.
    """
    pk = _new("ai-act", facts, tx, snapshot)
    app, prof = facts["application"], facts.get("profile") or {}
    q = prof.get("ai_act") or {}
    agents = facts["agents"]

    s = pk.section("system", tx("aia.system"))
    desc, desc_flag = _todo(tx, prof.get("system_description", ""))
    purpose, purpose_flag = _todo(tx, prof.get("intended_purpose", ""))
    provider, provider_flag = _todo(tx, prof.get("controller", ""))
    s.add(
        _fields(
            "system.fields",
            [
                Item(
                    "name",
                    cells=[tx("f.name"), prof.get("system_name") or app["name"]],
                    sources=["profile.system", "app.name"],
                ),
                Item(
                    "description",
                    cells=[tx("f.description"), desc],
                    sources=["profile.system"],
                    flag=desc_flag,
                ),
                Item(
                    "purpose",
                    cells=[tx("f.purpose"), purpose],
                    sources=["profile.system"],
                    flag=purpose_flag,
                ),
                Item(
                    "organisation",
                    cells=[tx("f.organisation"), provider],
                    sources=["profile.system"],
                    flag=provider_flag,
                ),
                Item(
                    "role",
                    cells=[tx("f.role"), tx(f"role.{q.get('role') or 'provider_deployer'}")],
                    sources=["profile.ai_act"],
                ),
                Item(
                    "agents",
                    cells=[tx("f.agents"), tx.join(a["name"] for a in agents) or "-"],
                    sources=["table.agents"],
                ),
            ],
        )
    )

    c = cls.classify(facts)
    s = pk.section("classification", tx("aia.classification"))
    s.add(
        _p(
            "classification.level",
            tx("aia.level", level=tx(f"level.{c.level}")),
            ["profile.ai_act", "agent.channels"],
        )
    )
    s.add(
        _list(
            "classification.reasons",
            (
                Item(f"r{i}", tx(r.key, **tx.params(r.params)), sources=r.sources)
                for i, r in enumerate(c.reasons)
            ),
            title=tx("aia.reasons"),
        )
    )
    rows = []
    for o in c.obligations:
        support = tx(o.support, **tx.params(o.support_params)) if o.support else tx("support.none")
        gap = tx(o.warn, **tx.params(o.warn_params)) if o.warn else ""
        rows.append(
            Item(
                o.key,
                cells=[tx(f"{o.key}.ref"), tx(o.key, **tx.params(o.params)), support, gap or "-"],
                sources=o.sources,
                flag="warn" if gap else None,
            )
        )
    s.add(
        _table(
            "classification.obligations",
            [tx("col.article"), tx("col.obligation"), tx("col.support"), tx("col.gap")],
            rows,
            title=tx("aia.obligations"),
        )
    )
    s.add(_p("classification.timeline", tx("aia.timeline"), ["law.timeline"]))
    if not q:
        s.add(_p("classification.unanswered", tx("aia.unanswered"), ["profile.ai_act"], human=True))

    # Technical documentation in the spirit of Annex IV.
    s = pk.section("technical", tx("aia.technical"))
    s.add(
        _table(
            "technical.agents",
            [
                tx("col.agent"),
                tx("col.version"),
                tx("col.saved"),
                tx("col.channels"),
                tx("col.capabilities"),
                tx("col.versions"),
            ],
            (
                Item(
                    a["name"],
                    cells=[
                        a["name"] + (f" ({a['title']})" if a.get("title") else ""),
                        a["version"],
                        tx.date(a["savedAt"]),
                        tx.join(tx(f"channel.{ch}") for ch in a["channels"])
                        + (f", {tx('channel.a2a')}" if a.get("a2a") else ""),
                        tx.join(_capabilities(a, tx)) or "-",
                        str(a["versions"]),
                    ],
                    sources=["table.agents", "agent.channels"],
                )
                for a in agents
            ),
            title=tx("aia.agents"),
        )
    )
    s.add(
        _table(
            "technical.models",
            [
                tx("col.model"),
                tx("col.provider"),
                tx("col.where"),
                tx("col.role"),
                tx("col.usedBy"),
            ],
            (
                Item(
                    m["id"],
                    cells=[
                        m["id"],
                        m["provider"],
                        _where(m, tx),
                        tx.join(tx(f"mrole.{r}") for r in m["roles"]),
                        tx.join(m["usedBy"]),
                    ],
                    sources=["agent.models", "env.aws_region", "env.model_allow"],
                    flag="warn"
                    if m.get("geography") not in ("eu", None)
                    or m["provider"] in ("openai", "mistral", "anthropic", "azure")
                    else None,
                )
                for m in facts["models"]
            ),
            title=tx("aia.models"),
        )
    )
    s.add(_list("technical.services", _services(facts, tx), title=tx("aia.services")))
    s.add(
        _table(
            "technical.tools",
            [
                tx("col.server"),
                tx("col.reach"),
                tx("col.auth"),
                tx("col.tools"),
                tx("col.mutating"),
            ],
            [
                Item(
                    srv["name"],
                    cells=[
                        srv["name"],
                        tx("reach.connector", kind=srv["connector"])
                        if srv["connector"]
                        else srv["host"] or "-",
                        tx(f"auth.{srv['auth']}"),
                        str(len(srv["tools"])),
                        tx.join(t["name"] for t in srv["tools"] if t["mutating"]) or tx("none"),
                    ],
                    sources=["mcp.servers", "mcp.tools"],
                )
                for srv in facts["mcpServers"]
            ]
            + _builtin_tools(facts, tx)
            + _delegated(facts, tx),
            title=tx("aia.tools"),
        )
    )
    s.add(_list("technical.oversight", _oversight(facts, tx), title=tx("aia.oversight")))
    s.add(
        _table(
            "technical.accuracy",
            [tx("col.agent"), tx("col.redteam"), tx("col.evals"), tx("col.trend")],
            (_accuracy_row(a, tx) for a in agents),
            title=tx("aia.accuracy"),
        )
    )
    s.add(_p("technical.accuracyNote", tx("aia.accuracyNote"), ["table.redteam", "table.evals"]))
    s.add(
        _table(
            "technical.security",
            [tx("col.control"), tx("col.mode"), tx("col.owasp"), tx("col.nist"), tx("col.events")],
            (_control_row(facts, ctl, tx) for ctl in facts["security"]["controls"]),
            title=tx("aia.security"),
        )
    )
    s.add(_list("technical.logging", _logging(facts, tx), title=tx("aia.logging")))

    s = pk.section("changes", tx("aia.changes"))
    changes = facts.get("changes") or []
    if changes:
        s.add(
            _table(
                "changes.log",
                [tx("col.when"), tx("col.change"), tx("col.target"), tx("col.by")],
                (
                    Item(
                        ch.get("id") or f"{ch['at']}-{i}",
                        cells=[
                            tx.datetime(ch["at"]),
                            tx.action(ch["action"]),
                            ch["target"] or "-",
                            tx(f"actor.{ch['actorKind']}")
                            + (f" ({ch['role']})" if ch.get("role") else ""),
                        ],
                        sources=["table.audit"],
                    )
                    for i, ch in enumerate(changes)
                ),
            )
        )
    else:
        s.add(_p("changes.none", tx("aia.noChanges"), ["table.audit"]))
    s.add(_p("changes.scope", tx("aia.changesScope"), ["table.audit", "app.audit_ttl_days"]))

    s = pk.section("human", tx("aia.human"))
    todo = ["instructions", "monitoring", "incidents", "literacy", "data"]
    if c.level == cls.HIGH_RISK:
        todo += ["qms", "conformity", "fria"]
    s.add(
        _list(
            "human.todo",
            (Item(k, tx(f"aia.todo.{k}"), sources=["profile.ai_act"], flag="human") for k in todo),
        )
    )
    pk.extra["classification"] = {
        "level": c.level,
        "interacts": c.interacts,
        "synthetic": c.synthetic,
        "obligations": [o.key for o in c.obligations],
    }
    return pk


def _capabilities(a: dict[str, Any], tx: Tx) -> list[str]:
    out = []
    if a["mcpServers"]:
        out.append(tx("cap.mcp", servers=", ".join(a["mcpServers"])))
    if a["knowledgeBases"]:
        out.append(tx("cap.knowledge", bases=", ".join(a["knowledgeBases"])))
    if a["code"]:
        out.append(tx("cap.code"))
    if a["memoryEpisodes"]:
        out.append(tx("cap.episodes"))
    if a["handoffTo"]:
        out.append(tx("cap.handoff", agents=", ".join(a["handoffTo"])))
    if a["structuredOutput"]:
        out.append(tx("cap.structured"))
    return out


def _where(m: dict[str, Any], tx: Tx) -> str:
    if m["provider"] == "bedrock":
        return tx("where.bedrock", geo=(m.get("geography") or "?").upper(), region=m["region"])
    if m["provider"] in ("ollama",):
        return tx("where.selfHosted")
    if m["provider"] in ("test", "function"):
        return tx("where.test")
    return tx("where.provider")


def _services(facts: dict[str, Any], tx: Tx) -> list[Item]:
    dep, grants = facts["deployment"], facts["grants"]
    region = dep["awsRegion"]
    out = [
        Item(
            "embeddings",
            tx("svc.embeddings", model=dep["embeddingModel"], region=region),
            sources=["env.embedding_model", "env.aws_region"],
        ),
    ]
    if dep.get("guardrail"):
        out.append(
            Item(
                "guardrail",
                tx("svc.guardrail", region=dep["guardrailRegion"]),
                sources=["env.guardrail"],
            )
        )
    if grants.get("voiceServer"):
        out.append(
            Item(
                "voice",
                tx("svc.voiceServer", stt=dep["voiceStt"], tts=dep["voiceTts"]),
                sources=["env.voice", "grant.voice"],
            )
        )
    if grants.get("voiceBrowser"):
        out.append(Item("browserVoice", tx("svc.voiceBrowser"), sources=["grant.voice"]))
    code = dep.get("code") or {}
    if code.get("enabled") and grants.get("code"):
        out.append(
            Item(
                "code",
                tx("svc.code", region=code.get("region"), id=code.get("interpreter")),
                sources=["env.code", "grant.code"],
            )
        )
    else:
        out.append(Item("code", tx("svc.codeOff"), sources=["env.code", "grant.code"]))
    out.append(
        Item(
            "otel",
            tx("svc.otelOn") if dep["otelEnabled"] else tx("svc.otelOff"),
            sources=["env.otel"],
        )
    )
    return out


def _builtin_tools(facts: dict[str, Any], tx: Tx) -> list[Item]:
    agents = facts["agents"]
    rows = []
    kb = _agents_using(agents, lambda a: bool(a["knowledgeBases"]))
    if kb:
        rows.append(("knowledge_search", tx("builtin.knowledge"), kb))
    code = _agents_using(agents, lambda a: a["code"]) if facts["grants"].get("code") else []
    if code:
        rows.append(("code_run", tx("builtin.code"), code))
    scripted = set(facts.get("skillScripts") or [])
    runs = (
        _agents_using(
            agents,
            lambda a: a["code"] and (a["skills"] is None or bool(scripted & set(a["skills"]))),
        )
        if scripted and facts["grants"].get("code")
        else []
    )
    if runs:
        rows.append(("skill_run", tx("builtin.skillRun"), runs))
    ho = _agents_using(agents, lambda a: bool(a["handoffTo"]))
    if ho:
        rows.append(("handoff", tx("builtin.handoff"), ho))
    client = _agents_using(agents, lambda a: any(a["clientTools"].values()))
    if client:
        rows.append(("client", tx("builtin.client"), client))
    return [
        Item(
            f"builtin.{name}",
            cells=[name, tx("reach.builtin"), "-", desc, tx("none")],
            sources=["agent.tools"],
        )
        for name, desc, _who in rows
    ]


def _delegated(facts: dict[str, Any], tx: Tx) -> list[Item]:
    """Per-user connections and messaging channels, as rows of the tools table.

    Returns:
        Rows.
    """
    rows = [
        Item(
            f"connection.{c['provider']}",
            cells=[
                tx(f"conn.{c['provider']}"),
                tx("reach.connection"),
                tx("auth.delegated"),
                tx.join(tx(f"family.{f}") for f in c["families"]) or "-",
                tx("conn.confirm"),
            ],
            sources=["grant.connection", "table.connections"],
        )
        for c in facts.get("connections") or []
        if c["granted"]
    ]
    rows += [
        Item(
            f"channel.{c['kind']}.{c['name']}",
            cells=[
                f"{tx(f'chan.{c["kind"]}')}: {c['name']}",
                tx("reach.channel", agent=c["agent"]),
                "-",
                "-",
                "-",
            ],
            sources=["table.channels", "grant.channel"],
        )
        for c in facts.get("channels") or []
        if c["enabled"]
    ]
    return rows


def _oversight(facts: dict[str, Any], tx: Tx) -> list[Item]:
    app, grants = facts["application"], facts["grants"]
    mutating = [
        f"{s['name']}/{t['name']}"
        for s in facts["mcpServers"]
        for t in s["tools"]
        if t["mutating"] and t["granted"]
    ]
    approvals = sum(w["approvals"] for w in facts["workflows"])
    tool_steps = sum(w["toolSteps"] for w in facts["workflows"])
    top, _ = _mode_summary(facts, "tools.policy")
    out = [
        Item(
            "confirm",
            tx("ov.confirm", n=len(mutating), tools=tx.join(mutating[:12]) or tx("none")),
            sources=["mcp.tools", "code.turns"],
        ),
        Item("serverRuns", tx("ov.serverRuns"), sources=["code.runs"]),
        Item("policy", tx("ov.policy", mode=top), sources=["app.security"]),
        Item(
            "approvals",
            tx(
                "ov.approvals",
                n=approvals,
                steps=tool_steps,
                auto=tx("yes") if grants.get("autonomous") else tx("no"),
            ),
            sources=["workflow.approvals", "grant.workflow"],
            flag="warn" if grants.get("autonomous") else None,
        ),
        Item(
            "gate",
            tx("ov.gate", mode=app["redteamGate"], min=app["redteamMinScore"]),
            sources=["app.redteam_gate"],
            flag="warn" if app["redteamGate"] == "off" else None,
        ),
        Item("review", tx("ov.review"), sources=["table.review_notes", "table.traces"]),
        Item("budget", tx("ov.budget"), sources=["app.caps", "app.security"]),
    ]
    if any(a["handoffTo"] for a in facts["agents"]):
        out.append(Item("handoff", tx("ov.handoff"), sources=["agent.tools"]))
    return out


def _trend(points: list[dict[str, Any]] | None) -> str:
    vals = [p["value"] for p in points or [] if p.get("value") is not None]
    return " > ".join(f"{v:g}" for v in vals) if vals else "-"


def _accuracy_row(a: dict[str, Any], tx: Tx) -> Item:
    rt, ev = a.get("redteam"), a.get("evals")
    red = (
        tx("acc.score", value=f"{rt['value']:g}", version=rt["version"], when=tx.date(rt["at"]))
        if rt and rt.get("value") is not None
        else tx("acc.none")
    )
    evs = (
        tx("acc.pass", value=f"{ev['value']:g}", version=ev["version"], when=tx.date(ev["at"]))
        if ev and ev.get("value") is not None
        else tx("acc.none")
    )
    trend = tx(
        "acc.trend", red=_trend(rt and rt.get("trend")), evals=_trend(ev and ev.get("trend"))
    )
    flag = "warn" if not rt or rt.get("value") is None else None
    return Item(
        a["name"],
        cells=[a["name"], red, evs, trend],
        sources=["table.redteam", "table.evals"],
        flag=flag,
    )


def _control_row(facts: dict[str, Any], ctl: dict[str, Any], tx: Tx) -> Item:
    mode, differ = _mode_summary(facts, ctl["id"])
    events = (facts["activity"].get("securityEvents") or {}).get(ctl["id"]) or {}
    count = sum(events.values())
    shown = mode + (f" ({tx('mode.differ', agents=', '.join(differ))})" if differ else "")
    return Item(
        ctl["id"],
        cells=[
            f"{ctl['id']}: {tx(f'ctl.{ctl["id"]}')}",
            shown,
            ", ".join(ctl["owasp"]),
            ", ".join(NIST.get(ctl["id"], ())) or "-",
            str(count),
        ],
        sources=["app.security", "table.security_events"],
        flag="warn" if mode == "off" else None,
    )


def _retention(value: Any, tx: Tx) -> str:
    return tx("days", n=int(value)) if value else tx("kept")


def _logging(facts: dict[str, Any], tx: Tx) -> list[Item]:
    app, dep, act = facts["application"], facts["deployment"], facts["activity"]
    external = app["storageMode"] == "external"
    return [
        Item(
            "traces",
            tx(
                "log.traces",
                content="none" if external else app["tracingContent"],
                kept=_retention(app["conversationTtlDays"], tx),
                n=act["turns"] + act["serverRuns"],
                days=act["days"],
            ),
            sources=["table.traces", "app.tracing_content", "app.conversation_ttl_days"],
        ),
        Item(
            "audit",
            tx("log.audit", kept=_retention(app["auditTtlDays"] or dep["auditTtlDays"], tx)),
            sources=["table.audit", "app.audit_ttl_days"],
        ),
        Item(
            "events",
            tx(
                "log.events",
                kept=_retention(app["securityEventTtlDays"] or dep["securityEventTtlDays"], tx),
            ),
            sources=["table.security_events", "app.security_event_ttl_days"],
        ),
        Item("usage", tx("log.usage"), sources=["table.usage"]),
        Item("versions", tx("log.versions"), sources=["table.agents"]),
        Item("logs", tx("log.logs"), sources=["app.security"]),
        Item(
            "otel",
            tx("log.otelOn") if dep["otelEnabled"] else tx("log.otelOff"),
            sources=["env.otel"],
        ),
    ]


# GDPR


def _rules(profile: dict[str, Any], tx: Tx) -> list[str]:
    """A profile's rules in words.

    Returns:
        One phrase per dimension that is set.
    """
    out: list[str] = []
    res = profile.get("residency") or {}
    where = [g.upper() for g in res.get("geographies") or []] + list(res.get("countries") or [])
    if where:
        out.append(tx("dp.residency", where=", ".join(where)))
    if j := profile.get("jurisdiction"):
        who = [tx("dp.eu") if x == "eu" else x for x in j]
        out.append(tx("dp.jurisdiction", who=", ".join(who)))
    if (r := profile.get("retention")) in ("no_training", "zero_retention"):
        out.append(tx(f"dp.retention.{r}"))
    return out


def _where(f: dict[str, Any] | None, tx: Tx) -> str:
    if not f:
        return tx("dp.unknown")
    if f.get("inProcess"):
        return tx("dp.inProcess")
    parts = [
        x for x in ((f.get("geography") or "").upper(), ", ".join(f.get("countries") or [])) if x
    ]
    return " · ".join(parts) or tx("dp.unknown")


def _terms(f: dict[str, Any] | None, tx: Tx) -> str:
    if not f or f.get("inProcess"):
        return "" if f else tx("dp.unknown")
    ret = f.get("retention")
    days = tx("dp.days", n=f["retentionDays"]) if f.get("retentionDays") else ""
    kept = tx(f"dp.ret.{ret}", days=days) if ret else tx("dp.unknown")
    training = f.get("training")
    trained = (
        tx("dp.unknown") if training is None else tx(f"dp.train.{'yes' if training else 'no'}")
    )
    return f"{kept} / {trained}"


def _data_protection(facts: dict[str, Any], tx: Tx) -> list[Block]:
    """The application's data protection profile and every processor under it.

    Returns:
        The blocks: the profile, the processors with their facts and verdict, the browser's.
    """
    dp = facts.get("dataProtection")
    if not dp:
        return []
    rules = _rules(dp.get("profile") or {}, tx)
    out = [
        _p(
            "dataProtection.profile",
            tx("dp.profile", rules="; ".join(rules)) if rules else tx("dp.none"),
            ["app.data_protection", "app.residency"],
        )
    ]
    used = {c["provider"] for c in facts.get("connections") or [] if c.get("granted")}
    rows: list[Item] = []
    for x in dp["processors"]:
        if x["kind"] == "model" and not x.get("usedBy"):
            continue
        if x["kind"] == "classifier" and not x.get("configured"):
            continue
        if x["kind"] == "connection" and x["name"] not in used:
            continue
        f = x.get("facts")
        why = ", ".join(tx(f"dp.why.{r['dimension']}.{r['code']}") for r in x["reasons"])
        rows.append(
            Item(
                f"dp.{x['kind']}.{x['name']}",
                cells=[
                    f"{(f or {}).get('title') or x['name']} ({x['name']})",
                    tx(f"dp.kind.{x['kind']}"),
                    _where(f, tx),
                    (f or {}).get("controlledFrom")
                    or ("" if f and f.get("inProcess") else tx("dp.unknown")),
                    _terms(f, tx),
                    tx("dp.yes") if x["allowed"] else tx("dp.no", why=why),
                ],
                sources=["catalog.providers", "app.data_protection"],
                flag=None if x["allowed"] else "warn",
            )
        )
    out.append(
        _table(
            "dataProtection.processors",
            [
                tx("col.service"),
                tx("col.kind"),
                tx("col.where"),
                tx("col.controlledFrom"),
                tx("col.terms"),
                tx("col.allowed"),
            ],
            rows,
            title=tx("dp.title"),
        )
    )
    b = dp.get("browser") or {}
    vendors = b.get("vendors") or []
    out.append(
        _p(
            "dataProtection.browser",
            tx(
                "dp.browser",
                voices=tx("dp.on") if b.get("online_voices") else tx("dp.off"),
                recognition=tx("dp.on") if b.get("online_recognition") else tx("dp.off"),
                vendors=tx("dp.vendors", vendors=", ".join(vendors)) if vendors else "",
            ),
            ["app.data_protection"],
        )
    )
    out.append(
        _p(
            "dataProtection.sources",
            tx("dp.sources"),
            ["catalog.providers"],
        )
    )
    return out


def _subprocessors(facts: dict[str, Any], tx: Tx) -> list[Item]:
    dep, grants = facts["deployment"], facts["grants"]
    region = dep["awsRegion"]
    rows: list[Item] = []
    providers = sorted({m["provider"] for m in facts["models"]})
    for p in providers:
        if p == "bedrock":
            geos = sorted({
                (m.get("geography") or "?").upper()
                for m in facts["models"]
                if m["provider"] == "bedrock"
            })
            rows.append(
                Item(
                    "bedrock",
                    cells=[
                        "Amazon Bedrock (AWS)",
                        tx("sp.models"),
                        tx("sp.bedrockWhere", region=region, geos=", ".join(geos)),
                        tx("sp.always"),
                    ],
                    sources=["agent.models", "env.aws_region"],
                )
            )
        elif p in ("test", "function"):
            continue
        elif p == "ollama":
            rows.append(
                Item(
                    "ollama",
                    cells=["Ollama", tx("sp.models"), tx("where.selfHosted"), tx("sp.always")],
                    sources=["agent.models"],
                )
            )
        else:
            rows.append(
                Item(
                    p,
                    cells=[p, tx("sp.models"), tx("sp.unknownWhere"), tx("sp.always")],
                    sources=["agent.models", "env.model_allow"],
                    flag="warn",
                )
            )
    rows.append(
        Item(
            "embeddings",
            cells=["Amazon Bedrock (AWS)", tx("sp.embeddings"), region, tx("sp.whenKnowledge")],
            sources=["env.embedding_model"],
        )
    )
    if dep.get("guardrail"):
        rows.append(
            Item(
                "guardrail",
                cells=[
                    "Amazon Bedrock Guardrails (AWS)",
                    tx("sp.guardrail"),
                    dep["guardrailRegion"],
                    tx("sp.whenClassifier"),
                ],
                sources=["env.guardrail"],
            )
        )
    code = dep.get("code") or {}
    if code.get("enabled") and grants.get("code"):
        rows.append(
            Item(
                "code",
                cells=[
                    "Amazon Bedrock AgentCore (AWS)",
                    tx("sp.code"),
                    code.get("region") or region,
                    tx("sp.whenCode"),
                ],
                sources=["env.code", "grant.code"],
            )
        )
    if grants.get("voiceServer"):
        rows.append(
            Item(
                "voice",
                cells=[
                    f"{dep['voiceStt']} / {dep['voiceTts']}",
                    tx("sp.voice"),
                    tx("sp.providerWhere"),
                    tx("sp.whenVoice"),
                ],
                sources=["env.voice", "grant.voice"],
                flag="human",
            )
        )
    for srv in facts["mcpServers"]:
        if srv["connector"] in ("google_drive",):
            name = "Google Drive (Google)"
        elif srv["connector"] in ("microsoft_files",):
            name = "Microsoft SharePoint/OneDrive (Microsoft)"
        elif srv["connector"] == "imap":
            name = tx("sp.imap")
        else:
            continue
        rows.append(
            Item(
                f"conn.{srv['name']}",
                cells=[
                    name,
                    tx("sp.connector", name=srv["name"]),
                    tx("sp.providerWhere"),
                    tx("sp.whenTool"),
                ],
                sources=["mcp.servers"],
                flag="human",
            )
        )
    rows.extend(
        Item(
            f"connection.{c['provider']}",
            cells=[
                tx(f"conn.{c['provider']}"),
                tx("sp.connection"),
                tx("sp.providerWhere"),
                tx("sp.whenConnected", n=c["people"]),
            ],
            sources=["grant.connection", "table.connections"],
            flag="human",
        )
        for c in facts.get("connections") or []
        if c["granted"]
    )
    rows.extend(
        Item(
            f"channel.{kind}",
            cells=[
                tx(f"spchan.{kind}"),
                tx("sp.channel"),
                tx("sp.providerWhere"),
                tx("sp.always"),
            ],
            sources=["table.channels"],
            flag="human",
        )
        for kind in sorted({c["kind"] for c in facts.get("channels") or [] if c["enabled"]})
    )
    rows.append(
        Item(
            "db",
            cells=[
                tx("sp.dbHost", kind=dep["databaseKind"]),
                tx("sp.db"),
                tx(
                    "sp.declared",
                    region=(facts["application"].get("residency") or {}).get("region") or region,
                ),
                tx("sp.always"),
            ],
            sources=["env.database", "app.residency"],
            flag="human",
        )
    )
    if dep["otelEnabled"]:
        rows.append(
            Item(
                "otel",
                cells=[tx("sp.otelName"), tx("sp.otel"), tx("sp.providerWhere"), tx("sp.always")],
                sources=["env.otel"],
                flag="human",
            )
        )
    if dep["sharedState"]:
        rows.append(
            Item(
                "redis",
                cells=["Redis / Valkey", tx("sp.redis"), tx("sp.providerWhere"), tx("sp.always")],
                sources=["env.redis"],
            )
        )
    if dep["sso"]["configured"]:
        rows.append(
            Item(
                "sso",
                cells=[tx("sp.idp"), tx("sp.sso"), tx("sp.providerWhere"), tx("sp.consoleOnly")],
                sources=["env.sso"],
            )
        )
    return rows


def _transfers(facts: dict[str, Any], tx: Tx) -> Item:
    outside = [
        m["id"]
        for m in facts["models"]
        if (m["provider"] == "bedrock" and m.get("geography") not in ("eu",))
        or m["provider"] not in ("bedrock", "ollama", "test", "function")
    ]
    region = facts["deployment"]["awsRegion"]
    note = (facts.get("profile") or {}).get("gdpr", {}).get("transfers_note") or ""
    if outside or not region.startswith("eu-"):
        text = tx("tr.some", models=tx.join(outside) or "-", region=region)
        flag = "warn"
    else:
        text = tx("tr.none", region=region)
        flag = None
    if note:
        text = f"{text} {note}"
    return Item(
        "transfers",
        cells=[tx("f.transfers"), text],
        sources=["agent.models", "env.aws_region", "profile.gdpr"],
        flag=flag,
    )


def _data_categories(facts: dict[str, Any], tx: Tx) -> list[str]:
    app, grants, dep = facts["application"], facts["grants"], facts["deployment"]
    cats = [tx("cat.identifier"), tx("cat.usage")]
    if app["storageMode"] == "evokai":
        cats.append(tx("cat.conversations"))
    else:
        cats.append(tx("cat.conversationsTransit"))
    if facts["mcpServers"]:
        cats.append(tx("cat.toolData"))
    pii_types = facts["security"].get("piiTypes") or [
        *PII_TYPES,
        *facts["security"].get("piiDeclared", []),
    ]
    cats.append(tx("cat.identifiers", types=", ".join(pii_types)))
    if app["memoryMode"] != "off" and grants.get("memory") and app["storageMode"] == "evokai":
        cats.append(tx("cat.memories"))
    if grants.get("voiceServer"):
        cats.append(tx("cat.voice"))
    if facts["knowledgeBases"]:
        cats.append(tx("cat.documents"))
    if any(s["connector"] == "imap" for s in facts["mcpServers"]):
        cats.append(tx("cat.email"))
    if any(c["granted"] for c in facts.get("connections") or []):
        cats.append(tx("cat.connected"))
    if any(c["enabled"] for c in facts.get("channels") or []):
        cats.append(tx("cat.channels"))
    if dep["sso"]["configured"]:
        cats.append(tx("cat.console"))
    cats += list((facts.get("profile") or {}).get("gdpr", {}).get("data_categories") or [])
    return cats


def _retention_rows(facts: dict[str, Any], tx: Tx) -> list[Item]:
    app, dep = facts["application"], facts["deployment"]
    conv = _retention(app["conversationTtlDays"], tx)
    rows = [
        (
            "conversations",
            tx("ret.conversations"),
            "evokai_sessions, evokai_messages, evokai_events",
            conv if app["storageMode"] == "evokai" else tx("ret.notStored"),
            "app.conversation_ttl_days",
        ),
        ("traces", tx("ret.traces"), "evokai_traces", conv, "app.conversation_ttl_days"),
        (
            "memories",
            tx("ret.memories"),
            "evokai_memories",
            tx("ret.off") if app["memoryMode"] == "off" else _retention(app["memoryTtlDays"], tx),
            "app.memory",
        ),
        (
            "events",
            tx("ret.events"),
            "evokai_security_events",
            _retention(app["securityEventTtlDays"] or dep["securityEventTtlDays"], tx),
            "app.security_event_ttl_days",
        ),
        (
            "audit",
            tx("ret.audit"),
            "evokai_audit_log",
            _retention(app["auditTtlDays"] or dep["auditTtlDays"], tx),
            "app.audit_ttl_days",
        ),
        (
            "credentials",
            tx("ret.credentials"),
            "evokai_session_credentials",
            tx("ret.minutes", n=dep["sessionTokenMaxTtl"] // 60),
            "env.session_token",
        ),
        ("usage", tx("ret.usage"), "evokai_usage_records", tx("ret.usageKept"), "table.usage"),
    ]
    rows += [
        (
            f"kb.{b['name']}",
            tx("ret.kb", name=b["name"]),
            "evokai_knowledge_*",
            _retention(b["retentionDays"], tx),
            "table.knowledge",
        )
        for b in facts["knowledgeBases"]
    ]
    rows += [
        (
            f"wf.{w['name']}",
            tx("ret.wf", name=w["name"]),
            "evokai_workflow_runs",
            _retention(w["retentionDays"] or 90, tx),
            "workflow.approvals",
        )
        for w in facts["workflows"]
    ]
    return [Item(i, cells=[a, b, c], sources=[src]) for i, a, b, c, src in rows]


def _dpia_criteria(facts: dict[str, Any]) -> list[tuple[str, bool | None, list[str]]]:
    g = (facts.get("profile") or {}).get("gdpr") or {}
    q = (facts.get("profile") or {}).get("ai_act") or {}
    answered = bool(g)
    return [
        ("innovative", True, ["agent.models"]),
        ("evaluation", bool(q.get("profiling")) if answered or q else None, ["profile.ai_act"]),
        ("automated", bool(g.get("automated_decisions")) if answered else None, ["profile.gdpr"]),
        (
            "monitoring",
            bool(g.get("systematic_monitoring")) if answered else None,
            ["profile.gdpr"],
        ),
        ("sensitive", bool(g.get("special_categories")) if answered else None, ["profile.gdpr"]),
        (
            "scale",
            bool(g.get("large_scale")) if answered else None,
            ["profile.gdpr", "code.activity"],
        ),
        (
            "matching",
            bool(facts["mcpServers"]) and bool(facts["knowledgeBases"]),
            ["mcp.servers", "table.knowledge"],
        ),
        ("vulnerable", bool(g.get("vulnerable_subjects")) if answered else None, ["profile.gdpr"]),
        ("prevents", None, ["profile.gdpr"]),
    ]


RISKS = (
    (
        "injection",
        ("input.injection", "untrusted.spotlight", "untrusted.injection", "tools.policy"),
    ),
    ("disclosure", ("pii.model", "pii.history", "pii.logs", "pii.browser", "output.secrets")),
    ("exfiltration", ("output.links", "tools.egress")),
    ("agency", ("tools.policy", "tools.args", "tools.loop")),
    ("poisoning", ("memory.write", "untrusted.injection")),
    ("inaccuracy", ("scope",)),
    ("retention", ()),
    ("transfer", ()),
    ("access", ()),
    ("availability", ("abuse.rate", "abuse.concurrency", "turn.budget", "tools.rate")),
)


def gdpr(facts: dict[str, Any], tx: Tx, snapshot: dict[str, Any] | None = None) -> Pack:
    """The GDPR pack.

    Returns:
        The pack.
    """
    pk = _new("gdpr", facts, tx, snapshot)
    app, prof = facts["application"], facts.get("profile") or {}
    g = prof.get("gdpr") or {}

    s = pk.section("ropa", tx("gd.ropa"))
    s.add(_p("ropa.role", tx("gd.roleNote"), ["code.privacy"]))
    controller, cflag = _todo(tx, prof.get("controller", ""))
    processor, pflag = _todo(tx, prof.get("processor", ""))
    purposes = g.get("purposes") or []
    inferred_purposes = [tx("purpose.assist", app=app["name"])]
    if facts["grants"].get("server") and any("server" in a["channels"] for a in facts["agents"]):
        inferred_purposes.append(tx("purpose.automation"))
    if facts["knowledgeBases"]:
        inferred_purposes.append(tx("purpose.knowledge"))
    if app["memoryMode"] != "off" and app["storageMode"] == "evokai":
        inferred_purposes.append(tx("purpose.memory"))
    inferred_purposes.append(tx("purpose.security"))
    subjects = g.get("data_subjects") or [tx("subj.users", app=app["name"])] + (
        [tx("subj.third")] if facts["mcpServers"] or facts["knowledgeBases"] else []
    ) + ([tx("subj.staff")] if facts["deployment"]["sso"]["configured"] else [])
    basis = g.get("legal_basis") or ""
    s.add(
        _fields(
            "ropa.fields",
            [
                Item(
                    "controller",
                    cells=[tx("f.controller"), controller],
                    sources=["profile.system"],
                    flag=cflag,
                ),
                Item(
                    "processor",
                    cells=[tx("f.processor"), processor],
                    sources=["profile.system"],
                    flag=pflag,
                ),
                Item(
                    "dpo",
                    cells=[
                        tx("f.dpo"),
                        tx("yes")
                        if g.get("dpo_appointed")
                        else tx("no")
                        if g.get("dpo_appointed") is False
                        else tx("todo"),
                    ],
                    sources=["profile.gdpr"],
                    flag=None if g.get("dpo_appointed") is not None else "human",
                ),
                Item(
                    "purposes",
                    cells=[tx("f.purposes"), tx.join(purposes or inferred_purposes)],
                    sources=["profile.gdpr"]
                    if purposes
                    else ["agent.channels", "table.knowledge", "app.memory"],
                ),
                Item(
                    "basis",
                    cells=[
                        tx("f.basis"),
                        (
                            tx(f"basis.{basis}")
                            + (f". {g['legal_basis_note']}" if g.get("legal_basis_note") else "")
                        )
                        if basis
                        else tx("todo"),
                    ],
                    sources=["profile.gdpr"],
                    flag=None if basis else "human",
                ),
                Item(
                    "subjects",
                    cells=[tx("f.subjects"), tx.join(subjects)],
                    sources=["profile.gdpr"]
                    if g.get("data_subjects")
                    else ["agent.channels", "mcp.servers"],
                ),
                Item(
                    "categories",
                    cells=[tx("f.categories"), tx.join(_data_categories(facts, tx))],
                    sources=[
                        "app.storage_mode",
                        "app.security",
                        "app.memory",
                        "grant.voice",
                        "mcp.servers",
                    ],
                ),
                Item(
                    "special",
                    cells=[
                        tx("f.special"),
                        tx("gd.special") if g.get("special_categories") else tx("gd.noSpecial"),
                    ],
                    sources=["profile.gdpr"],
                    flag="warn" if g.get("special_categories") else None,
                ),
                Item(
                    "recipients",
                    cells=[
                        tx("f.recipients"),
                        tx("gd.recipientsSee")
                        + (f" {tx.join(g['recipients'])}" if g.get("recipients") else ""),
                    ],
                    sources=["env.aws_region", "profile.gdpr"],
                ),
                _transfers(facts, tx),
                Item(
                    "retention",
                    cells=[tx("f.retention"), tx("gd.retentionSee")],
                    sources=["app.conversation_ttl_days"],
                ),
                Item(
                    "security",
                    cells=[tx("f.security"), tx("gd.securitySee")],
                    sources=["app.security"],
                ),
            ],
        )
    )

    s = pk.section("subprocessors", tx("gd.subprocessors"))
    s.add(
        _table(
            "subprocessors.list",
            [tx("col.service"), tx("col.purpose"), tx("col.where"), tx("col.when")],
            _subprocessors(facts, tx),
        )
    )
    s.add(_p("subprocessors.note", tx("gd.noProviderStorage"), ["code.policy"]))
    for block in _data_protection(facts, tx):
        s.add(block)

    s = pk.section("retention", tx("gd.retention"))
    s.add(
        _table(
            "retention.list",
            [tx("col.data"), tx("col.table"), tx("col.kept")],
            _retention_rows(facts, tx),
        )
    )
    s.add(_p("retention.purge", tx("gd.purge"), ["code.purge"]))

    s = pk.section("dpia", tx("gd.dpia"))
    s.add(_p("dpia.intro", tx("gd.dpiaIntro"), ["law.wp248"]))
    crit = _dpia_criteria(facts)
    met = sum(1 for _, v, _ in crit if v)
    s.add(
        _table(
            "dpia.screening",
            [tx("col.criterion"), tx("col.met")],
            (
                Item(
                    k,
                    cells=[
                        tx(f"crit.{k}"),
                        tx("yes") if v else tx("no") if v is False else tx("todo"),
                    ],
                    sources=src,
                    flag="human" if v is None else None,
                )
                for k, v, src in crit
            ),
            title=tx("gd.screening"),
        )
    )
    s.add(
        _p(
            "dpia.verdict",
            tx("gd.dpiaRequired", n=met) if met >= 2 else tx("gd.dpiaMaybe", n=met),
            ["law.wp248", "profile.gdpr"],
        )
    )
    s.add(
        _fields(
            "dpia.description",
            [
                Item(
                    "nature",
                    cells=[
                        tx("dp.nature"),
                        tx(
                            "dp.natureText",
                            agents=len(facts["agents"]),
                            tools=sum(len(x["tools"]) for x in facts["mcpServers"]),
                            n=facts["activity"]["conversations"],
                            days=facts["activity"]["days"],
                        ),
                    ],
                    sources=["table.agents", "mcp.tools", "code.activity"],
                ),
                Item(
                    "purpose",
                    cells=[tx("dp.purpose"), prof.get("intended_purpose") or tx("todo")],
                    sources=["profile.system"],
                    flag=None if prof.get("intended_purpose") else "human",
                ),
                Item(
                    "necessity",
                    cells=[tx("dp.necessity"), tx("dp.necessityText")],
                    sources=["profile.gdpr"],
                    flag="human",
                ),
                Item(
                    "minimisation",
                    cells=[
                        tx("dp.minimisation"),
                        tx(
                            "dp.minimisationText",
                            mode=_mode_summary(facts, "pii.model")[0],
                            history=_mode_summary(facts, "pii.history")[0],
                        ),
                    ],
                    sources=["app.security", "code.privacy"],
                ),
            ],
            title=tx("gd.dpiaDescription"),
        )
    )
    rows = []
    for rid, controls in RISKS:
        measures = [f"{c} ({_mode_summary(facts, c)[0]})" for c in controls] + [
            tx(f"risk.{rid}.extra")
        ]
        rows.append(
            Item(
                rid,
                cells=[tx(f"risk.{rid}"), tx.join(measures), tx("todo")],
                sources=["app.security", "table.security_events"]
                if controls
                else ["app.conversation_ttl_days", "app.residency", "env.sso"],
                flag="human",
            )
        )
    s.add(
        _table(
            "dpia.risks",
            [tx("col.risk"), tx("col.measures"), tx("col.residual")],
            rows,
            title=tx("gd.risks"),
        )
    )
    s.add(
        _list(
            "dpia.signoff",
            (
                Item(k, tx(f"dp.sign.{k}"), sources=["profile.gdpr"], flag="human")
                for k in ("dpo", "approval", "review")
            ),
            title=tx("gd.signoff"),
        )
    )

    s = pk.section("rights", tx("gd.rights"))
    s.add(_p("rights.intro", tx("gd.rightsIntro"), ["code.privacy"]))
    s.add(
        _table(
            "rights.list",
            [tx("col.right"), tx("col.how")],
            (
                Item(
                    r,
                    cells=[tx(f"right.{r}"), tx(f"right.{r}.how")],
                    sources=["api.privacy"]
                    if r not in ("objection", "automated")
                    else ["profile.gdpr"],
                    flag="human" if r in ("automated",) else None,
                )
                for r in (
                    "access",
                    "portability",
                    "rectification",
                    "erasure",
                    "restriction",
                    "objection",
                    "consent",
                    "automated",
                )
            ),
        )
    )

    s = pk.section("dpa", tx("gd.dpa"))
    s.add(_p("dpa.intro", tx("gd.dpaIntro"), ["code.privacy"]))
    for group, items in _toms(facts, tx):
        s.add(_list(f"dpa.{group}", items, title=tx(f"tom.{group}")))
    return pk


def _toms(facts: dict[str, Any], tx: Tx) -> list[tuple[str, list[Item]]]:
    app, dep = facts["application"], facts["deployment"]
    sso = dep["sso"]["configured"]
    res = app.get("residency") or {}
    return [
        (
            "pseudonymisation",
            [
                Item("subject", tx("tom.subject"), sources=["code.privacy"]),
                Item("hashes", tx("tom.hashes"), sources=["table.traces", "table.audit"]),
                Item(
                    "masking",
                    tx(
                        "tom.masking",
                        model=_mode_summary(facts, "pii.model")[0],
                        history=_mode_summary(facts, "pii.history")[0],
                        logs=_mode_summary(facts, "pii.logs")[0],
                    ),
                    sources=["app.security"],
                ),
            ],
        ),
        (
            "encryption",
            [
                Item("secrets", tx("tom.fernet"), sources=["code.security"]),
                Item(
                    "transit",
                    tx("tom.tls", db=tx("yes") if dep["databaseTls"] else tx("todo")),
                    sources=["env.database"],
                    flag=None if dep["databaseTls"] else "human",
                ),
                Item("rest", tx("tom.rest"), sources=["env.database"], flag="human"),
            ],
        ),
        (
            "access",
            [
                Item("keys", tx("tom.keys"), sources=["code.security"]),
                Item("roles", tx("tom.roles"), sources=["code.roles"]),
                Item(
                    "sso",
                    tx("tom.ssoOn") if sso else tx("tom.ssoOff"),
                    sources=["env.sso"],
                    flag=None if sso else "warn",
                ),
                Item(
                    "tokens",
                    tx("tom.tokens", min=dep["sessionTokenMaxTtl"] // 60),
                    sources=["env.session_token"],
                ),
                Item("asUser", tx("tom.asUser"), sources=["mcp.servers"]),
            ],
        ),
        (
            "integrity",
            [
                Item("confirm", tx("tom.confirm"), sources=["mcp.tools"]),
                Item("guard", tx("tom.guard"), sources=["app.security"]),
                Item("audit", tx("tom.audit"), sources=["table.audit"]),
            ],
        ),
        (
            "availability",
            [
                Item("limits", tx("tom.limits"), sources=["app.caps", "app.security"]),
                Item("fallbacks", tx("tom.fallbacks"), sources=["agent.models"]),
                Item("backup", tx("tom.backup"), sources=["env.database"], flag="human"),
            ],
        ),
        (
            "separation",
            [
                Item("apps", tx("tom.apps"), sources=["code.privacy"]),
                Item(
                    "residency",
                    tx(
                        "tom.residency",
                        region=res.get("region") or dep["awsRegion"],
                        geos=", ".join(res.get("model_regions") or []) or tx("none"),
                    ),
                    sources=["app.residency"],
                    flag=None if res else "warn",
                ),
                Item("noProvider", tx("tom.noProvider"), sources=["code.policy"]),
            ],
        ),
        (
            "deletion",
            [
                Item("ttl", tx("tom.ttl"), sources=["code.purge", "app.conversation_ttl_days"]),
                Item("erase", tx("tom.erase"), sources=["api.privacy"]),
            ],
        ),
        (
            "testing",
            [
                Item("redteam", tx("tom.redteam"), sources=["table.redteam", "app.redteam_gate"]),
                Item("evals", tx("tom.evals"), sources=["table.evals"]),
                Item("ci", tx("tom.ci"), sources=["code.ci"]),
            ],
        ),
        (
            "incidents",
            [
                Item("events", tx("tom.events"), sources=["table.security_events"]),
                Item("process", tx("tom.process"), sources=["profile.gdpr"], flag="human"),
            ],
        ),
    ]


# Control mapping


def _status(facts: dict[str, Any], rule: str) -> str:
    """Whether what a control mapping relies on is in force.

    Returns:
        supported, available, partial or organisation.
    """
    dep, app = facts["deployment"], facts["application"]
    checks: dict[str, Callable[[], str]] = {
        "sso": lambda: SUPPORTED if dep["sso"]["configured"] else AVAILABLE,
        "gate": lambda: SUPPORTED if app["redteamGate"] == "enforce" else PARTIAL,
        "residency": lambda: SUPPORTED if app.get("residency") else AVAILABLE,
        "ttl": lambda: SUPPORTED if app.get("conversationTtlDays") else AVAILABLE,
        "masking": lambda: (
            SUPPORTED if _mode_summary(facts, "pii.model")[0] == "enforce" else PARTIAL
        ),
        "otel": lambda: SUPPORTED if dep["otelEnabled"] else PARTIAL,
        "systempolicy": lambda: SUPPORTED if dep.get("systemPolicy") else AVAILABLE,
        "dataprotection": lambda: (
            SUPPORTED
            if (facts.get("dataProtection") or {}).get("profile")
            or (facts.get("dataProtection") or {}).get("systemProfile")
            else AVAILABLE
        ),
        "partial": lambda: PARTIAL,
        "org": lambda: ORGANISATION,
    }
    return checks.get(rule, lambda: SUPPORTED)()


def controls(facts: dict[str, Any], tx: Tx, snapshot: dict[str, Any] | None = None) -> Pack:
    """The ISO/IEC 27001 Annex A, SOC 2 and ISO/IEC 42001 Annex A mapping.

    Returns:
        The pack.
    """
    pk = _new("controls", facts, tx, snapshot)
    s = pk.section("scope", tx("ct.scope"))
    s.add(_p("scope.support", tx("ct.support"), ["code.ci"]))
    s.add(
        _list(
            "scope.statuses",
            (
                Item(k, tx(f"st.{k}.long"), sources=["code.ci"])
                for k in (SUPPORTED, AVAILABLE, PARTIAL, ORGANISATION)
            ),
            title=tx("ct.legend"),
        )
    )
    counts: dict[str, dict[str, int]] = {}
    for sid, title, catalogue, mapping, prefix in (
        ("iso27001", tx("ct.iso"), ISO_CONTROLS, CONTROL_MAP, "ctrl"),
        ("soc2", tx("ct.soc2"), SOC2_CONTROLS, CONTROL_MAP, "ctrl"),
        ("iso42001", tx("ct.iso42001"), tuple(ISO42001_MAP), ISO42001_MAP, "ctrl42"),
    ):
        sec = pk.section(sid, title)
        if sid == "iso42001":
            sec.add(_p(f"{sid}.note", tx("ct.iso42001.note"), ["code.ci"]))
        rows = []
        tally: dict[str, int] = {}
        for cid in catalogue:
            feature, evidence, rule = mapping[cid]
            st = _status(facts, rule)
            tally[st] = tally.get(st, 0) + 1
            rows.append(
                Item(
                    cid,
                    cells=[
                        cid,
                        tx(f"{prefix}.{cid}"),
                        tx(f"feat.{feature}"),
                        evidence,
                        tx(f"st.{st}"),
                    ],
                    sources=[f"map.{feature}"],
                    flag="human" if st == ORGANISATION else "warn" if st == AVAILABLE else None,
                )
            )
        sec.add(
            _table(
                f"{sid}.map",
                [
                    tx("col.control"),
                    tx("col.title"),
                    tx("col.feature"),
                    tx("col.evidence"),
                    tx("col.status"),
                ],
                rows,
            )
        )
        sec.add(
            _p(
                f"{sid}.summary",
                tx(
                    "ct.summary",
                    **{k: tally.get(k, 0) for k in (SUPPORTED, AVAILABLE, PARTIAL, ORGANISATION)},
                ),
                ["code.ci"],
            )
        )
        counts[sid] = tally
    pk.extra["counts"] = counts
    return pk


BUILDERS: dict[str, Callable[..., Pack]] = {"ai-act": ai_act, "gdpr": gdpr, "controls": controls}


def build(
    pack: str, facts: dict[str, Any], lang: str, snapshot: dict[str, Any] | None = None
) -> Pack:
    """One pack in one language.

    Returns:
        The pack (``KeyError`` for an unknown pack).
    """
    return BUILDERS[pack](facts, Tx(lang), snapshot)
