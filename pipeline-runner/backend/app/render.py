"""Data-driven report renderer: assembled report JSON -> branded HTML -> PDF.

Visual system matches the Valuatum equity-research design (green palette,
Archivo + Source Sans 3, range bar, confidence pills, method/weight charts,
combo bar+line, heatmap). Everything is derived from the pipeline JSON so any
company renders automatically. No report content is hardcoded; internal pipeline
tokens ([input_data] etc.) are sanitised out — the reader never sees them.

PDF is produced with the already-installed headless Chromium (new-headless mode,
which supports CSS @page margin boxes, so page numbers are pure CSS).
"""
import html
import math
import os
import re
import shutil
import subprocess
import tempfile

from .runner import SECTION_ORDER

REPORTS_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "_reports"))
_REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
_TEMPLATE = os.path.join(_REPO_ROOT, "template", "report.template.html")

_CHROME_CANDIDATES = [
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    "/Applications/Chromium.app/Contents/MacOS/Chromium",
    "google-chrome", "chromium", "chromium-browser",
    "C:/Program Files/Google/Chrome/Application/chrome.exe",
    "C:/Program Files (x86)/Google/Chrome/Application/chrome.exe",
]

C = {
    "ink": "#1A1D1A", "lime": "#A6CE39", "limeDeep": "#8FB525", "green": "#2E4B3C",
    "greenSoft": "#E7EDE8", "greenLine": "#C7D4CB", "red": "#C0504D",
    "redSoft": "#F6E7E6", "gray": "#6B7280", "line": "#E1E4DE",
    "lineStrong": "#CBD0C9",
}
HEAD = "Archivo, system-ui, sans-serif"
SANS = "'Source Sans 3', system-ui, sans-serif"
SNAP_COLORS = ["#A6CE39", "#2E4B3C", "#6B7280", "#8FB525"]


class CoverGuardError(RuntimeError):
    """Rendered cover does not contain the headline/base-case figures intact."""


def find_chrome():
    for c in _CHROME_CANDIDATES:
        if os.path.isabs(c):
            if os.path.exists(c):
                return c
        elif shutil.which(c):
            return shutil.which(c)
    return None


def pdf_available():
    return find_chrome() is not None


# --------------------------------------------------------------------------- #
# text / numbers
# --------------------------------------------------------------------------- #
_VAR_RE = re.compile(r"\{\{[^}]*\}\}")
_PLACEHOLDER_RE = re.compile(r"\[\[[^\]]*\]\]")
_INPUT_TOK = re.compile(r"\[?\binput[_ ]?data\b\]?", re.IGNORECASE)
_ENRICH_TOK = re.compile(r"\[?\benrichment\b\]?", re.IGNORECASE)


def _clean(s):
    """Strip leaked pipeline tokens; the reader must never see [input_data]."""
    if s is None:
        return ""
    s = str(s)
    s = _VAR_RE.sub("", s)
    s = _PLACEHOLDER_RE.sub("", s)
    s = _INPUT_TOK.sub("tilinpäätösdata", s)
    s = _ENRICH_TOK.sub("julkinen lähde", s)
    return s


def _esc(s):
    return html.escape(_clean(s))


def _short(v):
    """Cover figure for display: clean + drop a trailing parenthetical the model
    sometimes appends (e.g. '2 693 tEUR (realistinen base case)')."""
    if v is None or str(v).strip() == "":
        return ""
    return re.sub(r"\s*\([^)]*\)\s*$", "", _clean(str(v))).strip()


def _strip_tags(h):
    return re.sub(r"<[^>]+>", " ", h)


def _norm_ws(s):
    return re.sub(r"\s+", " ", html.unescape(s)).strip()


def _to_num(x):
    if isinstance(x, bool):
        return None
    if isinstance(x, (int, float)):
        return float(x)
    if isinstance(x, str):
        m = re.search(r"[−-]?\d[\d\s  ]*(?:[.,]\d+)?", x)
        if m:
            t = re.sub(r"[\s  ]", "", m.group(0)).replace("−", "-")
            t = t.replace(",", ".")
            try:
                return float(t)
            except ValueError:
                return None
    return None


def _fmt(n, decimals=0):
    if n is None:
        return "–"
    sign = "-" if n < 0 else ""
    a = abs(n)
    if decimals:
        whole = f"{a:,.{decimals}f}"
        intp, frac = whole.split(".")
        return f"{sign}{intp.replace(',', ' ')},{frac}"
    return f"{sign}{round(a):,.0f}".replace(",", " ")


def _fmt_teur(n):
    if n is None:
        return "–"
    if isinstance(n, str):
        return _clean(n)
    return f"{_fmt(n)} tEUR"


def _num_cell(v):
    """Render a table value, colouring positive growth green / negative red."""
    n = _to_num(v)
    txt = _esc(v)
    if n is not None and isinstance(v, str) and ("%" in v or v.strip().startswith(("+", "-", "−"))):
        cls = "neg" if n < 0 else ("pos" if v.strip().startswith("+") else "")
        if cls:
            return f'<span class="{cls}">{txt}</span>'
    if n is not None and n < 0:
        return f'<span class="neg">{txt}</span>'
    return txt


# --------------------------------------------------------------------------- #
# SVG charts (ported from the original lib/charts.js; pure SVG, no JS)
# --------------------------------------------------------------------------- #
def _nums(values):
    out = []
    for v in values or []:
        out.append(_to_num(v))
    return out


def _svg(vb_w, vb_h, inner):
    return (f'<svg viewBox="0 0 {vb_w} {vb_h}" width="100%" '
            f'preserveAspectRatio="xMidYMid meet" font-family="{SANS}" '
            f'xmlns="http://www.w3.org/2000/svg">{inner}</svg>')


def _nice_step(rng, ticks):
    raw = (rng or 1) / max(1, ticks)
    mag = 10 ** math.floor(math.log10(raw)) if raw > 0 else 1
    f = raw / mag
    nf = 1 if f <= 1 else 2 if f <= 2 else 2.5 if f <= 2.5 else 5 if f <= 5 else 10
    return nf * mag


def _nice_scale(dmin, dmax, ticks=4):
    lo, hi = min(0, dmin), max(0, dmax)
    if lo == hi:
        hi = lo + 1
    step = _nice_step(hi - lo, ticks)
    lo = math.floor(lo / step) * step
    hi = math.ceil(hi / step) * step
    count = max(1, round((hi - lo) / step))
    return lo, hi, step, count


