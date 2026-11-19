"""Packs as Markdown and as a printable HTML document.

Both show the same content as the JSON: the disclaimer first, the metadata, the sections, and the
sources as numbered footnotes after each statement. Parts to complete are marked; gaps are marked.
The HTML is self-contained (inline styles, no script, no external resource) with a print
stylesheet: A4 pages, tables that do not split rows, footnotes kept at the end.
"""

from __future__ import annotations

import html
from typing import Any

from evokai.compliance.texts import Tx

PRINT_CSS = """
:root { --ink: #1d1a24; --muted: #5d5868; --line: #d9d4e2; --accent: #5b21b6; --warn: #8a4b00;
  --warnbg: #fff4e0; --human: #0b5c66; --humanbg: #e3f5f7; }
* { box-sizing: border-box; }
body { font: 10.5pt/1.5 "Inter", "Segoe UI", system-ui, sans-serif; color: var(--ink);
  max-width: 980px; margin: 32px auto; padding: 0 24px; background: #fff; }
h1 { font-size: 22pt; margin: 0 0 4px; color: var(--accent); letter-spacing: -0.01em; }
h2 { font-size: 14pt; margin: 28px 0 8px; padding-bottom: 4px;
  border-bottom: 2px solid var(--accent); }
h3 { font-size: 11pt; margin: 18px 0 6px; }
.sub { color: var(--muted); margin: 0 0 16px; }
.disclaimer { border: 1px solid var(--line); border-left: 4px solid var(--accent);
  padding: 10px 14px;
  background: #f7f5fb; margin: 16px 0; font-size: 9.5pt; }
dl.meta { display: grid; grid-template-columns: max-content 1fr; gap: 2px 16px; font-size: 9.5pt;
  margin: 0 0 12px; }
dl.meta dt { color: var(--muted); }
dl.meta dd { margin: 0; }
nav ol { columns: 2; font-size: 9.5pt; padding-left: 18px; }
table { width: 100%; border-collapse: collapse; margin: 6px 0 12px; font-size: 9pt; }
th, td { text-align: left; vertical-align: top; padding: 5px 7px;
  border-bottom: 1px solid var(--line); }
th { background: #f2eff7; font-weight: 600; }
table.fields th { width: 28%; background: transparent; color: var(--muted); font-weight: 500; }
tr.warn td { background: var(--warnbg); }
tr.human td, li.human, p.human { background: var(--humanbg); }
li.warn { background: var(--warnbg); }
.badge { display: inline-block; font-size: 7.5pt; font-weight: 600; padding: 0 5px;
  border-radius: 3px;
  margin-left: 4px; text-transform: uppercase; letter-spacing: 0.03em; vertical-align: 1px; }
.badge.human { color: var(--human); border: 1px solid var(--human); }
.badge.warn { color: var(--warn); border: 1px solid var(--warn); }
sup.fn a { color: var(--accent); text-decoration: none; font-size: 7pt; }
ol.footnotes { font-size: 8.5pt; color: var(--muted); }
code { font-size: 9pt; }
@page { size: A4; margin: 16mm 14mm; }
@media print {
  body { margin: 0; max-width: none; padding: 0; font-size: 9.5pt; }
  nav { display: none; }
  h2 { break-after: avoid; }
  table, li { break-inside: auto; }
  tr { break-inside: avoid; }
  .disclaimer { break-inside: avoid; }
  section.footnotes { break-before: page; }
  a { color: inherit; }
}
"""


def _numbers(pack: dict[str, Any]) -> dict[str, int]:
    return {f["key"]: f["n"] for f in pack["footnotes"]}


def _md_refs(keys: list[str], nums: dict[str, int]) -> str:
    return "".join(f"[^{nums[k]}]" for k in keys if k in nums)


def _md_cell(text: str) -> str:
    return text.replace("|", "\\|").replace("\n", " ")


def to_markdown(pack: dict[str, Any]) -> str:
    """A pack as Markdown with footnotes.

    Returns:
        The Markdown.
    """
    tx = Tx(pack["lang"])
    nums = _numbers(pack)
    human, warn = f" **({tx('flag.human')})**", f" **({tx('flag.warn')})**"
    out = [f"# {pack['title']}", "", f"*{pack['subtitle']}*", "", f"> {pack['disclaimer']}", ""]
    out += [f"- **{m['label']}**: {m['value']}" for m in pack["meta"]]
    out.append("")

    def mark(flag: str | None) -> str:
        return human if flag == "human" else warn if flag == "warn" else ""

    for s in pack["sections"]:
        out += [f"## {s['title']}", ""]
        for b in s["blocks"]:
            refs = _md_refs(b["sources"], nums)
            if b.get("title"):
                out += [f"### {b['title']}{refs if b['kind'] != 'p' else ''}", ""]
            kind = b["kind"]
            if kind == "p":
                out += [f"{b['text']}{refs}{human if b.get('human') else ''}", ""]
            elif kind == "list":
                for i in b.get("items", []):
                    out.append(f"- {i['text']}{_md_refs(i['sources'], nums)}{mark(i.get('flag'))}")
                out.append("")
            elif kind == "fields":
                out += ["| | |", "|---|---|"]
                for i in b.get("items", []):
                    k, v = i["cells"][0], i["cells"][1]
                    out.append(
                        f"| **{_md_cell(k)}** | {_md_cell(v)}{_md_refs(i['sources'], nums)}"
                        f"{mark(i.get('flag'))} |"
                    )
                out.append("")
            elif kind == "table":
                cols = b["columns"]
                out += ["| " + " | ".join(cols) + " |", "|" + "---|" * len(cols)]
                for i in b.get("items", []):
                    cells = [_md_cell(c) for c in i["cells"]]
                    cells[-1] += _md_refs(i["sources"], nums) + mark(i.get("flag"))
                    out.append("| " + " | ".join(cells) + " |")
                out.append("")
    out += [f"## {tx('footnotes')}", ""]
    out += [f"[^{f['n']}]: {f['text']}" for f in pack["footnotes"]]
    out.append("")
    return "\n".join(out)


