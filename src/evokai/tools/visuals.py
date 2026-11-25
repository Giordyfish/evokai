"""Charts (ui_chart), schemas (ui_diagram) and questions (ui_ask) for the client.

Model input is untrusted: everything is coerced, trimmed and capped here, and only these shapes
reach the client. The shapes are the client's contract: keep them stable.
"""

from __future__ import annotations

import math
import re
from typing import Any

CHART_TYPES = ["bar", "column", "line", "area", "donut", "stacked", "kpi"]
MAX_POINTS = 24
MAX_SLICES = 6
OTHER_LABEL = "Other"

_WS = re.compile(r"\s+")


def _text(v: Any, limit: int) -> str:
    if isinstance(v, bool) or not isinstance(v, (str, int, float)):
        return ""
    s = str(v) if not isinstance(v, float) or not v.is_integer() else str(int(v))
    return _WS.sub(" ", s).strip()[:limit]


UNITS = ("eur", "pct", "ms")


def _unit(v: Any) -> str:
    # A figure is money only when the model says so: a missing unit is a plain number.
    return v if v in UNITS else "count"


def _finite(v: Any) -> float | int | None:
    if isinstance(v, bool) or v is None or v == "":
        return None
    try:
        n = float(v.replace(",", ".")) if isinstance(v, str) else float(v)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(n):
        return None
    return int(n) if n.is_integer() else n


def _list(v: Any) -> list[dict[str, Any]]:
    return [x for x in v if isinstance(x, dict)] if isinstance(v, list) else []


def _points(v: Any, limit: int = MAX_POINTS) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for p in _list(v):
        label, value = _text(p.get("label"), 40), _finite(p.get("value"))
        if label and value is not None:
            out.append({"label": label, "value": value})
        if len(out) >= limit:
            break
    return out


def _slices(v: Any, other: str = OTHER_LABEL) -> list[dict[str, Any]]:
    """Shares of a whole: positive, biggest first, at most six (the tail folds into "Other").

    Returns:
        Points.
    """
    ps = sorted((p for p in _points(v, 60) if p["value"] > 0), key=lambda p: -p["value"])
    if len(ps) <= MAX_SLICES:
        return ps
    rest = sum(p["value"] for p in ps[MAX_SLICES - 1 :])
    return [*ps[: MAX_SLICES - 1], {"label": other, "value": rest}]


def to_chart(data: dict[str, Any], other: str = OTHER_LABEL) -> dict[str, Any] | None:
    """Sanitise a chart.

    Returns:
        The chart for the client, or None if unusable.
    """
    ctype = data.get("type") if data.get("type") in CHART_TYPES else "bar"
    title, subtitle = _text(data.get("title"), 80), _text(data.get("subtitle"), 100)
    if ctype == "kpi":
        items = []
        for it in _list(data.get("items") if data.get("items") is not None else data.get("points")):
            label, value = _text(it.get("label"), 40), _finite(it.get("value"))
            if not label or value is None:
                continue
            items.append({
                "label": label,
                "value": value,
                "unit": _unit(it.get("unit", data.get("unit"))),
                "delta": _finite(it.get("delta")),
                "note": _text(it.get("note"), 40),
            })
            if len(items) >= 4:
                break
        return (
            {"type": "kpi", "title": title, "subtitle": subtitle, "items": items} if items else None
        )
    if ctype in ("donut", "stacked"):
        ps = _slices(data.get("points"), other)
        return (
            {
                "type": ctype,
                "title": title,
                "subtitle": subtitle,
                "unit": _unit(data.get("unit")),
                "points": ps,
            }
            if len(ps) >= 2
            else None
        )
    ps = _points(data.get("points"))
    if not ps:
        return None
    highlight = _text(data.get("highlight"), 40)
    c = data.get("compare") if isinstance(data.get("compare"), dict) else None
    labels = [p["label"] for p in ps]
    cps = [p for p in _points(c.get("points")) if p["label"] in labels] if c else []
    compare = None
    if c and cps:
        by_label = {p["label"]: p for p in cps}
        compare = {
            "name": _text(c.get("name"), 40) or "Confronto",
            "points": [by_label[label] for label in labels if label in by_label],
        }
    return {
        "type": ctype,
        "title": title,
        "subtitle": subtitle,
        "unit": _unit(data.get("unit")),
        "points": ps,
        "highlight": highlight if highlight and highlight in labels else None,
        "name": _text(data.get("name"), 40),
        "compare": None if ctype == "bar" else compare,
    }


