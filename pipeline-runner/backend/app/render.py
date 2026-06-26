"""Data-driven report renderer: assembled report JSON → clean HTML → PDF.

This is a fresh renderer (the previous template-based one corrupted the cover
headline into per-character fragments). Every word and number comes from the
JSON; no report content is hardcoded. The renderer walks sections in canonical
order and renders each block by its `type`.

PDF is produced with the already-installed headless Chromium in new-headless
mode, which supports CSS `@page` margin boxes — so page numbers are pure CSS and
need no JS. Charts are rendered to inline SVG server-side (no client JS runs in
the print pass).
"""
import html
import os
import re
import shutil
import subprocess
import tempfile

from .runner import SECTION_ORDER

REPORTS_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "_reports"))

_CHROME_CANDIDATES = [
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    "/Applications/Chromium.app/Contents/MacOS/Chromium",
    "google-chrome", "chromium", "chromium-browser",
]


class CoverGuardError(RuntimeError):
    """Raised when the rendered cover does not contain the headline/base-case
    figures intact — refuses to ship a corrupted cover (it shipped twice)."""


def find_chrome():
    for c in _CHROME_CANDIDATES:
        if os.path.isabs(c):
            if os.path.exists(c):
                return c
        else:
            found = shutil.which(c)
            if found:
                return found
    return None


def pdf_available():
    return find_chrome() is not None


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #
def _esc(s):
    return html.escape("" if s is None else str(s))


def _css_str(s):
    # Escape for a CSS string inside <style>. Strip angle brackets so a value
    # like "</style>" can't break out of the style element.
    return (str("" if s is None else s)
            .replace("\\", "\\\\").replace('"', '\\"')
            .replace("<", "").replace(">", "").replace("\n", " "))


def _fmt_teur(n):
    """Numeric tEUR field → '1 598 tEUR' (only for raw numbers; pre-formatted
    strings in the JSON are rendered verbatim elsewhere)."""
    if n is None:
        return "–"
    if isinstance(n, str):
        return n
    try:
        f = float(n)
    except (TypeError, ValueError):
        return str(n)
    sign = "-" if f < 0 else ""
    whole = f"{abs(f):,.0f}".replace(",", " ")
    return f"{sign}{whole} tEUR"


def _fmt_pct(n):
    if n is None:
        return "–"
    if isinstance(n, str):
        return n
    try:
        f = float(n)
    except (TypeError, ValueError):
        return str(n)
    s = f"{f:.0f}" if float(f).is_integer() else f"{f:.1f}".replace(".", ",")
    return f"{s} %"


def _strip_tags(h):
    return re.sub(r"<[^>]+>", " ", h)


def _norm_ws(s):
    return re.sub(r"\s+", " ", html.unescape(s)).strip()


# --------------------------------------------------------------------------- #
# blocks
# --------------------------------------------------------------------------- #
def _block_heading(b):
    return f'<h3 class="b-heading">{_esc(b.get("text"))}</h3>'


def _block_paragraph(b):
    return f'<p class="b-paragraph">{_esc(b.get("text"))}</p>'


def _block_callout(b):
    variant = b.get("variant", "info")
    if variant not in ("info", "warning", "key"):
        variant = "info"
    title = b.get("title")
    title_html = f'<div class="callout-title">{_esc(title)}</div>' if title else ""
    return (
        f'<div class="callout callout-{variant}">{title_html}'
        f'<div class="callout-text">{_esc(b.get("text"))}</div></div>'
    )


def _block_metric_cards(b):
    cards = b.get("cards") or []
    cells = []
    for c in cards:
        if not isinstance(c, dict):
            continue
        emph = " mcard-emph" if c.get("emphasis") else ""
        cells.append(
            f'<div class="mcard{emph}">'
            f'<div class="mcard-value">{_esc(c.get("value"))}</div>'
            f'<div class="mcard-label">{_esc(c.get("label"))}</div></div>'
        )
    return f'<div class="metric-cards">{"".join(cells)}</div>'