def _nice_max(v):
    if v <= 0:
        return 1
    mag = 10 ** math.floor(math.log10(v))
    f = v / mag
    nf = 1 if f <= 1 else 2 if f <= 2 else 5 if f <= 5 else 10
    return nf * mag


def _grow(sc, ticks):
    lo, hi, step, count = sc
    while count < ticks:
        if lo < 0 and (hi <= 0 or -lo <= hi):
            lo -= step
        else:
            hi += step
        count += 1
    return lo, hi, step, count


def _svg_bars(labels, series, forecast_from=None):
    W, H = 600, 250
    pt, pr, pb, pl = 16, 14, 34, 42
    allv = [v for s in series for v in _nums(s.get("values")) if v is not None]
    if not allv or not labels:
        return ""
    lo, hi, step, count = _nice_scale(min(0, *allv), max(0, *allv))
    plotW, plotH = W - pl - pr, H - pt - pb

    def y(v):
        return pt + plotH * (1 - (v - lo) / (hi - lo))
    g = []
    if forecast_from is not None and forecast_from < len(labels):
        gx = pl + plotW * forecast_from / len(labels)
        g.append(f'<rect x="{gx:.1f}" y="{pt}" width="{pl + plotW - gx:.1f}" '
                 f'height="{plotH}" fill="{C["greenSoft"]}" opacity="0.5"/>')
        g.append(f'<line x1="{gx:.1f}" y1="{pt}" x2="{gx:.1f}" y2="{pt + plotH}" '
                 f'stroke="{C["lineStrong"]}" stroke-dasharray="3 3"/>')
    for i in range(count + 1):
        val = lo + (hi - lo) * i / count
        yy = y(val)
        g.append(f'<line x1="{pl}" y1="{yy:.1f}" x2="{W - pr}" y2="{yy:.1f}" '
                 f'stroke="{C["line"]}"/>')
        g.append(f'<text x="{pl - 6}" y="{yy + 3:.1f}" text-anchor="end" '
                 f'font-size="9" fill="{C["gray"]}">{_fmt(val).replace(" ", " ")}</text>')
    groupW = plotW / len(labels)
    bw = (groupW * 0.62) / max(1, len(series))
    palette = [C["lime"], C["green"], C["gray"]]
    for i, lab in enumerate(labels):
        gx = pl + groupW * i + groupW * 0.19
        for si, s in enumerate(series):
            vals = _nums(s.get("values"))
            v = vals[i] if i < len(vals) else None
            if v is None:
                continue
            yy, y0 = y(v), y(0)
            color = s.get("color") or palette[si % len(palette)]
            if forecast_from is not None and i >= forecast_from:
                color = C["greenLine"] if si else C["lime"]
            g.append(f'<rect x="{gx + bw * si:.1f}" y="{min(yy, y0):.1f}" '
                     f'width="{bw * 0.86:.1f}" height="{max(abs(yy - y0), 0.5):.1f}" '
                     f'fill="{color}"/>')
        g.append(f'<text x="{pl + groupW * i + groupW / 2:.1f}" y="{H - pb + 16}" '
                 f'text-anchor="middle" font-size="9.5" fill="{C["gray"]}">{_esc(lab)}</text>')
    g.append(f'<line x1="{pl}" y1="{y(0):.1f}" x2="{W - pr}" y2="{y(0):.1f}" '
             f'stroke="{C["lineStrong"]}" stroke-width="1.2"/>')
    return _svg(W, H, "".join(g))


def _svg_combo(labels, bar_vals, line_vals, line_pct=True, forecast_from=None):
    W, H = 600, 260
    pt, pr, pb, pl = 16, 44, 34, 42
    bv = [v for v in bar_vals if v is not None]
    lv = [v for v in line_vals if v is not None]
    if not bv or not labels:
        return _svg_bars(labels, [{"values": bar_vals}], forecast_from)
    bs = _nice_scale(min(0, *bv), max(0, *bv))
    ls = _nice_scale(min(0, *lv), max(0, *lv)) if lv else bs
    ticks = max(bs[3], ls[3])
    bs = _grow(bs, ticks)
    ls = _grow(ls, ticks)
    plotW, plotH = W - pl - pr, H - pt - pb

    def yb(v):
        return pt + plotH * (1 - (v - bs[0]) / (bs[1] - bs[0]))

    def yl(v):
        return pt + plotH * (1 - (v - ls[0]) / (ls[1] - ls[0]))
    groupW = plotW / len(labels)

    def xm(i):
        return pl + groupW * i + groupW / 2
    g = []
    if forecast_from is not None and forecast_from < len(labels):
        gx = pl + plotW * forecast_from / len(labels)
        g.append(f'<rect x="{gx:.1f}" y="{pt}" width="{pl + plotW - gx:.1f}" '
                 f'height="{plotH}" fill="{C["greenSoft"]}" opacity="0.5"/>')
        g.append(f'<line x1="{gx:.1f}" y1="{pt}" x2="{gx:.1f}" y2="{pt + plotH}" '
                 f'stroke="{C["lineStrong"]}" stroke-dasharray="3 3"/>')
        g.append(f'<text x="{gx + 4:.1f}" y="{pt + 9}" font-size="7.5" '
                 f'fill="{C["gray"]}">ennuste alkaa</text>')
    for i in range(ticks + 1):
        val = bs[0] + (bs[1] - bs[0]) * i / ticks
        yy = yb(val)
        g.append(f'<line x1="{pl}" y1="{yy:.1f}" x2="{W - pr}" y2="{yy:.1f}" stroke="{C["line"]}"/>')
        g.append(f'<text x="{pl - 6}" y="{yy + 3:.1f}" text-anchor="end" font-size="9" '
                 f'fill="{C["gray"]}">{_fmt(val)}</text>')
        rval = ls[0] + (ls[1] - ls[0]) * i / ticks
        g.append(f'<text x="{W - pr + 6}" y="{yy + 3:.1f}" text-anchor="start" font-size="9" '
                 f'fill="{C["limeDeep"]}">{_fmt(rval, 0)}{" %" if line_pct else ""}</text>')
    bw = groupW * 0.5
    for i, lab in enumerate(labels):
        v = bar_vals[i] if i < len(bar_vals) else None
        if v is not None:
            yy, y0 = yb(v), yb(0)
            col = C["greenLine"] if (forecast_from is not None and i >= forecast_from) else C["lime"]
            g.append(f'<rect x="{xm(i) - bw / 2:.1f}" y="{min(yy, y0):.1f}" width="{bw:.1f}" '
                     f'height="{max(abs(yy - y0), 0.5):.1f}" fill="{col}"/>')
        g.append(f'<text x="{xm(i):.1f}" y="{H - pb + 16}" text-anchor="middle" '
                 f'font-size="9.5" fill="{C["gray"]}">{_esc(lab)}</text>')
    g.append(f'<line x1="{pl}" y1="{yb(0):.1f}" x2="{W - pr}" y2="{yb(0):.1f}" '
             f'stroke="{C["lineStrong"]}" stroke-width="1.2"/>')
    pts = [f"{xm(i):.1f},{yl(v):.1f}" for i, v in enumerate(line_vals) if v is not None]
    if pts:
        g.append(f'<polyline points="{" ".join(pts)}" fill="none" '
                 f'stroke="{C["green"]}" stroke-width="2.6"/>')
        for i, v in enumerate(line_vals):
            if v is not None:
                g.append(f'<circle cx="{xm(i):.1f}" cy="{yl(v):.1f}" r="3.2" '
                         f'fill="#fff" stroke="{C["green"]}" stroke-width="1.6"/>')
    return _svg(W, H, "".join(g))


