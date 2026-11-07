"""Report assembly and export.

Score and KPI tables are generated from computed data (never by the LLM) so
figures in the report are always exact. HTML export is self-contained (inline
CSS, no external assets) so it can be opened or archived offline.
"""

from __future__ import annotations

import html
import re
from datetime import datetime, timezone

from esglocal import __version__
from esglocal.catalog import CATEGORIES, Catalog
from esglocal.privacy.safe_markdown import safe_markdown
from esglocal.scoring import Assessment, rating_for


def _fmt(v, nd: int = 2) -> str:
    if v is None:
        return "—"
    if abs(v) >= 1000:
        return f"{v:,.0f}"
    return f"{v:,.{nd}f}"


def _cell(text: str) -> str:
    return str(text).replace("|", "\\|").replace("\n", " ")


def scores_table(assessment: Assessment) -> str:
    rows = ["| Category | Weight | Score | Rating |", "|---|---:|---:|---|"]
    for cat in CATEGORIES:
        s = assessment.category_scores.get(cat)
        rows.append(
            f"| {cat} | {assessment.weights.get(cat, 0):.0%} | {_fmt(s, 1)} | {rating_for(s)} |"
        )
    rows.append(
        f"| **Overall** | 100% | **{_fmt(assessment.overall, 1)}** | **{assessment.rating}** |"
    )
    return "\n".join(rows)


def kpi_table(assessment: Assessment, catalog: Catalog) -> str:
    rows = [
        "| ID | KPI | Value | Unit | Score | Frameworks |",
        "|---|---|---:|---|---:|---|",
    ]
    for r in assessment.results:
        kpi = catalog.kpi(r.kpi_id)
        value = "narrative" if r.qualitative and r.text else _fmt(r.value)
        if r.error and not r.text:
            value = "missing"
        rows.append(
            f"| {r.kpi_id} | {_cell(r.name)} | {value} | {_cell(r.unit)} | {_fmt(r.score, 0)} | "
            f"{_cell(', '.join(kpi.frameworks))} |"
        )
    return "\n".join(rows)


def build_report(
    company: str,
    assessment: Assessment,
    catalog: Catalog,
    body_markdown: str,
    strategy: dict | None = None,
    quality: list[dict] | None = None,
    model: str = "",
) -> str:
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    parts = [
        f"# ESG Report — {company or 'Company'}",
        f"*Industry:* {assessment.industry} · *Generated:* {now} · "
        f"*Model:* {model or 'n/a'} (local) · *ESG Local AI v{__version__}*",
        "",
        "## ESG scorecard",
        scores_table(assessment),
        "",
        f"KPI coverage: **{assessment.coverage:.0%}** of material quantitative KPIs.",
        "",
        body_markdown.strip(),
        "",
    ]
    if strategy and strategy.get("priorities"):
        parts += ["## Prioritised actions (structured)", ""]
        for i, p in enumerate(strategy["priorities"], 1):
            kpis = f" — KPIs: {', '.join(p['kpi_ids'])}" if p.get("kpi_ids") else ""
            parts.append(f"{i}. **{p['title']}** ({p.get('horizon', '')}){kpis}")
            for a in p.get("actions", []):
                parts.append(f"   - {a}")
            if p.get("expected_impact"):
                parts.append(f"   - *Expected impact:* {p['expected_impact']}")
        parts.append("")
    parts += ["## KPI detail", kpi_table(assessment, catalog), ""]
    if quality:
        notable = [q for q in quality if q["severity"] != "info"]
        if notable:
            parts.append("## Data-quality notes")
            parts += [f"- **{q['severity']}** {q['message']}" for q in notable]
            parts.append("")
    parts.append(
        "---\n*Generated entirely on-device. Benchmarks are illustrative defaults unless "
        "overridden; this report is decision support, not assured disclosure.*"
    )
    # LLM-written parts may contain injected remote images/links: neutralise everything
    return safe_markdown("\n".join(parts))


# ----------------------------------------------------------------- HTML export
_INLINE = [
    (re.compile(r"\*\*(.+?)\*\*"), r"<strong>\1</strong>"),
    (re.compile(r"(?<!\*)\*(?!\s)(.+?)(?<!\s)\*(?!\*)"), r"<em>\1</em>"),
    (re.compile(r"`(.+?)`"), r"<code>\1</code>"),
]


