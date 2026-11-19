"""What changed between two snapshots: statement by statement, and fact by fact.

Statements are compared by their ids (a block, or a block and one of its items), so a reworded
row is "changed" and a new agent is "added". Facts are compared by their JSON paths. Both sides
render in the same language, so the comparison reads like the packs.
"""

from __future__ import annotations

from typing import Any

from evokai.compliance.packs import PACKS, build

MAX_FACTS = 300
IGNORED_FACTS = ("collectedAt",)


def _statements(pack: dict[str, Any]) -> dict[str, tuple[str, str]]:
    """Every statement of a pack: id to (section title, text).

    Returns:
        The statements.
    """
    out: dict[str, tuple[str, str]] = {}
    for s in pack["sections"]:
        for b in s["blocks"]:
            if b.get("text"):
                out[b["id"]] = (s["title"], b["text"])
            for i in b.get("items", []):
                text = i.get("text") or " | ".join(i.get("cells") or [])
                out[f"{b['id']}/{i['id']}"] = (s["title"], text)
    return out


def _flatten(value: Any, path: str = "") -> dict[str, Any]:
    if isinstance(value, dict):
        out: dict[str, Any] = {}
        for k, v in value.items():
            out.update(_flatten(v, f"{path}.{k}" if path else str(k)))
        return out
    if isinstance(value, list) and value and all(isinstance(x, dict) for x in value):
        out = {}
        for i, v in enumerate(value):
            key = v.get("name") or v.get("id") or str(i)
            out.update(_flatten(v, f"{path}[{key}]"))
        return out
    return {path: value}


def facts_diff(old: dict[str, Any], new: dict[str, Any]) -> list[dict[str, Any]]:
    """Facts that differ.

    Returns:
        path, before, after (at most ``MAX_FACTS``).
    """
    a, b = _flatten(old), _flatten(new)
    out = []
    for path in sorted(set(a) | set(b)):
        if path.startswith(IGNORED_FACTS) or path.startswith("activity."):
            continue
        if a.get(path) != b.get(path):
            out.append({"path": path, "before": a.get(path), "after": b.get(path)})
        if len(out) >= MAX_FACTS:
            break
    return out


def diff(old: dict[str, Any], new: dict[str, Any], lang: str) -> dict[str, Any]:
    """Compare two snapshots.

    Args:
        old: The earlier snapshot (with its facts).
        new: The later one.
        lang: The language of the statements.

    Returns:
        from, to, packs (per pack: added, removed, changed statements, and the classification
        before and after for the AI Act), facts (paths that differ), counts.
    """
    packs: dict[str, Any] = {}
    total = 0
    for name in PACKS:
        a = build(name, old["facts"], lang, old).as_json()
        b = build(name, new["facts"], lang, new).as_json()
        sa, sb = _statements(a), _statements(b)
        added = [{"id": k, "section": sb[k][0], "text": sb[k][1]} for k in sb if k not in sa]
        removed = [{"id": k, "section": sa[k][0], "text": sa[k][1]} for k in sa if k not in sb]
        changed = [
            {"id": k, "section": sb[k][0], "before": sa[k][1], "after": sb[k][1]}
            for k in sb
            if k in sa and sa[k][1] != sb[k][1]
        ]
        entry: dict[str, Any] = {"added": added, "removed": removed, "changed": changed}
        if name == "ai-act":
            entry["classification"] = {
                "before": a["classification"]["level"],
                "after": b["classification"]["level"],
            }
        packs[name] = entry
        total += len(added) + len(removed) + len(changed)
    facts = facts_diff(old["facts"], new["facts"])
    return {
        "from": {"id": old["id"], "createdAt": old["createdAt"]},
        "to": {"id": new["id"], "createdAt": new["createdAt"]},
        "lang": lang,
        "packs": packs,
        "facts": facts,
        "counts": {"statements": total, "facts": len(facts)},
    }