def _svg_hbars(items):
    """items: [{label, value, status, muted}]."""
    rowH, padL, padR, padT = 30, 160, 60, 8
    W = 600
    H = padT * 2 + rowH * max(1, len(items))
    vals = [it["value"] for it in items if isinstance(it.get("value"), (int, float))]
    vmax = _nice_max(max([1.0] + [v for v in vals if v > 0]))
    plotW = W - padL - padR
    g = []
    for i, it in enumerate(items):
        cy = padT + rowH * i + rowH / 2
        v = it.get("value")
        has = isinstance(v, (int, float))
        muted = it.get("muted") or not has
        g.append(f'<text x="{padL - 8}" y="{cy + 3:.1f}" text-anchor="end" font-size="9.5" '
                 f'fill="{C["gray"] if muted else C["ink"]}" font-weight="600">{_esc(it["label"])}</text>')
        if has and v > 0:
            bw = plotW * v / vmax
            g.append(f'<rect x="{padL}" y="{cy - 8:.1f}" width="{max(bw, 1):.1f}" height="16" '
                     f'fill="{C["lineStrong"] if muted else C["lime"]}"/>')
            g.append(f'<text x="{padL + bw + 6:.1f}" y="{cy + 3:.1f}" font-size="9.5" '
                     f'fill="{C["green"]}" font-family="{HEAD}" font-weight="700">{_fmt(v, 1)}</text>')
        else:
            g.append(f'<text x="{padL + 4}" y="{cy + 3:.1f}" font-size="8.5" '
                     f'fill="{C["gray"]}" font-style="italic">{_esc(it.get("status") or "hylätty")}</text>')
    return _svg(W, H, "".join(g))


def _svg_donut(segments):
    segs = [s for s in segments if (s.get("value") or 0) > 0]
    total = sum(s["value"] for s in segs) or 1
    cx, cy, ro, ri = 100, 100, 94, 54
    a0 = -math.pi / 2
    g = []
    for i, seg in enumerate(segs):
        frac = seg["value"] / total
        a1 = a0 + frac * 2 * math.pi
        large = 1 if frac > 0.5 else 0
        xo0, yo0 = cx + ro * math.cos(a0), cy + ro * math.sin(a0)
        xo1, yo1 = cx + ro * math.cos(a1), cy + ro * math.sin(a1)
        xi0, yi0 = cx + ri * math.cos(a0), cy + ri * math.sin(a0)
        xi1, yi1 = cx + ri * math.cos(a1), cy + ri * math.sin(a1)
        color = seg.get("color", SNAP_COLORS[i % len(SNAP_COLORS)])
        g.append(f'<path d="M{xo0:.2f} {yo0:.2f} A{ro} {ro} 0 {large} 1 {xo1:.2f} {yo1:.2f} '
                 f'L{xi1:.2f} {yi1:.2f} A{ri} {ri} 0 {large} 0 {xi0:.2f} {yi0:.2f} Z" fill="{color}"/>')
        if frac > 0.06:
            am = (a0 + a1) / 2
            lx, ly = cx + (ro + ri) / 2 * math.cos(am), cy + (ro + ri) / 2 * math.sin(am)
            tc = "#2E4B3C" if color == C["lime"] else "#fff"
            g.append(f'<text x="{lx:.2f}" y="{ly + 3.6:.2f}" fill="{tc}" font-size="11" '
                     f'text-anchor="middle" font-weight="700">{round(frac * 100)} %</text>')
        a0 = a1
    return _svg(200, 200, "".join(g))


def _heat_color(t):
    # t in [0,1]: 0 = red (low/neg), 0.5 = pale, 1 = green (high)
    if t < 0.5:
        k = t / 0.5
        r, g, b = 192, int(80 + 145 * k), int(77 + 150 * k)
    else:
        k = (t - 0.5) / 0.5
        r, g, b = int(231 - 65 * k), int(237 - 30 * k), int(216 - 159 * k)
    return f"rgb({r},{g},{b})"


def _svg_heatmap(x_axis, series):
    rows = [s for s in series if s.get("values")]
    if not rows or not x_axis:
        return ""
    allv = [v for s in rows for v in _nums(s.get("values")) if v is not None]
    if not allv:
        return ""
    vmin, vmax = min(allv), max(allv)
    span = (vmax - vmin) or 1
    cw = (600 - 130) / len(x_axis)
    ch = 34
    H = 30 + ch * len(rows) + 10
    g = []
    for ci, lab in enumerate(x_axis):
        g.append(f'<text x="{130 + cw * ci + cw / 2:.1f}" y="20" text-anchor="middle" '
                 f'font-size="9" font-weight="700" fill="{C["green"]}">{_esc(lab)}</text>')
    for ri, s in enumerate(rows):
        vals = _nums(s.get("values"))
        yy = 30 + ch * ri
        g.append(f'<text x="8" y="{yy + ch / 2 + 3:.1f}" font-size="9" '
                 f'font-weight="600" fill="{C["ink"]}">{_esc(s.get("name"))}</text>')
        for ci in range(len(x_axis)):
            v = vals[ci] if ci < len(vals) else None
            x = 130 + cw * ci
            fill = "#F2F3F1" if v is None else _heat_color((v - vmin) / span)
            g.append(f'<rect x="{x:.1f}" y="{yy:.1f}" width="{cw - 2:.1f}" height="{ch - 2}" '
                     f'rx="2" fill="{fill}"/>')
            if v is not None:
                tc = "#fff" if (v - vmin) / span < 0.28 else C["ink"]
                g.append(f'<text x="{x + cw / 2:.1f}" y="{yy + ch / 2 + 3:.1f}" text-anchor="middle" '
                         f'font-size="8.5" fill="{tc}">{_fmt(v, 1)}</text>')
    return _svg(600, H, "".join(g))