def _block_key_value(b):
    items = [it for it in (b.get("items") or []) if isinstance(it, dict)]
    has_source = any(it.get("source") for it in items)
    title = b.get("title")
    rows = []
    for it in items:
        src = (
            f'<td class="kv-source">{_esc(it.get("source"))}</td>'
            if has_source else ""
        )
        rows.append(
            f'<tr><td class="kv-key">{_esc(it.get("key"))}</td>'
            f'<td class="kv-val">{_esc(it.get("value"))}</td>{src}</tr>'
        )
    head = f'<div class="kv-title">{_esc(title)}</div>' if title else ""
    return f'{head}<table class="key-value"><tbody>{"".join(rows)}</tbody></table>'


def _render_table(columns, rows, title=None, unit=None):
    cap = ""
    if title or unit:
        u = f' <span class="tbl-unit">({_esc(unit)})</span>' if unit else ""
        cap = f'<div class="tbl-title">{_esc(title)}{u}</div>'
    ths = "".join(f"<th>{_esc(c)}</th>" for c in (columns or []))
    trs = []
    for r in rows or []:
        cells = r if isinstance(r, list) else [r]
        trs.append("<tr>" + "".join(f"<td>{_esc(c)}</td>" for c in cells) + "</tr>")
    return (
        f'{cap}<table class="data-table"><thead><tr>{ths}</tr></thead>'
        f'<tbody>{"".join(trs)}</tbody></table>'
    )


def _block_table(b):
    if b.get("status") == "not_available":
        reason = b.get("reason") or "Tietoa ei saatavilla."
        title = b.get("title")
        head = f'<div class="tbl-title">{_esc(title)}</div>' if title else ""
        return f'{head}<p class="not-available">{_esc(reason)}</p>'
    return _render_table(b.get("columns"), b.get("rows"), b.get("title"),
                         b.get("unit"))


def _block_chart(b):
    if b.get("status") == "not_available":
        reason = b.get("reason") or "Kuvaajaa ei voitu muodostaa."
        title = b.get("title")
        head = f'<div class="tbl-title">{_esc(title)}</div>' if title else ""
        return f'{head}<p class="not-available">{_esc(reason)}</p>'
    title = b.get("title")
    head = f'<div class="chart-title">{_esc(title)}</div>' if title else ""
    svg = _chart_svg(b)
    return f'<div class="chart">{head}{svg}</div>'


def _block_scenario_table(b):
    name = b.get("scenario", "")
    value = _fmt_teur(b.get("value_teur"))
    prob = _fmt_pct(b.get("probability_pct"))
    drivers = b.get("drivers") or []
    driver_chips = "".join(
        f'<div class="driver"><span class="driver-k">{_esc(d.get("key"))}</span>'
        f'<span class="driver-v">{_esc(d.get("value"))}</span></div>'
        for d in drivers if isinstance(d, dict)
    )
    peru = b.get("perusluvut") or {}
    avain = b.get("avainluvut") or {}
    sub_p = _render_table(peru.get("columns"), peru.get("rows"), "Perusluvut")
    sub_a = _render_table(avain.get("columns"), avain.get("rows"), "Avainluvut")
    return (
        f'<div class="scenario-panel scenario-{_esc(name)}">'
        f'<div class="scenario-head">'
        f'<span class="scenario-name">{_esc(name).capitalize()}</span>'
        f'<span class="scenario-figs"><span class="scenario-val">{_esc(value)}</span>'
        f'<span class="scenario-prob">p = {_esc(prob)}</span></span></div>'
        f'<div class="drivers-strip"><div class="drivers-label">Ajurit – '
        f'näitä muuttamalla arvo muuttuu</div>'
        f'<div class="drivers-row">{driver_chips}</div></div>'
        f'<div class="scenario-subtables">'
        f'<div class="subtable">{sub_p}</div>'
        f'<div class="subtable">{sub_a}</div></div></div>'
    )


_BLOCKS = {
    "heading": _block_heading,
    "paragraph": _block_paragraph,
    "callout": _block_callout,
    "metric_cards": _block_metric_cards,
    "key_value": _block_key_value,
    "table": _block_table,
    "chart": _block_chart,
    "scenario_table": _block_scenario_table,
}