def _e(text: Any) -> str:
    return html.escape(str(text), quote=True)


def _h_refs(keys: list[str], nums: dict[str, int]) -> str:
    return "".join(
        f'<sup class="fn"><a href="#fn{nums[k]}">[{nums[k]}]</a></sup>' for k in keys if k in nums
    )


def _badge(tx: Tx, flag: str | None) -> str:
    if flag not in ("human", "warn"):
        return ""
    return f'<span class="badge {flag}">{_e(tx(f"flag.{flag}"))}</span>'


def to_html(pack: dict[str, Any]) -> str:
    """A pack as a self-contained, printable HTML document.

    Returns:
        The HTML.
    """
    tx = Tx(pack["lang"])
    nums = _numbers(pack)
    body: list[str] = [
        f"<h1>{_e(pack['title'])}</h1>",
        f'<p class="sub">{_e(pack["subtitle"])}</p>',
        f'<div class="disclaimer" role="note">{_e(pack["disclaimer"])}</div>',
        '<dl class="meta">',
        *(f"<dt>{_e(m['label'])}</dt><dd>{_e(m['value'])}</dd>" for m in pack["meta"]),
        "</dl>",
        f"<nav><h3>{_e(tx('contents'))}</h3><ol>",
        *(f'<li><a href="#{_e(s["id"])}">{_e(s["title"])}</a></li>' for s in pack["sections"]),
        "</ol></nav>",
    ]
    for s in pack["sections"]:
        body.append(f'<section id="{_e(s["id"])}"><h2>{_e(s["title"])}</h2>')
        for b in s["blocks"]:
            refs = _h_refs(b["sources"], nums)
            kind = b["kind"]
            if b.get("title"):
                body.append(f"<h3>{_e(b['title'])}{refs if kind != 'p' else ''}</h3>")
            if kind == "p":
                cls = ' class="human"' if b.get("human") else ""
                badge = _badge(tx, "human") if b.get("human") else ""
                body.append(f'<p id="{_e(b["id"])}"{cls}>{_e(b["text"])}{refs}{badge}</p>')
            elif kind == "list":
                body.append(f'<ul id="{_e(b["id"])}">')
                for i in b.get("items", []):
                    cls = f' class="{i["flag"]}"' if i.get("flag") else ""
                    body.append(
                        f"<li{cls}>{_e(i['text'])}{_h_refs(i['sources'], nums)}"
                        f"{_badge(tx, i.get('flag'))}</li>"
                    )
                body.append("</ul>")
            elif kind == "fields":
                body.append(f'<table class="fields" id="{_e(b["id"])}"><tbody>')
                for i in b.get("items", []):
                    cls = f' class="{i["flag"]}"' if i.get("flag") else ""
                    body.append(
                        f'<tr{cls}><th scope="row">{_e(i["cells"][0])}</th>'
                        f"<td>{_e(i['cells'][1])}{_h_refs(i['sources'], nums)}"
                        f"{_badge(tx, i.get('flag'))}</td></tr>"
                    )
                body.append("</tbody></table>")
            elif kind == "table":
                body.append(f'<table id="{_e(b["id"])}"><thead><tr>')
                body += [f'<th scope="col">{_e(c)}</th>' for c in b["columns"]]
                body.append("</tr></thead><tbody>")
                for i in b.get("items", []):
                    cls = f' class="{i["flag"]}"' if i.get("flag") else ""
                    cells = [f"<td>{_e(c)}</td>" for c in i["cells"]]
                    cells[-1] = (
                        f"<td>{_e(i['cells'][-1])}{_h_refs(i['sources'], nums)}"
                        f"{_badge(tx, i.get('flag'))}</td>"
                    )
                    body.append(f"<tr{cls}>{''.join(cells)}</tr>")
                body.append("</tbody></table>")
        body.append("</section>")
    body.append(f'<section class="footnotes"><h2>{_e(tx("footnotes"))}</h2><ol class="footnotes">')
    body += [f'<li id="fn{f["n"]}">{_e(f["text"])}</li>' for f in pack["footnotes"]]
    body.append("</ol></section>")
    title = f"{pack['title']}: {pack['meta'][0]['value']}" if pack["meta"] else pack["title"]
    return (
        f'<!doctype html>\n<html lang="{_e(pack["lang"])}"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        f"<title>{_e(title)}</title><style>{PRINT_CSS}</style></head><body>"
        + "\n".join(body)
        + "</body></html>\n"
    )