def _inline(text: str) -> str:
    out = html.escape(text, quote=False)
    for pattern, repl in _INLINE:
        out = pattern.sub(repl, out)
    return out


def markdown_to_html(md: str) -> str:
    """Small, safe Markdown subset → HTML (headings, lists, tables, emphasis, rules)."""
    lines = md.splitlines()
    out: list[str] = []
    i = 0
    list_stack: list[str] = []

    def close_lists():
        while list_stack:
            out.append(f"</{list_stack.pop()}>")

    while i < len(lines):
        line = lines[i]
        stripped = line.strip()
        if (
            stripped.startswith("|")
            and i + 1 < len(lines)
            and re.match(r"^\|[\s:|-]+\|$", lines[i + 1].strip())
        ):
            close_lists()
            header = [c.strip() for c in stripped.strip("|").split("|")]
            out.append(
                "<table><thead><tr>"
                + "".join(f"<th>{_inline(c)}</th>" for c in header)
                + "</tr></thead><tbody>"
            )
            i += 2
            while i < len(lines) and lines[i].strip().startswith("|"):
                cells = [c.strip() for c in re.split(r"(?<!\\)\|", lines[i].strip().strip("|"))]
                out.append(
                    "<tr>"
                    + "".join(f"<td>{_inline(c.replace(chr(92) + '|', '|'))}</td>" for c in cells)
                    + "</tr>"
                )
                i += 1
            out.append("</tbody></table>")
            continue
        m = re.match(r"^(#{1,4})\s+(.*)$", stripped)
        if m:
            close_lists()
            level = len(m.group(1))
            out.append(f"<h{level}>{_inline(m.group(2))}</h{level}>")
        elif stripped == "---":
            close_lists()
            out.append("<hr>")
        elif re.match(r"^\s*([-*]|\d+\.)\s+", line):
            indent = len(line) - len(line.lstrip())
            tag = "ol" if re.match(r"^\s*\d+\.", line) else "ul"
            depth = 1 + indent // 3
            while len(list_stack) < depth:
                list_stack.append(tag)
                out.append(f"<{tag}>")
            while len(list_stack) > depth:
                out.append(f"</{list_stack.pop()}>")
            item = re.sub(r"^\s*([-*]|\d+\.)\s+", "", line)
            out.append(f"<li>{_inline(item)}</li>")
        elif not stripped:
            close_lists()
        else:
            close_lists()
            out.append(f"<p>{_inline(stripped)}</p>")
        i += 1
    close_lists()
    return "\n".join(out)


_CSS = """
:root{--fg:#1f2933;--muted:#52606d;--line:#d9e2ec;--accent:#0b7a53;--bg:#fff}
@media (prefers-color-scheme:dark){:root{--fg:#e4e7eb;--muted:#9aa5b1;--line:#3e4c59;--accent:#3ebd93;--bg:#14191f}}
body{font:15px/1.6 system-ui,-apple-system,Segoe UI,Roboto,sans-serif;color:var(--fg);background:var(--bg);
max-width:960px;margin:2rem auto;padding:0 1rem}
h1{color:var(--accent)}h2{border-bottom:1px solid var(--line);padding-bottom:.25rem;margin-top:2rem}
table{border-collapse:collapse;width:100%;font-size:13px;margin:1rem 0;display:block;overflow-x:auto}
th,td{border:1px solid var(--line);padding:.35rem .5rem;text-align:left}th{background:rgba(127,127,127,.08)}
code{background:rgba(127,127,127,.12);padding:0 .25rem;border-radius:3px}em{color:var(--muted)}
"""


def to_html(markdown: str, title: str = "ESG Report") -> str:
    return (
        "<!doctype html><html lang='en'><head><meta charset='utf-8'>"
        "<meta name='viewport' content='width=device-width,initial-scale=1'>"
        f"<title>{html.escape(title)}</title><style>{_CSS}</style></head><body>"
        f"{markdown_to_html(markdown)}</body></html>"
    )