def _render_block(b):
    if not isinstance(b, dict):
        return ""
    fn = _BLOCKS.get(b.get("type"))
    if fn is None:
        return f'<div class="unknown-block">[tuntematon lohko: {_esc(b.get("type"))}]</div>'
    return fn(b)


# --------------------------------------------------------------------------- #
# charts (server-rendered SVG, no JS)
# --------------------------------------------------------------------------- #
_W, _H, _PAD = 640, 280, 40
_SERIES_COLORS = ["#1f6feb", "#2da44e", "#bf8700", "#cf222e", "#8250df", "#0a7ea4"]


def _nums(values):
    out = []
    for v in values or []:
        try:
            out.append(float(v) if v is not None else None)
        except (TypeError, ValueError):
            out.append(None)
    return out


def _chart_svg(b):
    ctype = b.get("chart_type", "bar")
    x_axis = b.get("x_axis") or []
    series = [s for s in (b.get("series") or []) if isinstance(s, dict)]
    try:
        if ctype == "heatmap_or_matrix":
            return _svg_heatmap(b, x_axis, series)
        return _svg_bars_lines(ctype, x_axis, series)
    except Exception:
        return '<p class="not-available">Kuvaajan piirto epäonnistui.</p>'


def _svg_bars_lines(ctype, x_axis, series):
    n = len(x_axis) or max((len(s.get("values") or []) for s in series), default=0)
    if n == 0 or not series:
        return '<p class="not-available">Ei kuvaajadataa.</p>'
    bar_series = [s for s in series if s.get("type", "bar") == "bar"]
    line_series = [s for s in series if s.get("type") == "line"]
    plot_w = _W - 2 * _PAD
    plot_h = _H - 2 * _PAD

    def _scaler(group):
        """A y-mapper for a set of series (bars and lines get their own scale so
        a percentage line is not flattened against revenue bars)."""
        vals = [v for s in group for v in _nums(s.get("values")) if v is not None]
        if not vals:
            return None, None
        vmax = max(vals + [0.0])
        vmin = min(vals + [0.0])
        span = (vmax - vmin) or 1.0
        return (lambda v: _PAD + plot_h * (1 - (v - vmin) / span)), vmin

    y_bar, bar_min = _scaler(bar_series)
    y_line, _ = _scaler(line_series)
    if y_bar is None and y_line is None:
        return '<p class="not-available">Ei kuvaajadataa.</p>'

    parts = [f'<svg viewBox="0 0 {_W} {_H}" class="svg-chart" '
             'xmlns="http://www.w3.org/2000/svg">']
    slot = plot_w / n
    grouped = ctype == "bar_grouped"
    bs_count = max(1, len(bar_series))
    bw = (slot * 0.7) / (bs_count if grouped else 1)

    if y_bar is not None:
        zero_y = y_bar(max(0.0, bar_min))
        parts.append(f'<line x1="{_PAD}" y1="{zero_y:.1f}" x2="{_W - _PAD}" '
                     f'y2="{zero_y:.1f}" class="axis"/>')
        for bi, s in enumerate(bar_series):
            color = _SERIES_COLORS[series.index(s) % len(_SERIES_COLORS)]
            vals = _nums(s.get("values"))
            for i in range(n):
                v = vals[i] if i < len(vals) else None
                if v is None:
                    continue
                cx = _PAD + slot * i + slot * 0.15
                x = cx + (bi * bw if grouped else (slot * 0.7 - bw) / 2)
                top = y_bar(max(v, 0.0))
                h = abs(zero_y - y_bar(v))
                parts.append(f'<rect x="{x:.1f}" y="{top:.1f}" width="{bw:.1f}" '
                             f'height="{h:.1f}" fill="{color}" opacity="0.9"/>')

    if y_line is not None:
        for s in line_series:
            color = _SERIES_COLORS[series.index(s) % len(_SERIES_COLORS)]
            vals = _nums(s.get("values"))
            pts = []
            for i in range(n):
                v = vals[i] if i < len(vals) else None
                if v is None:
                    continue
                cx = _PAD + slot * i + slot * 0.5
                pts.append(f"{cx:.1f},{y_line(v):.1f}")
            if pts:
                parts.append(f'<polyline points="{" ".join(pts)}" fill="none" '
                             f'stroke="{color}" stroke-width="2.5"/>')
                for pt in pts:
                    px, py = pt.split(",")
                    parts.append(f'<circle cx="{px}" cy="{py}" r="3" fill="{color}"/>')

    for i, lab in enumerate(x_axis):
        cx = _PAD + slot * i + slot * 0.5
        parts.append(f'<text x="{cx:.1f}" y="{_H - 12}" class="x-lab" '
                     f'text-anchor="middle">{_esc(lab)}</text>')

    legend_x = _PAD
    for s in series:
        color = _SERIES_COLORS[series.index(s) % len(_SERIES_COLORS)]
        parts.append(f'<rect x="{legend_x}" y="8" width="10" height="10" '
                     f'fill="{color}"/>')
        parts.append(f'<text x="{legend_x + 14}" y="17" class="legend">'
                     f'{_esc(s.get("name"))}</text>')
        legend_x += 14 + 8 * len(str(s.get("name") or "")) + 18
    parts.append("</svg>")
    return "".join(parts)