# --------------------------------------------------------------------------- #
# range bar + confidence pills (HTML/CSS, like the original)
# --------------------------------------------------------------------------- #
def _range_bar(low, high, mid, caption="Arvostusväli", caption_right=""):
    if low is None or high is None:
        return ""
    span = (high - low) or 1
    midpct = 50 if mid is None else max(0, min(100, (mid - low) / span * 100))
    mid_lab = (f'<div class="rb-lab mid" style="left:{midpct:.1f}%;">{_fmt(mid)}'
               f'<span class="lu"> tEUR</span></div>' if mid is not None else "")
    mid_tick = (f'<div class="rb-tick mid" style="left:{midpct:.1f}%;"></div>'
                if mid is not None else "")
    return (
        '<div class="rangebar">'
        f'<div class="rb-caption"><span>{_esc(caption)}</span>'
        f'<span>{_esc(caption_right)}</span></div>'
        '<div class="rb-track"><div class="rb-line"></div>'
        '<div class="rb-band" style="left:0%; right:0%;"></div>'
        '<div class="rb-tick end" style="left:0%;"></div>'
        f'<div class="rb-lab" style="left:0%;">{_fmt(low)}</div>'
        f'{mid_tick}{mid_lab}'
        '<div class="rb-tick end" style="left:100%;"></div>'
        f'<div class="rb-lab" style="left:100%;">{_fmt(high)}</div>'
        '</div></div>'
    )


_CONF_LEVELS = ["Matala", "Kohtalainen", "Korkea"]


def _conf_pills(level, note="", caption="Arvion luottamustaso"):
    if not level:
        return ""
    lv = str(level).strip().lower()
    colors = {"matala": C["red"], "kohtalainen": C["limeDeep"], "korkea": C["green"]}
    pills = []
    for L in _CONF_LEVELS:
        on = L.lower() == lv
        style = f' style="background:{colors.get(lv, C["green"])};border-color:{colors.get(lv, C["green"])};color:#fff"' if on else ""
        pills.append(f'<span{(" class=on" + style) if on else ""}>{L}</span>')
    note_html = f'<div class="conf-note">{_esc(note)}</div>' if note else ""
    return (f'<div class="cv-conf"><h4 class="blk">{_esc(caption)}</h4>'
            f'<div class="conf">{"".join(pills)}</div>{note_html}</div>')


# --------------------------------------------------------------------------- #
# derive snapshot data (range / confidence / methods / weights) from pipeline
# --------------------------------------------------------------------------- #
def _scenario_values(report):
    scen = (report.get("_scenarios") or {}).get("scenarios")
    if not isinstance(scen, list):
        return None
    out = []
    for s in scen:
        if isinstance(s, dict):
            out.append({"name": str(s.get("name", "")),
                        "value": _to_num(s.get("value_teur")),
                        "prob": _to_num(s.get("probability_pct"))})
    return out or None


def _derive(report):
    d = {}
    sc = report.get("_scenarios") or {}
    ev = _to_num(sc.get("expected_value_teur"))
    base = _to_num(sc.get("realistic_base_case_teur"))
    vals = _scenario_values(report)
    if vals:
        nums = [v["value"] for v in vals if v["value"] is not None]
        if nums:
            lo, hi = min(nums), max(nums)
            d["range"] = {"low": lo, "high": hi, "mid": ev if ev is not None else base}
        d["weights_donut"] = [
            {"value": v["prob"] or 0, "color": SNAP_COLORS[i % len(SNAP_COLORS)],
             "label": v["name"].capitalize()}
            for i, v in enumerate(vals) if (v["prob"] or 0) > 0]
    scoring = report.get("_scoring") or {}
    ms = scoring.get("method_scoring")
    if isinstance(ms, list) and ms:
        items, donut = [], []
        for i, m in enumerate(ms):
            if not isinstance(m, dict):
                continue
            status = str(m.get("status", "")).lower()
            val = _to_num(m.get("value_teur"))
            accepted = status.startswith("hyväks") or (m.get("weight_pct") or 0) > 0
            items.append({
                "label": _clean(m.get("method", "")),
                "value": val if accepted and val is not None and val > 0 else None,
                "status": "hylätty" if not accepted else None,
                "muted": not accepted,
            })
            w = _to_num(m.get("weight_pct"))
            if accepted and w and w > 0:
                donut.append({"value": w, "color": SNAP_COLORS[len(donut) % len(SNAP_COLORS)],
                              "label": _clean(m.get("method", ""))})
        d["methods"] = items
        if donut and "weights_donut" not in d:
            d["weights_donut"] = donut
    return d


# --------------------------------------------------------------------------- #
# blocks
# --------------------------------------------------------------------------- #
def _block_heading(b):
    return f'<h3 class="blk">{_esc(b.get("text"))}</h3>'


def _block_paragraph(b):
    return f'<p>{_esc(b.get("text"))}</p>'


def _callout_variant(v):
    return {"key": "reality", "warning": "kill", "info": "neutral"}.get(v, "neutral")


def _block_callout(b):
    variant = _callout_variant(b.get("variant", "info"))
    title = b.get("title")
    th = (f'<div class="co-t"><span class="co-badge"></span>{_esc(title)}</div>'
          if title else "")
    return f'<div class="callout {variant}">{th}<p>{_esc(b.get("text"))}</p></div>'


def _block_metric_cards(b):
    cards = [c for c in (b.get("cards") or []) if isinstance(c, dict)]
    n = max(1, min(len(cards), 4))
    cells = []
    for c in cards:
        accent = " accent" if c.get("emphasis") else ""
        cells.append(f'<div class="mcard{accent}"><div class="mval">{_esc(c.get("value"))}</div>'
                     f'<div class="mlabel">{_esc(c.get("label"))}</div></div>')
    return (f'<div class="mgrid" style="grid-template-columns:repeat({n},1fr);">'
            f'{"".join(cells)}</div>')