def to_diagram(data: dict[str, Any]) -> dict[str, Any] | None:
    """Sanitise a schema.

    Returns:
        The schema for the client, or None if unusable.
    """
    title = _text(data.get("title"), 80)
    kind = data.get("kind")
    if kind == "steps":
        steps = [
            {
                "label": _text(s.get("label"), 40),
                "note": _text(s.get("note"), 60),
                "state": s.get("state") if s.get("state") in ("done", "current") else "todo",
            }
            for s in _list(data.get("steps"))
        ]
        steps = [s for s in steps if s["label"]][:7]
        return {"kind": "steps", "title": title, "steps": steps} if len(steps) >= 2 else None
    if kind == "tree":
        r = data.get("root") if isinstance(data.get("root"), dict) else {}
        root = {
            "label": _text(r.get("label", data.get("root")) if r else data.get("root"), 40),
            "note": _text(r.get("note"), 60) if r else "",
        }
        children = [
            {"label": _text(c.get("label"), 40), "note": _text(c.get("note"), 60)}
            for c in _list(data.get("children"))
        ]
        children = [c for c in children if c["label"]][:8]
        return (
            {"kind": "tree", "title": title, "root": root, "children": children}
            if root["label"] and children
            else None
        )
    if kind == "compare":
        raw_cols = data.get("columns")
        cols: list[Any] = raw_cols if isinstance(raw_cols, list) else []
        columns = [
            _text(cols[0] if cols else None, 30) or "A",
            _text(cols[1] if len(cols) > 1 else None, 30) or "B",
        ]
        rows = []
        for r in _list(data.get("rows")):
            left, right = _text(r.get("left"), 40), _text(r.get("right"), 40)
            rows.append({
                "label": _text(r.get("label"), 40),
                "left": left,
                "right": right,
                "differs": left.lower() != right.lower(),
            })
        rows = [r for r in rows if r["label"]][:10]
        return (
            {"kind": "compare", "title": title, "columns": columns, "rows": rows} if rows else None
        )
    return None


_NUMBER = re.compile(r"-?\d+(?:\.\d+)?(?:[eE][-+]?\d+)?")


def numbers_in(text: str) -> set[float]:
    """The numbers written in a text (a tool's result), to check a chart against.

    Returns:
        Them, as floats.
    """
    out: set[float] = set()
    for tok in _NUMBER.findall(text or ""):
        try:
            n = float(tok)
        except ValueError:
            continue
        if math.isfinite(n):
            out.add(n)
    return out


def _decimals(v: float) -> int:
    s = repr(float(v))
    return 0 if float(v).is_integer() or "e" in s else len(s.split(".")[1])


def _found(v: float, seen: set[float]) -> bool:
    if v in seen:
        return True
    # Rounded the way the chart writes it (33.3 for 33.333…): still the tool's number.
    d = _decimals(v)
    return any(round(n, d) == v for n in seen)


def ungrounded(chart: dict[str, Any], seen: set[float]) -> list[float | int]:
    """The chart's values that no tool returned (a delta is a comparison, not a figure).

    Returns:
        Them, in the chart's order.
    """
    if chart["type"] == "kpi":
        values = [it["value"] for it in chart["items"]]
    else:
        values = [p["value"] for p in chart["points"]]
        values += [p["value"] for p in (chart.get("compare") or {}).get("points", [])]
    return [v for v in values if not _found(float(v), seen)]


def chart_summary(chart: dict[str, Any]) -> str:
    """What the model gets back, so it can refer to the chart without repeating the numbers.

    Returns:
        A one-line summary.
    """
    if chart["type"] == "kpi":
        return f"Shown {len(chart['items'])} key figures."
    return f"Grafico {chart['type']} con {len(chart['points'])} voci mostrato."


def to_ask(data: dict[str, Any]) -> dict[str, Any] | None:
    """Sanitise a question with options.

    Returns:
        The question for the client, or None if unusable.
    """
    question = _text(data.get("question"), 140)
    seen: set[str] = set()
    options: list[str] = []
    raw_options = data.get("options")
    for o in raw_options if isinstance(raw_options, list) else []:
        label = _text(o.get("label") if isinstance(o, dict) else o, 40)
        if not label or label.lower() in seen:
            continue
        seen.add(label.lower())
        options.append(label)
        if len(options) >= 5:
            break
    if not question or len(options) < 2:
        return None
    return {
        "question": question,
        "options": options,
        "multiple": data.get("multiple") is True,
        "other": data.get("other") is True,
    }