def _svg_heatmap(b, x_axis, series):
    rows = [s for s in series if s.get("values")]
    if not rows or not x_axis:
        return '<p class="not-available">Ei matriisidataa.</p>'
    allv = [v for s in rows for v in _nums(s.get("values")) if v is not None]
    if not allv:
        return '<p class="not-available">Ei matriisidataa.</p>'
    vmin, vmax = min(allv), max(allv)
    span = (vmax - vmin) or 1.0
    cell_w = (_W - 120) / len(x_axis)
    cell_h = 30
    h = _PAD + cell_h * len(rows) + 30
    parts = [f'<svg viewBox="0 0 {_W} {h:.0f}" class="svg-chart" '
             'xmlns="http://www.w3.org/2000/svg">']
    for ri, s in enumerate(rows):
        vals = _nums(s.get("values"))
        parts.append(f'<text x="8" y="{_PAD + cell_h * ri + 20:.0f}" '
                     f'class="x-lab">{_esc(s.get("name"))}</text>')
        for ci in range(len(x_axis)):
            v = vals[ci] if ci < len(vals) else None
            x = 120 + cell_w * ci
            yy = _PAD + cell_h * ri
            if v is None:
                fill = "#eee"
            else:
                t = (v - vmin) / span
                fill = f"rgb({int(207 - 120 * t)},{int(225 - 60 * t)},{int(247 - 100 * t)})"
            parts.append(f'<rect x="{x:.1f}" y="{yy:.0f}" width="{cell_w - 2:.1f}" '
                         f'height="{cell_h - 2}" fill="{fill}"/>')
            if v is not None:
                parts.append(f'<text x="{x + cell_w / 2:.1f}" y="{yy + 19:.0f}" '
                             f'text-anchor="middle" class="cell-lab">{_esc(v)}</text>')
    for ci, lab in enumerate(x_axis):
        parts.append(f'<text x="{120 + cell_w * ci + cell_w / 2:.1f}" '
                     f'y="{_PAD - 8:.0f}" text-anchor="middle" class="x-lab">'
                     f'{_esc(lab)}</text>')
    parts.append("</svg>")
    return "".join(parts)


# --------------------------------------------------------------------------- #
# pages
# --------------------------------------------------------------------------- #
def _ordered_sections(report):
    # Only canonical section ids are rendered (drops any stray/duplicate id and,
    # per contract, never renders a section 7).
    secs = [s for s in (report.get("sections") or [])
            if isinstance(s, dict) and str(s.get("id")) in SECTION_ORDER]
    return sorted(secs, key=lambda s: SECTION_ORDER.index(str(s.get("id"))))