def _block_key_value(b):
    items = [it for it in (b.get("items") or []) if isinstance(it, dict)]
    title = b.get("title")
    rows = []
    for it in items:
        src = it.get("source")
        src_html = f' <span class="muted" style="font-size:7pt">({_esc(src)})</span>' if src else ""
        rows.append(f'<div class="kv"><span class="k">{_esc(it.get("key"))}{src_html}</span>'
                    f'<span class="v">{_esc(it.get("value"))}</span></div>')
    head = f'<h4 class="blk">{_esc(title)}</h4>' if title else ""
    return f'{head}{"".join(rows)}'


def _render_table(columns, rows, title=None, unit=None):
    cap = ""
    if title or unit:
        u = f' <span class="muted">({_esc(unit)})</span>' if unit else ""
        cap = f'<h4 class="blk">{_esc(title)}{u}</h4>'
    ths = "".join(f"<th>{_esc(c)}</th>" for c in (columns or []))
    trs = []
    for r in rows or []:
        cells = r if isinstance(r, list) else [r]
        tds = []
        for j, c in enumerate(cells):
            align = ' style="text-align:left"' if j == 0 else ""
            tds.append(f"<td{align}>{_num_cell(c)}</td>")
        trs.append("<tr>" + "".join(tds) + "</tr>")
    return (f'{cap}<table class="tbl"><thead><tr>{ths}</tr></thead>'
            f'<tbody>{"".join(trs)}</tbody></table>')


def _block_table(b):
    if b.get("status") == "not_available":
        reason = b.get("reason") or "Tietoa ei saatavilla."
        head = f'<h4 class="blk">{_esc(b.get("title"))}</h4>' if b.get("title") else ""
        return f'{head}<p class="muted" style="font-style:italic">{_esc(reason)}</p>'
    return _render_table(b.get("columns"), b.get("rows"), b.get("title"), b.get("unit"))


def _block_chart(b):
    if b.get("status") == "not_available":
        reason = b.get("reason") or "Kuvaajaa ei voitu muodostaa."
        head = f'<h4 class="blk">{_esc(b.get("title"))}</h4>' if b.get("title") else ""
        return f'{head}<p class="muted" style="font-style:italic">{_esc(reason)}</p>'
    title = b.get("title")
    head = f'<h4 class="blk">{_esc(title)}</h4>' if title else ""
    svg = _chart_svg(b)
    return f'<div class="chart-host">{head}{svg}</div>' if svg else head


def _chart_svg(b):
    ctype = b.get("chart_type", "bar")
    x = b.get("x_axis") or []
    series = [s for s in (b.get("series") or []) if isinstance(s, dict)]
    forecast = None
    for i, lab in enumerate(x):
        if "e" in str(lab).lower() and any(ch.isdigit() for ch in str(lab)):
            forecast = i
            break
    try:
        if ctype == "heatmap_or_matrix":
            return _svg_heatmap(x, series)
        if ctype == "bar_line":
            bar = next((s for s in series if s.get("type", "bar") == "bar"), None)
            line = next((s for s in series if s.get("type") == "line"), None)
            return _svg_combo(x, _nums(bar.get("values")) if bar else [],
                              _nums(line.get("values")) if line else [],
                              line_pct="%" in str(b.get("unit", "")) or True,
                              forecast_from=forecast)
        return _svg_bars(x, series, forecast_from=forecast)
    except Exception:
        return ""


def _block_scenario_table(b):
    name = b.get("scenario", "")
    cls = {"optimistinen": "pos", "pessimistinen": "neg"}.get(str(name).lower(), "")
    drivers = "".join(
        f'<div class="driver"><span class="dk">{_esc(d.get("key"))}</span>'
        f'<span class="dv">{_esc(d.get("value"))}</span></div>'
        for d in (b.get("drivers") or []) if isinstance(d, dict))
    peru = b.get("perusluvut") or {}
    avain = b.get("avainluvut") or {}
    return (
        f'<div class="scen scen-{_esc(str(name).lower())}">'
        f'<div class="scen-h"><span class="scen-name">{_esc(str(name).capitalize())}</span>'
        f'<span class="scen-fig"><span class="scen-val {cls}">{_fmt_teur(_to_num(b.get("value_teur")))}</span>'
        f'<span class="scen-p">p = {_fmt(_to_num(b.get("probability_pct")))} %</span></span></div>'
        f'<div class="drivers-strip"><div class="drivers-lab">Ajurit — näitä muuttamalla arvo muuttuu</div>'
        f'<div class="drivers-row">{drivers}</div></div>'
        f'<div class="scen-tables">{_render_table(peru.get("columns"), peru.get("rows"), "Perusluvut")}'
        f'{_render_table(avain.get("columns"), avain.get("rows"), "Avainluvut")}</div></div>'
    )


_BLOCKS = {
    "heading": _block_heading, "paragraph": _block_paragraph, "callout": _block_callout,
    "metric_cards": _block_metric_cards, "key_value": _block_key_value,
    "table": _block_table, "chart": _block_chart, "scenario_table": _block_scenario_table,
}


def _render_block(b):
    if not isinstance(b, dict):
        return ""
    fn = _BLOCKS.get(b.get("type"))
    return fn(b) if fn else ""


# --------------------------------------------------------------------------- #
# pages
# --------------------------------------------------------------------------- #
def _ordered_sections(report):
    secs = [s for s in (report.get("sections") or [])
            if isinstance(s, dict) and str(s.get("id")) in SECTION_ORDER]
    return sorted(secs, key=lambda s: SECTION_ORDER.index(str(s.get("id"))))


def _brandmark():
    return '<span class="brandmark"><i></i>Valuatum</span>'


def _header(report):
    meta = report.get("meta") or {}
    bits = [meta.get("company_name"), meta.get("y_tunnus"), meta.get("report_date")]
    right = " · ".join(_esc(x) for x in bits if x)
    return f'<div class="phead">{_brandmark()}<span>{right}</span></div>'


def _footer():
    return ('<div class="pfoot"><span>Valuatum · AI-Arvonmääritysraportti</span>'
            '<span class="pf-r"></span></div>')


def _cover(report, derived):
    cover = report.get("cover") or {}
    meta = report.get("meta") or {}
    hv = cover.get("headline_value")
    bcv = cover.get("base_case_value")
    label = cover.get("headline_label") or "Skenaarioiden odotusarvo"
    rng = derived.get("range")
    range_html = ""
    if rng:
        range_html = _range_bar(rng["low"], rng["high"], rng["mid"],
                                caption="Arvostusväli", caption_right="skenaariot")
    meta_bits = [f'Y-tunnus {_esc(meta.get("y_tunnus"))}' if meta.get("y_tunnus") else "",
                 _esc(meta.get("industry")),
                 f'{_esc(meta.get("report_date"))} · Valuatum Oy' if meta.get("report_date") else ""]
    meta_lines = "<br>".join(x for x in meta_bits if x)
    conf = report.get("confidence") or {}
    base_block = (f'<div><div class="cv-big" style="font-size:30pt">'
                  f'<span class="cap">Realistinen base case</span>{html.escape(_short(bcv))}</div></div>'
                  if bcv else "")
    return (
        '<section class="page cover">'
        f'<div class="cv-top">{_brandmark_cover()}<span class="cv-tag">Equity Research · AI</span></div>'
        '<div class="cv-mid">'
        '<div class="cv-kicker">AI-Arvonmääritysraportti</div>'
        f'<h1>{_esc(meta.get("company_name"))}</h1>'
        f'<div class="cv-meta">{meta_lines}</div></div>'
        '<div class="cv-headline">'
        f'<div class="cv-big"><span class="cap">{_esc(label)}</span>{html.escape(_short(hv))}</div>'
        f'{range_html}</div>'
        f'<div class="cv-headline" style="border-top:none;padding-top:14px;margin-top:14px">{base_block}</div>'
        f'{_conf_pills(conf.get("level"), conf.get("deciding_rule"))}'
        '</section>'
    )


def _brandmark_cover():
    return '<span class="cv-brand"><i></i>Valuatum</span>'


def _snapshot(report, derived):
    cover = report.get("cover") or {}
    conf = report.get("confidence") or {}
    dq = (report.get("data_quality") or {}).get("class")
    rng = derived.get("range")
    cards = [("Oman pääoman arvo (estimaatti)", cover.get("headline_value")),
             ("Realistinen base case", cover.get("base_case_value"))]
    if rng:
        cards.append(("Arvostusväli", f'{_fmt(rng["low"])}–{_fmt(rng["high"])} tEUR'))
    cards.append(("Arvion luottamustaso", conf.get("level") or "–"))
    card_html = "".join(
        f'<div class="mcard"><div class="mval" style="font-size:15pt">{_esc(v)}</div>'
        f'<div class="mlabel">{_esc(k)}</div></div>' for k, v in cards)
    body = [f'<div class="mgrid" style="grid-template-columns:repeat({len(cards)},1fr)">{card_html}</div>']
    if rng:
        body.append('<div style="margin-top:18px">'
                    + _range_bar(rng["low"], rng["high"], rng["mid"],
                                 caption="Arvostusväli — sama jana läpi raportin",
                                 caption_right=f'Painotettu arvo {_fmt(rng["mid"])} tEUR') + '</div>')
    donut = derived.get("weights_donut")
    methods = derived.get("methods")
    if donut or methods:
        left = (f'<div><h4 class="blk">Menetelmäpainot</h4><div style="max-width:150px">'
                f'{_svg_donut(donut)}</div></div>') if donut else "<div></div>"
        right = (f'<div><h4 class="blk">Menetelmien arvot</h4>{_svg_hbars(methods)}</div>'
                 if methods else "<div></div>")
        body.append(f'<div class="two-col" style="margin-top:16px;grid-template-columns:0.7fr 1.3fr">'
                    f'{left}{right}</div>')
    return (
        '<section class="page">'
        f'{_header(report)}'
        '<div class="pbody">'
        '<div class="sec-head"><span class="sec-num" style="background:var(--green);color:#fff">·</span>'
        '<div class="sh-t"><h2>Snapshot</h2><div class="sh-sub">Arvion tiivistetyt avainluvut</div></div></div>'
        '<div class="sec-rule"></div>'
        f'{"".join(body)}</div>{_footer()}</section>'
    )


def _toc(report, sections):
    rows = "".join(
        f'<div class="toc-row"><span class="tn">{_esc(s.get("id"))}</span>'
        f'<span class="tt">{_esc(s.get("title"))}</span>'
        '<span class="td"></span><span class="tp"></span></div>'
        for s in sections)
    return (
        '<section class="page">'
        f'{_header(report)}'
        '<div class="pbody">'
        '<div class="sec-head"><span class="sec-num" style="background:var(--green);color:#fff">·</span>'
        '<div class="sh-t"><h2>Sisällys</h2><div class="sh-sub">AI-Arvonmääritysraportti</div></div></div>'
        '<div class="sec-rule"></div>'
        f'<div class="toc">{rows}</div></div>{_footer()}</section>'
    )


def _section(report, sec):
    blocks = "".join(_render_block(b) for b in (sec.get("blocks") or []))
    return (
        '<section class="page report-section">'
        f'{_header(report)}'
        '<div class="pbody">'
        f'<div class="sec-head"><span class="sec-num">{_esc(sec.get("id"))}</span>'
        f'<div class="sh-t"><h2>{_esc(sec.get("title"))}</h2></div></div>'
        '<div class="sec-rule"></div>'
        f'{blocks}</div>{_footer()}</section>'
    )


# --------------------------------------------------------------------------- #
# cover guard + assembly
# --------------------------------------------------------------------------- #
def _cover_guard(report, derived):
    cover = report.get("cover") or {}
    text = _norm_ws(_strip_tags(_cover(report, derived)))
    missing = []
    for label, val in (("headline_value", cover.get("headline_value")),
                       ("base_case_value", cover.get("base_case_value"))):
        if val is None or str(val).strip() == "":
            missing.append(f"{label} puuttuu/tyhjä")
            continue
        if _norm_ws(_short(val)) not in text:
            missing.append(f"{label}={val!r}")
    if missing:
        raise CoverGuardError("Kannen luvut eivät renderöityneet eheinä: "
                              + "; ".join(missing) + " — kansiteksti: " + text[:300])


_FONT_CSS = None


def _font_style():
    global _FONT_CSS
    if _FONT_CSS is None:
        try:
            with open(_TEMPLATE, encoding="utf-8") as f:
                t = f.read()
            blocks = re.findall(r"<style[^>]*>(.*?)</style>", t, re.DOTALL)
            _FONT_CSS = next((b for b in blocks if "@font-face" in b), "")
        except Exception:
            _FONT_CSS = ""
    return _FONT_CSS


def _page_css(report):
    meta = report.get("meta") or {}
    foot = (str(meta.get("company_name") or "").replace("\\", "").replace('"', "")
            .replace("<", "").replace(">", ""))
    return f"""
@page {{ size: A4; margin: 16mm 15mm 14mm; }}
@page cover {{ margin: 0; }}
@media print {{ .phead, .pfoot {{ position: running(none); }} }}
"""