def _cover_html(report):
    cover = report.get("cover") or {}
    meta = report.get("meta") or {}
    headline_label = cover.get("headline_label") or "Skenaarioiden odotusarvo"
    headline_value = cover.get("headline_value")
    base_case_value = cover.get("base_case_value")
    secondary = cover.get("secondary_lines") or []
    meta_bits = [meta.get("company_name"), meta.get("y_tunnus"),
                 meta.get("industry"), meta.get("report_date")]
    meta_line = "  ·  ".join(_esc(x) for x in meta_bits if x)
    sec_html = "".join(f'<div class="cover-secondary">{_esc(s)}</div>'
                       for s in secondary)
    # The two figures are single, intact text runs — no per-glyph layout, no
    # letter-spacing, no rotation. This is the bug that shipped twice.
    return (
        '<section class="page cover">'
        '<div class="cover-brand">Valuatum</div>'
        '<div class="cover-doctitle">AI-arvonmääritysraportti</div>'
        f'<div class="cover-company">{_esc(meta.get("company_name"))}</div>'
        f'<div class="cover-meta">{meta_line}</div>'
        '<div class="cover-figures">'
        '<div class="cover-fig cover-fig-primary">'
        f'<div class="cover-fig-label">{_esc(headline_label)}</div>'
        f'<div class="cover-fig-value">{_esc(headline_value)}</div></div>'
        '<div class="cover-fig cover-fig-base">'
        '<div class="cover-fig-label">Realistinen base case</div>'
        f'<div class="cover-fig-value">{_esc(base_case_value)}</div></div>'
        f'</div>{sec_html}'
        '</section>'
    )


def _cover_guard(report):
    """Assert the cover values appear intact in the rendered cover text. Raises
    CoverGuardError otherwise — refuses to ship a corrupted cover."""
    cover = report.get("cover") or {}
    hv = cover.get("headline_value")
    bcv = cover.get("base_case_value")
    text = _norm_ws(_strip_tags(_cover_html(report)))
    missing = []
    for label, val in (("headline_value", hv), ("base_case_value", bcv)):
        # A blank figure IS the corruption this guard exists to stop — the cover
        # must show both the expected value and the realistic base case.
        if val is None or str(val).strip() == "":
            missing.append(f"{label} puuttuu/tyhjä")
            continue
        if _norm_ws(str(val)) not in text:
            missing.append(f"{label}={val!r}")
    if missing:
        raise CoverGuardError(
            "Kannen luvut eivät renderöityneet eheinä: " + "; ".join(missing)
            + " — kansiteksti: " + text[:300]
        )


def _toc_html(sections):
    rows = "".join(
        f'<li><span class="toc-id">{_esc(s.get("id"))}</span>'
        f'<span class="toc-title">{_esc(s.get("title"))}</span></li>'
        for s in sections
    )
    return (
        '<section class="page toc"><h2 class="page-h2">Sisällys</h2>'
        f'<ol class="toc-list">{rows}</ol></section>'
    )


def _section_html(sec):
    blocks = "".join(_render_block(b) for b in (sec.get("blocks") or []))
    return (
        '<section class="report-section">'
        f'<h2 class="section-title"><span class="section-id">{_esc(sec.get("id"))}'
        f'</span>{_esc(sec.get("title"))}</h2>{blocks}</section>'
    )


def _page_css(report):
    meta = report.get("meta") or {}
    footer_left = _css_str(
        "Valuatum · " + (meta.get("company_name") or "AI-arvonmääritysraportti")
    )
    return f"""
@page {{
  size: A4;
  margin: 22mm 16mm 18mm 16mm;
  @bottom-left {{ content: "{footer_left}"; font-size: 8pt; color: #8a8a8a; }}
  @bottom-right {{ content: counter(page) " / " counter(pages); font-size: 8pt; color: #8a8a8a; }}
}}
@page cover {{ margin: 0; @bottom-left {{ content: ""; }} @bottom-right {{ content: ""; }} }}
"""


_STATIC_CSS = """
* { box-sizing: border-box; }
html, body { margin: 0; padding: 0; }
body {
  font-family: "Liberation Sans", "DejaVu Sans", Arial, sans-serif;
  color: #1a1a1a; font-size: 10.5pt; line-height: 1.5;
}
.page { page-break-after: always; }
.report-section { page-break-before: always; padding-top: 2mm; }
.page-h2, .section-title { font-size: 16pt; margin: 0 0 8mm 0; color: #0d1b2a; }
.section-id { display: inline-block; margin-right: .5em; color: #1f6feb; }

/* cover */
.cover {
  page: cover; position: relative; height: 297mm; padding: 30mm 24mm;
  background: linear-gradient(160deg, #0d1b2a 0%, #15314f 55%, #1f6feb 140%);
  color: #fff;
}
.cover-brand { font-size: 14pt; font-weight: 700; letter-spacing: normal; opacity: .9; }
.cover-doctitle { margin-top: 4mm; font-size: 13pt; opacity: .75; }
.cover-company { margin-top: 26mm; font-size: 30pt; font-weight: 700; line-height: 1.15; }
.cover-meta { margin-top: 4mm; font-size: 10pt; opacity: .8; }
.cover-figures { margin-top: 26mm; display: flex; gap: 18mm; flex-wrap: wrap; }
.cover-fig-label { font-size: 11pt; opacity: .8; margin-bottom: 2mm; }
/* headline + base case: single intact runs. NO letter-spacing, NO per-glyph layout. */
.cover-fig-value { font-size: 30pt; font-weight: 700; white-space: nowrap; letter-spacing: normal; word-spacing: normal; }
.cover-fig-primary .cover-fig-value { font-size: 38pt; color: #fff; }
.cover-fig-base .cover-fig-value { font-size: 26pt; color: #cfe3ff; }
.cover-secondary { margin-top: 5mm; font-size: 10pt; opacity: .82; }

/* toc */
.toc-list { list-style: none; padding: 0; margin: 0; font-size: 11pt; }
.toc-list li { display: flex; gap: 8px; padding: 3.2mm 0; border-bottom: 1px solid #e6e6e6; }
.toc-id { display: inline-block; min-width: 10mm; color: #1f6feb; font-weight: 700; }

/* blocks */
.b-heading { font-size: 12.5pt; margin: 6mm 0 2mm; color: #0d1b2a; }
.b-paragraph { margin: 0 0 3mm; }
.callout { border-radius: 6px; padding: 4mm 5mm; margin: 4mm 0; border-left: 4px solid; page-break-inside: avoid; }
.callout-info { background: #eef4ff; border-color: #1f6feb; }
.callout-warning { background: #fff5e8; border-color: #bf8700; }
.callout-key { background: #eaf7ee; border-color: #2da44e; }
.callout-title { font-weight: 700; margin-bottom: 1.5mm; }
.metric-cards { display: flex; flex-wrap: wrap; gap: 4mm; margin: 4mm 0; }
.mcard { flex: 1 1 38mm; min-width: 38mm; border: 1px solid #e0e0e0; border-radius: 6px; padding: 4mm; background: #fafafa; }
.mcard-emph { background: #0d1b2a; color: #fff; border-color: #0d1b2a; }
.mcard-value { font-size: 15pt; font-weight: 700; }
.mcard-label { font-size: 9pt; color: inherit; opacity: .75; margin-top: 1mm; }
.kv-title, .tbl-title, .chart-title { font-weight: 700; margin: 4mm 0 1.5mm; }
.tbl-unit { font-weight: 400; color: #777; font-size: 9pt; }
table.key-value, table.data-table { width: 100%; border-collapse: collapse; font-size: 9.5pt; margin: 1mm 0 4mm; }
table.data-table th, table.data-table td, table.key-value td { border: 1px solid #e2e2e2; padding: 1.6mm 2.4mm; text-align: left; vertical-align: top; }
table.data-table th { background: #f1f4f8; font-weight: 700; }
table.data-table tr:nth-child(even) td { background: #fafbfc; }
.kv-key { font-weight: 600; width: 38%; }
.kv-source { color: #888; font-size: 8.5pt; width: 26%; }
.not-available { color: #8a8a8a; font-style: italic; margin: 2mm 0 4mm; }
.chart { margin: 4mm 0; page-break-inside: avoid; }
.svg-chart { width: 100%; height: auto; }
.svg-chart .axis { stroke: #ccc; stroke-width: 1; }
.svg-chart .x-lab { font-size: 9px; fill: #555; }
.svg-chart .legend { font-size: 9px; fill: #555; }
.svg-chart .cell-lab { font-size: 9px; fill: #222; }

/* scenario panels */
.scenario-panel { border: 1px solid #d8dee6; border-radius: 8px; padding: 4mm 5mm; margin: 4mm 0; page-break-inside: avoid; }
.scenario-optimistinen { border-top: 4px solid #2da44e; }
.scenario-realistinen { border-top: 4px solid #1f6feb; }
.scenario-pessimistinen { border-top: 4px solid #cf222e; }
.scenario-head { display: flex; justify-content: space-between; align-items: baseline; }
.scenario-name { font-size: 13pt; font-weight: 700; }
.scenario-figs { display: flex; gap: 6mm; align-items: baseline; }
.scenario-val { font-size: 14pt; font-weight: 700; }
.scenario-prob { font-size: 10pt; color: #555; }
.drivers-strip { margin: 3mm 0; background: #f4f8ff; border: 1px solid #d6e4ff; border-radius: 6px; padding: 3mm; }
.drivers-label { font-size: 8.5pt; text-transform: uppercase; letter-spacing: .04em; color: #1f6feb; font-weight: 700; margin-bottom: 2mm; }
.drivers-row { display: flex; flex-wrap: wrap; gap: 3mm; }
.driver { display: flex; flex-direction: column; background: #fff; border: 1px solid #d6e4ff; border-radius: 5px; padding: 2mm 3mm; min-width: 28mm; }
.driver-k { font-size: 8pt; color: #666; }
.driver-v { font-size: 11pt; font-weight: 700; }
.scenario-subtables { display: flex; gap: 5mm; }
.scenario-subtables .subtable { flex: 1 1 0; min-width: 0; }
.unknown-block { color: #cf222e; font-size: 9pt; }
"""