def render_html(report):
    if not isinstance(report, dict):
        raise ValueError("report ei ole objekti")
    derived = _derive(report)
    _cover_guard(report, derived)
    sections = _ordered_sections(report)
    body = (_cover(report, derived) + _snapshot(report, derived)
            + _toc(report, sections)
            + "".join(_section(report, s) for s in sections))
    meta = report.get("meta") or {}
    title = _esc(meta.get("company_name") or "AI-Arvonmääritysraportti")
    fonts = _font_style()
    font_block = (f"<style>{fonts}</style>" if fonts
                  else '<style>@import url("https://fonts.googleapis.com/css2?'
                       'family=Archivo:wght@400;600;700;800&'
                       'family=Source+Sans+3:wght@400;600;700&display=swap");</style>')
    return ("<!doctype html><html lang=\"fi\"><head><meta charset=\"utf-8\">"
            f"<title>{title}</title>{font_block}"
            f"<style>{_STATIC_CSS}{_page_css(report)}</style></head>"
            f"<body>{body}</body></html>")


def render_pdf(report, out_path):
    chrome = find_chrome()
    if not chrome:
        raise RuntimeError("Chrome/Chromium ei löytynyt — PDF-vienti ei käytettävissä.")
    html_str = render_html(report)
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    if os.path.exists(out_path):
        os.unlink(out_path)
    with tempfile.NamedTemporaryFile("w", suffix=".html", delete=False,
                                     encoding="utf-8", dir=os.path.dirname(out_path)) as f:
        f.write(html_str)
        html_path = f.name
    try:
        proc = subprocess.run(
            [chrome, "--headless=new", f"--print-to-pdf={out_path}",
             "--no-pdf-header-footer", "--virtual-time-budget=12000",
             "--no-sandbox", f"file://{html_path}"],
            capture_output=True, text=True, timeout=120)
        if proc.returncode != 0 or not os.path.exists(out_path):
            raise RuntimeError("PDF-renderöinti epäonnistui:\n" + (proc.stderr or "")[:2000])
    finally:
        try:
            os.unlink(html_path)
        except OSError:
            pass
    return out_path