def render_html(report):
    """Assembled report dict → full standalone HTML string. Runs the cover guard."""
    if not isinstance(report, dict):
        raise ValueError("report ei ole objekti")
    _cover_guard(report)
    sections = _ordered_sections(report)
    body = (
        _cover_html(report)
        + _toc_html(sections)
        + "".join(_section_html(s) for s in sections)
    )
    meta = report.get("meta") or {}
    title = _esc((meta.get("company_name") or "AI-arvonmääritysraportti"))
    return (
        "<!doctype html><html lang=\"fi\"><head><meta charset=\"utf-8\">"
        f"<title>{title}</title><style>{_page_css(report)}{_STATIC_CSS}</style>"
        f"</head><body>{body}</body></html>"
    )


def render_pdf(report, out_path):
    """Render the report to PDF via new-headless Chromium. Returns out_path."""
    chrome = find_chrome()
    if not chrome:
        raise RuntimeError(
            "Chrome/Chromium ei löytynyt — PDF-vienti ei käytettävissä."
        )
    html_str = render_html(report)
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    # Never let a previous render's file masquerade as success if Chrome fails.
    if os.path.exists(out_path):
        os.unlink(out_path)
    with tempfile.NamedTemporaryFile(
        "w", suffix=".html", delete=False, encoding="utf-8", dir=os.path.dirname(out_path)
    ) as f:
        f.write(html_str)
        html_path = f.name
    try:
        proc = subprocess.run(
            [chrome, "--headless=new", "--no-pdf-header-footer",
             f"--print-to-pdf={out_path}", "--virtual-time-budget=8000",
             "--no-sandbox", f"file://{html_path}"],
            capture_output=True, text=True, timeout=90,
        )
        if proc.returncode != 0 or not os.path.exists(out_path):
            raise RuntimeError("PDF-renderöinti epäonnistui:\n" + (proc.stderr or "")[:2000])
    finally:
        try:
            os.unlink(html_path)
        except OSError:
            pass
    return out_path