_STATIC_CSS = """
:root{
  --bg:#FFFFFF; --ink:#1A1D1A; --lime:#A6CE39; --lime-deep:#8FB525;
  --green:#2E4B3C; --green-soft:#E7EDE8; --green-line:#C7D4CB; --red:#C0504D;
  --red-soft:#F6E7E6; --gray:#6B7280; --gray-soft:#F2F3F1; --line:#E1E4DE;
  --line-strong:#CBD0C9; --paper:#ECEDE7;
  --sans:"Source Sans 3", system-ui, sans-serif;
  --head:"Archivo", system-ui, sans-serif;
}
*{ box-sizing:border-box; }
html,body{ margin:0; padding:0; }
body{ background:#fff; color:var(--ink); font-family:var(--sans); font-size:9.6pt; line-height:1.5; }
.page{ position:relative; min-height:268mm; padding:0; page-break-after:always; display:flex; flex-direction:column; }
.report-section, .page{ page-break-inside:auto; }
.pbody{ flex:1 1 auto; padding-top:9px; }
.phead{ display:flex; justify-content:space-between; align-items:center; font-size:8pt; color:var(--gray);
  padding-bottom:8px; border-bottom:1px solid var(--line); font-variant-numeric:tabular-nums lining-nums; }
.brandmark{ display:flex; align-items:center; gap:6px; font-weight:700; color:var(--green); letter-spacing:.04em; font-family:var(--head); }
.brandmark i{ width:9px; height:9px; background:var(--lime); display:inline-block; }
.pfoot{ display:flex; justify-content:space-between; align-items:center; font-size:7.6pt; color:var(--gray);
  padding-top:7px; border-top:1px solid var(--line); margin-top:10px; }
h1,h2,h3,h4{ font-family:var(--head); color:var(--green); margin:0; line-height:1.12; }
p{ margin:0 0 7px; }
strong{ font-weight:700; color:var(--ink); }
.muted{ color:var(--gray); } .neg{ color:var(--red); font-weight:600; } .pos{ color:var(--lime-deep); font-weight:600; }
.sec-head{ display:flex; align-items:flex-start; gap:11px; margin:0 0 11px; }
.sec-num{ font-family:var(--head); font-weight:700; font-size:11pt; color:var(--green); background:var(--lime);
  width:26px; height:26px; flex:0 0 26px; display:flex; align-items:center; justify-content:center; }
.sec-head .sh-t{ flex:1 1 auto; }
.sec-head h2{ font-size:17pt; font-weight:700; letter-spacing:-.01em; }
.sec-head .sh-sub{ font-size:8pt; color:var(--gray); margin-top:3px; letter-spacing:.06em; text-transform:uppercase; font-weight:700; }
.sec-rule{ height:2px; background:var(--green); margin:0 0 13px; }
h3.blk{ font-size:10.5pt; font-weight:700; color:var(--green); margin:14px 0 6px; }
h4.blk{ font-size:8pt; font-weight:700; color:var(--gray); text-transform:uppercase; letter-spacing:.08em; margin:13px 0 6px; }
.mgrid{ display:grid; gap:8px; }
.mcard{ border:1px solid var(--line-strong); border-top:3px solid var(--green); padding:11px 12px; }
.mcard.accent{ border-top-color:var(--lime); }
.mcard .mval{ font-family:var(--head); font-weight:700; font-size:18pt; color:var(--green); line-height:1;
  font-variant-numeric:tabular-nums lining-nums; letter-spacing:-.01em; }
.mcard .mlabel{ font-size:7.6pt; color:var(--gray); margin-top:6px; line-height:1.25; }
.rangebar{ width:100%; }
.rb-caption{ display:flex; justify-content:space-between; font-size:7.6pt; color:var(--gray); margin-bottom:6px;
  font-weight:700; letter-spacing:.04em; text-transform:uppercase; }
.rb-track{ position:relative; height:50px; margin:20px 8px 30px; }
.rb-line{ position:absolute; top:24px; left:0; right:0; height:3px; background:var(--green-line); }
.rb-band{ position:absolute; top:23px; height:5px; background:var(--lime); opacity:.5; }
.rb-tick{ position:absolute; top:14px; width:1.5px; height:23px; background:var(--green); transform:translateX(-50%); }
.rb-tick.mid{ width:3px; height:34px; top:8px; }
.rb-tick.end{ background:var(--gray); }
.rb-lab{ position:absolute; top:40px; transform:translateX(-50%); font-size:8pt; font-weight:600; color:var(--gray);
  white-space:nowrap; font-variant-numeric:tabular-nums lining-nums; }
.rb-lab.mid{ top:-20px; font-family:var(--head); font-size:12pt; font-weight:700; color:var(--green); }
.rb-lab .lu{ font-size:7pt; color:var(--gray); font-weight:600; }
.cv-conf{ margin-top:14px; }
.conf{ display:inline-flex; border:1px solid var(--line-strong); }
.conf span{ font-size:8pt; font-weight:600; padding:4px 13px; color:var(--gray); border-right:1px solid var(--line-strong); }
.conf span:last-child{ border-right:none; }
.conf-note{ font-size:7.6pt; color:var(--gray); margin-top:6px; max-width:150mm; }
.callout{ padding:11px 14px; margin:12px 0; background:#fff; page-break-inside:avoid; }
.callout .co-t{ font-family:var(--head); font-weight:700; font-size:9.5pt; margin-bottom:5px; display:flex; align-items:center; gap:7px; }
.callout .co-badge{ width:9px; height:9px; display:inline-block; }
.callout.kill{ border-left:4px solid var(--red); background:var(--red-soft); }
.callout.kill .co-t, .callout.kill .co-badge{ color:var(--red); background:initial; } .callout.kill .co-badge{ background:var(--red); }
.callout.reality{ border:2px solid var(--green); background:var(--green-soft); }
.callout.reality .co-t{ color:var(--green); } .callout.reality .co-badge{ background:var(--green); }
.callout.neutral{ border:1px solid var(--line-strong); border-left:4px solid var(--gray); background:var(--gray-soft); }
.callout.neutral .co-badge{ background:var(--gray); }
table.tbl{ width:100%; border-collapse:collapse; font-size:8.4pt; margin:6px 0 10px; }
table.tbl th, table.tbl td{ padding:4.5px 7px; text-align:right; border-bottom:1px solid var(--line);
  font-variant-numeric:tabular-nums lining-nums; }
table.tbl thead th{ color:var(--green); font-weight:700; border-bottom:1.5px solid var(--green);
  font-family:var(--head); font-size:7.8pt; text-align:right; }
table.tbl thead th:first-child{ text-align:left; }
table.tbl tbody tr:nth-child(even) td{ background:#FAFBFA; }
.kv{ display:flex; justify-content:space-between; gap:10px; padding:3.5px 0; border-bottom:1px solid var(--line);
  font-size:8.6pt; align-items:baseline; }
.kv .k{ color:var(--gray); flex:1 1 auto; }
.kv .v{ font-variant-numeric:tabular-nums lining-nums; font-weight:600; white-space:nowrap; }
.chart-host{ width:100%; margin:8px 0 12px; page-break-inside:avoid; }
.two-col{ display:grid; grid-template-columns:1fr 1fr; gap:16px; align-items:start; }
.toc{ font-size:9.8pt; }
.toc-row{ display:flex; align-items:baseline; gap:8px; padding:7px 0; border-bottom:1px solid var(--line); }
.toc-row .tn{ font-family:var(--head); font-weight:700; color:var(--lime-deep); width:24px; flex:0 0 24px; }
.toc-row .tt{ color:var(--ink); font-weight:600; }
.toc-row .td{ flex:1 1 auto; border-bottom:1px dotted var(--line-strong); margin:0 4px 3px; }

/* cover */
.cover{ padding:24mm 22mm; justify-content:flex-start; min-height:297mm; }
.cover .cv-top{ display:flex; justify-content:space-between; align-items:center; border-bottom:1px solid var(--line); padding-bottom:10px; }
.cv-brand{ display:flex; align-items:center; gap:9px; font-family:var(--head); font-weight:700; font-size:13pt; color:var(--green); letter-spacing:.02em; }
.cv-brand i{ width:16px; height:16px; background:var(--lime); display:inline-block; }
.cv-tag{ font-size:8pt; color:var(--gray); letter-spacing:.14em; text-transform:uppercase; font-weight:700; }
.cv-mid{ margin-top:40px; }
.cv-kicker{ font-size:9pt; letter-spacing:.22em; text-transform:uppercase; color:var(--lime-deep); font-weight:700; }
.cover h1{ font-size:44pt; font-weight:800; letter-spacing:-.02em; line-height:1.02; margin:12px 0 0; color:var(--green); }
.cover .cv-meta{ margin-top:18px; font-size:10pt; color:var(--gray); line-height:1.7; }
.cv-headline{ margin-top:34px; border-top:2px solid var(--green); border-bottom:1px solid var(--line);
  padding:20px 0 10px; display:grid; grid-template-columns:auto 1fr; gap:36px; align-items:end; }
.cv-big{ font-family:var(--head); font-weight:800; font-size:50pt; color:var(--green); line-height:.92;
  font-variant-numeric:tabular-nums lining-nums; letter-spacing:-.02em; white-space:nowrap; }
.cv-big .cap{ display:block; font-family:var(--sans); font-size:8.5pt; font-weight:700; color:var(--gray);
  text-transform:uppercase; letter-spacing:.08em; margin-bottom:8px; white-space:normal; }

/* scenario panels */
.scen{ border:1px solid var(--line-strong); border-top:3px solid var(--green); padding:11px 13px; margin:11px 0; page-break-inside:avoid; }
.scen-optimistinen{ border-top-color:var(--lime); } .scen-pessimistinen{ border-top-color:var(--red); }
.scen-h{ display:flex; justify-content:space-between; align-items:baseline; }
.scen-name{ font-family:var(--head); font-size:12pt; font-weight:700; color:var(--green); }
.scen-fig{ display:flex; gap:12px; align-items:baseline; }
.scen-val{ font-family:var(--head); font-size:13pt; font-weight:700; color:var(--green); }
.scen-p{ font-size:9pt; color:var(--gray); }
.drivers-strip{ margin:9px 0; background:var(--green-soft); padding:9px 10px; }
.drivers-lab{ font-size:7.4pt; text-transform:uppercase; letter-spacing:.06em; color:var(--green); font-weight:700; margin-bottom:6px; }
.drivers-row{ display:flex; flex-wrap:wrap; gap:8px; }
.driver{ display:flex; flex-direction:column; background:#fff; border:1px solid var(--green-line); padding:5px 8px; min-width:26mm; }
.driver .dk{ font-size:7pt; color:var(--gray); } .driver .dv{ font-family:var(--head); font-size:10pt; font-weight:700; color:var(--green); }
.scen-tables{ display:grid; grid-template-columns:1fr; gap:2px; }
"""
