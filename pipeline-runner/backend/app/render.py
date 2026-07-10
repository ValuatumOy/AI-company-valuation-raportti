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
from contextvars import ContextVar
import shutil
import subprocess
import tempfile

from .runner import APPENDIX_SECTION_IDS, SECTION_ORDER

REPORTS_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "_reports"))
_CHROME_CANDIDATES = [
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    "/Applications/Chromium.app/Contents/MacOS/Chromium",
    "google-chrome", "chromium", "chromium-browser",
    "C:/Program Files/Google/Chrome/Application/chrome.exe",
    "C:/Program Files (x86)/Google/Chrome/Application/chrome.exe",
]

# Brand palette 2026-07: primary #12352B (Valuatum's new brand green); the
# accent is a tonal sage green — no second hue, everything stays in the brand
# family. "lime"/"limeDeep" keys kept so callers don't churn — they now hold
# the sage accent.
C = {
    "ink": "#1A1D1A", "lime": "#4F7A6A", "limeDeep": "#33604F", "green": "#12352B",
    "greenSoft": "#E8EEEA", "greenLine": "#C3D2C9", "red": "#C0504D",
    "redSoft": "#F6E7E6", "gray": "#6B7280", "line": "#E1E4DE",
    "lineStrong": "#CBD0C9",
}
# Gelasio is metric-compatible with Georgia — the PDF container has no
# Georgia, so the imported webfont keeps print identical to browser view.
HEAD = "Georgia, Gelasio, 'Times New Roman', serif"
SANS = ("-apple-system, 'Segoe UI', Roboto, 'Helvetica Neue', system-ui, "
        "sans-serif")
SNAP_COLORS = ["#4F7A6A", "#12352B", "#6B7280", "#7FA391"]


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
# Capture any trailing Finnish case suffix (input_datassa, input_datan, ...) so
# the inflected token doesn't slip past a \b boundary. The suffix carries over to
# the replacement noun, which also ends in -data, so it stays grammatical.
_INPUT_TOK = re.compile(r"\[?\binput[_ ]?data([a-zäöå]*)\]?", re.IGNORECASE)
_ENRICH_TOK = re.compile(r"\[?\benrichment[a-zäöå]*\]?", re.IGNORECASE)
# Raw schema field names the model quotes from its instructions/context
# ("market_signals ja client_reported_signals ovat tyhjät", "(tukee_kasvua)").
# Each maps to reader-facing Finnish; inflections are handled by matching the
# stem and letting the replacement stand alone.
_SCHEMA_TOKENS = [
    (re.compile(r"\(?\btukee[_ ]kasvua\b\)?", re.I), "(tukee kasvua)"),
    (re.compile(r"\(?\brajoittaa[_ ]kasvua\b\)?", re.I), "(rajoittaa kasvua)"),
    (re.compile(r"\bclient[_ ]reported[_ ]signals\b[a-zäöå]*", re.I), "asiakkaan ilmoittamat signaalit"),
    (re.compile(r"\bmarket[_ ]signals\b[a-zäöå]*", re.I), "markkinasignaalit"),
    (re.compile(r"\brevenue[_ ]anomaly[_ ]review\b[a-zäöå]*", re.I), "liikevaihtopoikkeaman tarkistus"),
    (re.compile(r"\bsource[_ ]register\b[a-zäöå]*", re.I), "lähderekisteri"),
    (re.compile(r"\bno[_ ]of[_ ]shares[_ ]total\b", re.I), "osakemäärä"),
    (re.compile(r"\bfair[_ ]value[_ ]dcf\b", re.I), "osakekohtainen DCF-arvo"),
    (re.compile(r"\bbusiness[_ ]profile\b[a-zäöå]*", re.I), "liiketoimintaprofiili"),
    (re.compile(r"\bgrowth[_ ]assessment\b[a-zäöå]*", re.I), "kasvuarvio"),
    (re.compile(r"\bvaluation[_ ]engine\b[a-zäöå]*", re.I), "arvonmääritysmoottori"),
    (re.compile(r"\bkey[_ ]ratios\b[a-zäöå]*", re.I), "tunnusluvut"),
    (re.compile(r"\bcredit[_ ]risk\b[a-zäöå]*", re.I), "luottoriskitiedot"),
    (re.compile(r"\buser[_ ]input\b[a-zäöå]*", re.I), "käyttäjän antamat lisätiedot"),
]


def _flat_text(v):
    """Coerce a text-ish field the model may emit as a list/dict/number (instead
    of a string) into a readable string — so it never renders as a raw '[...]' or
    '{...}' dump. Mirrors the table-row robustness for every free-text field."""
    if v is None:
        return ""
    if isinstance(v, str):
        return v
    if isinstance(v, list):
        return " ".join(p for p in (_flat_text(x) for x in v) if p)
    if isinstance(v, dict):
        for k in ("text", "value", "arvo", "label", "content", "teksti"):
            if k in v:
                return _flat_text(v[k])
        return " ".join(p for p in (_flat_text(x) for x in v.values()) if p)
    return str(v)   # int/float/bool -> exact prior scalar behavior (no reformatting)


_CITE_TAG = re.compile(r"</?cite\b[^>]*>", re.I)


def _clean(s):
    """Strip leaked pipeline tokens; the reader must never see [input_data]."""
    if s is None:
        return ""
    if not isinstance(s, str):
        s = _flat_text(s)   # lists/dicts/numbers -> readable string, never a dump
    # Web-search plugin citation markup (<cite index="25-1">…</cite>) leaks into
    # prose when a stage uses live web search; the reader must never see it.
    s = _CITE_TAG.sub("", s)
    s = _VAR_RE.sub("", s)
    s = _PLACEHOLDER_RE.sub("", s)
    s = _INPUT_TOK.sub(lambda m: "tilinpäätösdata" + m.group(1), s)
    s = _ENRICH_TOK.sub("julkinen lähde", s)
    for pat, repl in _SCHEMA_TOKENS:
        s = pat.sub(repl, s)
    return s


def _esc(s):
    return html.escape(_clean(s))


_MD_BOLD = re.compile(r"\*\*([^*\n]+)\*\*")
_MD_ITALIC = re.compile(r"(?<![\*\w])\*([^*\n]+)\*(?!\w)")

# Prose carries source citations as bare "(lähde: domain.fi, 2026-06-30)" text
# (see prompts rule 11) while the §15 source-register table holds the matching
# full URL. Readers had to hunt down the table to click through — this makes
# the inline citation itself a link, using the domain map built once per
# render_html() call (ContextVar, not a module global, so concurrent renders
# in different request threads never cross-contaminate each other's sources).
_source_domain_map: ContextVar[dict] = ContextVar("_source_domain_map", default={})
_SOURCE_CITE_RE = re.compile(r"\(lähde:\s*([a-zA-Z0-9][\w.-]*\.[a-zA-Z]{2,})((?:\s*,\s*[^)]*)?)\)")


def _linkify_citation(m):
    domain, rest = m.group(1), m.group(2)
    url = _source_domain_map.get().get(domain.lower())
    if not url:
        return m.group(0)
    href = html.escape(url, quote=True)
    return f'(lähde: <a class="src" href="{href}">{domain}</a>{rest})'


def _inline(s):
    """Escape, then render the markdown emphasis the prompt contract promises
    (**lihava**, *kursiivi*) — otherwise raw asterisks reach the client PDF.
    Also turns an inline "(lähde: domain, pvm)" citation into a clickable link
    when that domain's full URL is known from elsewhere in the report."""
    t = _esc(s)
    t = _MD_BOLD.sub(r"<strong>\1</strong>", t)
    t = _MD_ITALIC.sub(r"<em>\1</em>", t)
    t = _SOURCE_CITE_RE.sub(_linkify_citation, t)
    return t


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


# A report renders its *headline* monetary figures in one unit chosen from the
# largest figure on the cover: tEUR up to ~10 M€, then M€, then mrd. €. Without
# this a large-cap report prints a seven-digit tEUR number on the 50pt cover
# ("5 100 000 tEUR"), which reads as broken. Detail tables stay in tEUR.
_PURE_TEUR_RE = re.compile(r"^\s*(-?\d[\d\s  ]*(?:[.,]\d+)?)\s*tEUR\s*$", re.I)


def _scale_from_teur(teur):
    """(divisor, unit_label, decimals) for a tEUR magnitude."""
    a = abs(teur or 0)
    if a >= 1_000_000:
        return 1_000_000.0, "mrd. €", 2
    if a >= 10_000:
        return 1_000.0, "M€", 1
    return 1.0, "tEUR", 0


def _report_scale(report, derived):
    """One headline unit for the cover's primary valuation figure.

    Scenario expected value may be much larger than the base case; letting that
    drive the cover unit makes the primary value look like a peer figure to the
    scenario output. Keep the cover scale anchored to the realistic base case.
    """
    cand = []
    sc = report.get("_scenarios") or {}
    v = _to_num(sc.get("realistic_base_case_teur"))
    if v is not None:
        cand.append(v)
    cover = report.get("cover") or {}
    if cover.get("base_case_value") not in (None, ""):
        v = _to_num(_short(cover.get("base_case_value")))
        if v is not None:
            cand.append(v)
    elif cover.get("headline_value") not in (None, ""):
        v = _to_num(_short(cover.get("headline_value")))
        if v is not None:
            cand.append(v)
    return _scale_from_teur(max((abs(c) for c in cand), default=0))


def _fmt_scaled(teur, scale):
    div, unit, dec = scale
    if teur is None:
        return "–"
    return f"{_fmt(teur / div, dec)} {unit}"


def _scaled_cover_str(s, scale):
    """Re-express a single pure '<n> tEUR' cover string in the report unit. Any
    other string (range, prose, non-numeric) is returned cleaned but unscaled."""
    div, unit, dec = scale
    cleaned = _short(s)
    if div == 1.0:
        return cleaned
    m = _PURE_TEUR_RE.match(cleaned)
    if not m:
        return cleaned
    n = _to_num(m.group(1))
    return f"{_fmt(n / div, dec)} {unit}" if n is not None else cleaned


def _display_industry(meta):
    """Cover-safe industry label.

    Stage output can contain an analysis note such as
    "Ei tiedossa (input-datassa industry_code puuttuu; julkinen lähde mukaan ...)".
    That is a data-quality caveat, not a cover line. Prefer the source-backed
    industry embedded in the note; otherwise omit the cover industry.
    """
    s = _clean((meta or {}).get("industry")).strip()
    if not s:
        return ""
    low = s.lower()
    bad_markers = ("ei tiedossa", "input-data", "input_data", "industry_code", "puutt")
    if any(m in low for m in bad_markers):
        without_caveat = re.sub(
            r"\s*\([^)]*(?:input[-_ ]?data|industry_code|puutt)[^)]*\)",
            "",
            s,
            flags=re.IGNORECASE,
        ).strip(" .;")
        if without_caveat and "ei tiedossa" not in without_caveat.lower():
            return without_caveat
        m = re.search(r"mukaan\s+([^);.]+)", s, flags=re.IGNORECASE)
        if m:
            candidate = m.group(1).strip(" .;)")
            return candidate[:1].upper() + candidate[1:] if candidate else ""
        return ""
    return s


_URL_CELL_RE = re.compile(r"^\s*https?://(?:www\.)?([^/\s]+)(?:/\S*)?\s*$", re.I)


def _source_inline(v):
    if not v:
        return ""
    if isinstance(v, str):
        m = _URL_CELL_RE.match(v)
        if m:
            href = html.escape(v.strip(), quote=True)
            return f'<a class="src" href="{href}">{_esc(m.group(1))}</a>'
    return _esc(v)


def _fmt_raw_number(v):
    """Finnish-format a raw numeric JSON value for a table cell. LLM stages
    sometimes emit full-precision engine floats (4289677.53181) which read as
    US-formatted garbage in a Finnish PDF. Ints that look like years (1900-2100)
    pass through untouched — '2 026' as a column value would be worse."""
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return None
    if isinstance(v, int) and 1900 <= v <= 2100:
        return None
    if abs(v) >= 1000:
        return _fmt(float(round(v)))
    r = round(float(v), 2)
    if r == int(r):
        return str(int(r))
    return f"{r}".replace(".", ",")


def _num_cell(v):
    """Render a table value, colouring positive growth green / negative red."""
    # A bare URL in a source column reads as scraped data in a client PDF; show
    # just the domain (e.g. ytj.fi) as the visible text, but keep it a clickable
    # link to the full source so the reader can verify the claim ("verify me").
    if isinstance(v, str):
        m = _URL_CELL_RE.match(v)
        if m:
            href = html.escape(v.strip(), quote=True)
            return f'<a class="src" href="{href}">{_esc(m.group(1))}</a>'
    n = _to_num(v)
    formatted = _fmt_raw_number(v)
    txt = _esc(formatted) if formatted is not None else _esc(v)
    # Colour only cells that ARE figures — a prose sentence that merely contains
    # a negative number ("...FCFF n. −112 tEUR; arvo muodostuu...") must not
    # turn red wholesale.
    numish = bool(_cell_numish(v)) if isinstance(v, str) else True
    if numish and n is not None and isinstance(v, str) and (
            "%" in v or v.strip().startswith(("+", "-", "−"))):
        cls = "neg" if n < 0 else ("pos" if v.strip().startswith("+") else "")
        if cls:
            return f'<span class="{cls}">{txt}</span>'
    if numish and n is not None and n < 0:
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
    # Legend for multi-series charts — without it "kassa vs velat" bars are
    # unreadable (which color is which?).
    if len(series) > 1:
        lx = pl
        for si, s in enumerate(series):
            nm = _esc(_flat_text(s.get("name")) or f"Sarja {si + 1}")
            color = s.get("color") or palette[si % len(palette)]
            g.append(f'<rect x="{lx:.1f}" y="3" width="8" height="8" fill="{color}"/>')
            g.append(f'<text x="{lx + 11:.1f}" y="11" font-size="8.5" '
                     f'fill="{C["gray"]}">{nm}</text>')
            lx += 11 + 5.2 * len(nm) + 16
    return _svg(W, H, "".join(g))


def _axis_vals(vals):
    """Values used for AXIS bounds: drop up to 2 extreme outliers (beyond
    median ± 6×MAD) so one freak point doesn't flatten the whole series."""
    if len(vals) < 4:
        return vals
    sv = sorted(vals)
    med = sv[len(sv) // 2]
    mad = sorted(abs(v - med) for v in vals)[len(vals) // 2] or 1e-9
    kept = [v for v in vals if abs(v - med) <= 6 * mad]
    return kept if len(kept) >= len(vals) - 2 and len(kept) >= 2 else vals


def _svg_combo(labels, bar_vals, line_vals, line_pct=True, forecast_from=None,
               bar_name=None, line_name=None):
    W, H = 600, 260
    pt, pr, pb, pl = 16, 44, 34, 42
    bv = [v for v in bar_vals if v is not None]
    lv = [v for v in line_vals if v is not None]
    if not bv or not labels:
        return _svg_bars(labels, [{"values": bar_vals}], forecast_from)
    bs = _nice_scale(min(0, *bv), max(0, *bv))
    # Axis from outlier-robust line values: one launch-year freak (-775 % EBIT)
    # otherwise stretches the right axis so far every later year reads flat.
    # Outliers stay in the plot, clamped to the axis edge as open markers.
    la = _axis_vals(lv)
    ls = _nice_scale(min(0, *la), max(0, *la)) if la else bs
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
    def ylc(v):
        return max(pt, min(pt + plotH, yl(v)))  # clamp off-axis outliers to the edge

    pts = [f"{xm(i):.1f},{ylc(v):.1f}" for i, v in enumerate(line_vals) if v is not None]
    if pts:
        g.append(f'<polyline points="{" ".join(pts)}" fill="none" '
                 f'stroke="{C["green"]}" stroke-width="2.6"/>')
        for i, v in enumerate(line_vals):
            if v is not None:
                clamped = abs(ylc(v) - yl(v)) > 0.01
                stroke_extra = ' stroke-dasharray="2 2"' if clamped else ""
                g.append(f'<circle cx="{xm(i):.1f}" cy="{ylc(v):.1f}" r="3.2" '
                         f'fill="#fff" stroke="{C["green"]}" stroke-width="1.6"{stroke_extra}/>')
    if bar_name or line_name:
        lx = pl
        for nm_raw, color in ((bar_name, C["lime"]), (line_name, C["green"])):
            if not nm_raw:
                continue
            nm = _esc(_flat_text(nm_raw))
            g.append(f'<rect x="{lx:.1f}" y="3" width="8" height="8" fill="{color}"/>')
            g.append(f'<text x="{lx + 11:.1f}" y="11" font-size="8.5" '
                     f'fill="{C["gray"]}">{nm}</text>')
            lx += 11 + 5.2 * len(nm) + 16
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
            tc = "#fff"  # sage/green/gray fills all take white labels
            g.append(f'<text x="{lx:.2f}" y="{ly + 3.6:.2f}" fill="{tc}" font-size="11" '
                     f'text-anchor="middle" font-weight="700">{round(frac * 100)} %</text>')
        a0 = a1
    return _svg(200, 200, "".join(g))


def _heat_color(t):
    # t in [0,1]: 0 = brand red (low/neg), 0.5 = pale neutral, 1 = sage green
    # (high). Endpoints from the 2026-07 brand palette — the old lime top end
    # (#A6CF39) clashed with the #12352B primary.
    if t < 0.5:
        k = t / 0.5  # C["red"] -> pale
        r, g, b = int(192 + 40 * k), int(80 + 158 * k), int(77 + 157 * k)
    else:
        k = (t - 0.5) / 0.5  # pale -> C["lime"] (sage #4F7A6A)
        r, g, b = int(232 - 153 * k), int(238 - 116 * k), int(234 - 128 * k)
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
                t = (v - vmin) / span
                tc = "#fff" if (t < 0.28 or t > 0.82) else C["ink"]
                dec = 0 if v == round(v) else 1
                g.append(f'<text x="{x + cw / 2:.1f}" y="{yy + ch / 2 + 3:.1f}" text-anchor="middle" '
                         f'font-size="8.5" fill="{tc}">{_fmt(v, dec)}</text>')
    return _svg(600, H, "".join(g))


def _svg_waterfall(steps):
    """Bridge/waterfall chart: a 'start' step draws full-height from zero, a
    'delta' step floats between the running total and the running total + its
    (signed) value, a 'total' step draws full-height at its own value. Used for
    the EV -> oma pääoman arvo bridge (deterministic_ev_equity_waterfall)."""
    steps = [s for s in (steps or []) if isinstance(s, dict)
             and isinstance(s.get("value"), (int, float)) and not isinstance(s.get("value"), bool)]
    if not steps:
        return ""
    W, H = 600, 250
    pt, pr, pb, pl = 16, 14, 34, 42
    running = 0.0
    tops, bottoms = [], []
    for s in steps:
        kind = s.get("kind")
        v = s["value"]
        if kind == "delta":
            lo, hi = (running, running + v) if v >= 0 else (running + v, running)
            running += v
        else:
            lo, hi = 0.0, v
            running = v
        bottoms.append(lo)
        tops.append(hi)
    allv = tops + bottoms + [0.0]
    lo_sc, hi_sc, step, count = _nice_scale(min(allv), max(allv))
    plotW, plotH = W - pl - pr, H - pt - pb

    def y(v):
        return pt + plotH * (1 - (v - lo_sc) / (hi_sc - lo_sc))

    g = []
    for i in range(count + 1):
        val = lo_sc + (hi_sc - lo_sc) * i / count
        yy = y(val)
        g.append(f'<line x1="{pl}" y1="{yy:.1f}" x2="{W - pr}" y2="{yy:.1f}" '
                 f'stroke="{C["line"]}"/>')
        g.append(f'<text x="{pl - 6}" y="{yy + 3:.1f}" text-anchor="end" '
                 f'font-size="9" fill="{C["gray"]}">{_fmt(val).replace(" ", " ")}</text>')
    n = len(steps)
    groupW = plotW / n
    bw = groupW * 0.56
    for i, s in enumerate(steps):
        kind = s.get("kind")
        v = s["value"]
        lo_v, hi_v = bottoms[i], tops[i]
        gx = pl + groupW * i + (groupW - bw) / 2
        yy, y0 = y(hi_v), y(lo_v)
        if kind == "total":
            color = C["green"]
        elif kind == "start":
            color = C["greenLine"]
        else:
            color = C["lime"] if v >= 0 else C["red"]
        g.append(f'<rect x="{gx:.1f}" y="{min(yy, y0):.1f}" width="{bw:.1f}" '
                 f'height="{max(abs(yy - y0), 1.5):.1f}" fill="{color}"/>')
        if i > 0 and kind != "start":
            px = pl + groupW * (i - 1) + (groupW - bw) / 2 + bw
            py = y(bottoms[i - 1] if steps[i - 1].get("kind") == "delta" and steps[i - 1]["value"] < 0
                   else tops[i - 1])
            g.append(f'<line x1="{px:.1f}" y1="{py:.1f}" x2="{gx:.1f}" y2="{py:.1f}" '
                     f'stroke="{C["lineStrong"]}" stroke-dasharray="3 3"/>')
        lab_y = min(yy, y0) - 6 if v >= 0 or kind != "delta" else max(yy, y0) + 14
        sign = "+" if (kind == "delta" and v > 0) else ""
        g.append(f'<text x="{gx + bw / 2:.1f}" y="{lab_y:.1f}" text-anchor="middle" '
                 f'font-size="9" font-weight="700" fill="{C["ink"]}">{sign}{_fmt(v)}</text>')
        g.append(f'<text x="{pl + groupW * i + groupW / 2:.1f}" y="{H - pb + 16}" '
                 f'text-anchor="middle" font-size="9" fill="{C["gray"]}">{_esc(s.get("label"))}</text>')
    g.append(f'<line x1="{pl}" y1="{y(0):.1f}" x2="{W - pr}" y2="{y(0):.1f}" '
             f'stroke="{C["lineStrong"]}" stroke-width="1.2"/>')
    return _svg(W, H, "".join(g))


# --------------------------------------------------------------------------- #
# range bar + confidence pills (HTML/CSS, like the original)
# --------------------------------------------------------------------------- #
def _range_bar(low, high, mid, caption="Arvostusväli", caption_right="", scale=None):
    if low is None or high is None:
        return ""
    div, unit, dec = scale or (1.0, "tEUR", 0)
    span = (high - low) or 1  # ratios from raw values; only labels are scaled
    midpct = 50 if mid is None else max(0, min(100, (mid - low) / span * 100))
    mid_lab = (f'<div class="rb-lab mid" style="left:{midpct:.1f}%;">{_fmt(mid / div, dec)}'
               f'<span class="lu"> {unit}</span></div>' if mid is not None else "")
    mid_tick = (f'<div class="rb-tick mid" style="left:{midpct:.1f}%;"></div>'
                if mid is not None else "")
    return (
        '<div class="rangebar">'
        f'<div class="rb-caption"><span>{_esc(caption)}</span>'
        f'<span>{_esc(caption_right)}</span></div>'
        '<div class="rb-track"><div class="rb-line"></div>'
        '<div class="rb-band" style="left:0%; right:0%;"></div>'
        '<div class="rb-tick end" style="left:0%;"></div>'
        f'<div class="rb-lab" style="left:0%;">{_fmt(low / div, dec)}</div>'
        f'{mid_tick}{mid_lab}'
        '<div class="rb-tick end" style="left:100%;"></div>'
        f'<div class="rb-lab" style="left:100%;">{_fmt(high / div, dec)}</div>'
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
        # Single-writer reports have no stage-4 sidecar — their scenarios live
        # in machine_readable.scenarios (owner_value_teur/probability_pct).
        scen = (report.get("machine_readable") or {}).get("scenarios")
    if not isinstance(scen, list):
        return None
    out = []
    for s in scen:
        if isinstance(s, dict):
            out.append({"name": str(s.get("name", "")),
                        "value": _to_num(s.get("value_teur", s.get("owner_value_teur"))),
                        "prob": _to_num(s.get("probability_pct"))})
    return out or None


def _derive(report):
    d = {}
    sc = report.get("_scenarios") or {}
    ev = _to_num(sc.get("expected_value_teur"))
    if ev is None:  # single-writer shape: top-level expected_value object
        evf = report.get("expected_value")
        ev = _to_num(evf.get("value") if isinstance(evf, dict) else evf)
    base = _to_num(sc.get("realistic_base_case_teur"))
    if base is None:
        base = _to_num((report.get("machine_readable") or {})
                       .get("base_case_value_before_floor"))
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
    return f'<p>{_inline(b.get("text"))}</p>'


def _callout_variant(v):
    return {"key": "reality", "warning": "kill", "info": "neutral"}.get(v, "neutral")


def _block_callout(b):
    variant = _callout_variant(b.get("variant", "info"))
    title = b.get("title")
    th = (f'<div class="co-t"><span class="co-badge"></span>{_esc(title)}</div>'
          if title else "")
    # Contract allows text OR paragraphs[] OR items[](+ordered) — render whichever
    # is present (the old renderer only read `text`, silently dropping the others).
    parts = []
    if b.get("text") not in (None, ""):
        parts.append(f'<p>{_inline(b.get("text"))}</p>')
    for p in (b.get("paragraphs") or []):
        parts.append(f'<p>{_inline(p)}</p>')
    items = b.get("items")
    if isinstance(items, list) and items:
        tag = "ol" if b.get("ordered") else "ul"
        lis = "".join(f'<li>{_inline(it)}</li>' for it in items)
        parts.append(f'<{tag} class="co-list">{lis}</{tag}>')
    if not parts:  # nothing structured — flatten whatever is there, never blank-drop
        parts.append(f'<p>{_inline(b.get("text"))}</p>')
    return f'<div class="callout {variant}">{th}{"".join(parts)}</div>'


def _as_records(coll, keys):
    """Coerce a collection into a list of dicts, whatever shape the model emits:
    a dict record ({k: v}), a list of [k, v] lists, or already a list of dicts.
    `keys` names the fields to fill from a record/pair. Prevents the 'collection
    arrived as a dict/list' drift from silently dropping cards / rows / drivers."""
    if isinstance(coll, dict):
        return [{keys[0]: k, keys[1]: v} for k, v in coll.items()]
    out = []
    for it in coll or []:
        if isinstance(it, dict):
            out.append(it)
        elif isinstance(it, list):
            out.append({k: (it[i] if i < len(it) else "") for i, k in enumerate(keys)})
    return out


def _block_metric_cards(b):
    cards = []
    for c in _as_records(b.get("cards"), ("label", "value")):
        label = _clean(c.get("label")).lower()
        if "luottamustaso" in label:
            continue
        cards.append(c)
    if not cards:
        return ""
    n = max(1, min(len(cards), 4))
    cells = []
    for c in cards:
        accent = " accent" if c.get("emphasis") else ""
        val = _clean(c.get("value"))
        vcls = "mval long" if len(val) > 24 else "mval"
        cells.append(f'<div class="mcard{accent}"><div class="{vcls}">{_esc(c.get("value"))}</div>'
                     f'<div class="mlabel">{_esc(c.get("label"))}</div></div>')
    return (f'<div class="mgrid" style="grid-template-columns:repeat({n},1fr);">'
            f'{"".join(cells)}</div>')


def _block_key_value(b):
    items = _as_records(b.get("items"), ("key", "value"))
    title = b.get("title")
    rows = []
    for it in items:
        src = it.get("source")
        src_html = (
            f' <span class="muted" style="font-size:7pt">({_source_inline(src)})</span>'
            if src else ""
        )
        val = it.get("value")
        # A prose value ("Langaton suoramuuntoteknologia korvaa...") reads badly
        # right-aligned and nowrap'd — stack it under its label instead.
        long_text = isinstance(val, str) and len(val) > 60 and not _cell_numish(val)
        cls = "kv kvl" if long_text else "kv"
        rows.append(f'<div class="{cls}"><span class="k">{_esc(it.get("key"))}{src_html}</span>'
                    f'<span class="v">{_esc(val)}</span></div>')
    head = f'<h4 class="blk">{_esc(title)}</h4>' if title else ""
    return f'{head}{"".join(rows)}'


_ROW_LABEL_KEYS = ("row", "label", "name", "nimi", "rivi", "otsikko")
_ROW_VALUE_KEYS = ("values", "arvot", "vals", "cells")


def _coerce_table_rows(columns, rows):
    """Render any table shape the model emits as real cells — never a raw '{...}'
    dump. Stages emit three non-contract variants (all seen in one report):
      1. {"row"/"label": <label>, "values": [...]}  transposed metric-per-row
      2. {"<ColName>": v, ...}                       record keyed by the columns
      3. list-of-cells                               the contract shape
    plus the occasional dict-of-rows for `rows` itself. This coerces every case:
    variant 2 is aligned to the column order BY NAME (never trusting dict order);
    variant 1 becomes [label, *values] with an empty label header prepended when
    the header row lacks it; lists pass through untouched (no regression)."""
    if isinstance(rows, dict):  # rows given as {label: values-or-scalar, ...}
        rows = [{"row": k, "values": v} if isinstance(v, list) else {"row": k, "values": [v]}
                for k, v in rows.items()]
    if not isinstance(rows, list):
        return columns, rows

    col_list = list(columns) if isinstance(columns, list) else None
    col_set = {str(c) for c in col_list} if col_list else set()

    def values_of(r):
        for k in _ROW_VALUE_KEYS:
            if isinstance(r.get(k), list):
                return r[k]
        return None

    out, saw_labelled = [], False
    for r in rows:
        if isinstance(r, list):
            out.append(r)
        elif isinstance(r, dict):
            vals = values_of(r)
            keymatch = col_set and len(col_set & {str(k) for k in r}) >= max(2, len(col_set) - 1)
            if vals is None and keymatch:
                # variant 2: record keyed by column names — align by name
                out.append([r.get(c, r.get(str(c), "")) for c in col_list])
            elif vals is not None:
                # variant 1: {label, values}
                lab = next((r[k] for k in _ROW_LABEL_KEYS if k in r), None)
                out.append(([lab] if lab is not None else []) + list(vals))
                saw_labelled = saw_labelled or lab is not None
            else:
                out.append(list(r.values()))  # last resort: never dump a raw dict
        else:
            out.append(r)

    cols = columns
    if saw_labelled and col_list:
        widest = max((len(x) for x in out if isinstance(x, list)), default=0)
        if len(col_list) == widest - 1:
            cols = [""] + col_list
    # Rectangularize: pad the header and every row to one common width so a short
    # or ragged row (a model that dropped a value) never shifts later cells under
    # the wrong header.
    widths = [len(x) for x in out if isinstance(x, list)]
    width = max(([len(cols)] if isinstance(cols, list) else []) + widths, default=0)
    if width:
        if isinstance(cols, list):
            cols = list(cols) + [""] * (width - len(cols))
        out = [x + [""] * (width - len(x)) if isinstance(x, list) and len(x) < width else x
               for x in out]
    return cols, out


_LETTER_RE = re.compile(r"[A-Za-zÅÄÖåäö]")


def _cell_numish(v):
    """Does a cell read as a figure ('15 % → n. 3 000 tEUR/v') rather than
    prose? None = empty, don't vote."""
    s = str(v if v is not None else "").strip()
    if not s:
        return None
    digits = sum(ch.isdigit() for ch in s)
    letters = len(_LETTER_RE.findall(s))
    return digits > 0 and letters <= max(4, digits)


def _col_aligns(columns, rows):
    """Right-align only columns whose body cells are mostly figures. Prose
    columns (Lähde, Miten näkyy luvuissa, ...) read left-aligned; the old
    blanket text-align:right made multi-line prose cells ragged."""
    n = max([len(columns or [])]
            + [len(r) for r in rows or [] if isinstance(r, list)] or [0])
    aligns = []
    for j in range(n):
        if j == 0:
            aligns.append("left")
            continue
        votes = [_cell_numish(r[j]) for r in rows or []
                 if isinstance(r, list) and len(r) > j]
        votes = [v for v in votes if v is not None]
        numeric = bool(votes) and sum(votes) / len(votes) >= 0.6
        aligns.append("right" if numeric else "left")
    return aligns


def _render_table(columns, rows, title=None, unit=None):
    columns, rows = _coerce_table_rows(columns, rows)
    cap = ""
    if title or unit:
        u = f' <span class="muted">({_esc(unit)})</span>' if unit else ""
        cap = f'<h4 class="blk">{_esc(title)}{u}</h4>'
    wide = " wide" if isinstance(columns, list) and len(columns) >= 7 else ""
    aligns = _col_aligns(columns, rows)

    def _al(j):
        a = aligns[j] if j < len(aligns) else "right"
        return f' style="text-align:{a}"' if a != "right" else ""

    ths = "".join(f"<th{_al(j)}>{_esc(c)}</th>"
                  for j, c in enumerate(columns or []))
    trs = []
    for r in rows or []:
        cells = r if isinstance(r, list) else [r]
        tds = [f"<td{_al(j)}>{_num_cell(c)}</td>" for j, c in enumerate(cells)]
        trs.append("<tr>" + "".join(tds) + "</tr>")
    return (f'{cap}<table class="tbl{wide}"><thead><tr>{ths}</tr></thead>'
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
        if ctype == "waterfall":
            return _svg_waterfall(b.get("steps"))
        if ctype == "bar_line":
            bar = next((s for s in series if s.get("type", "bar") == "bar"), None)
            line = next((s for s in series if s.get("type") == "line"), None)
            return _svg_combo(x, _nums(bar.get("values")) if bar else [],
                              _nums(line.get("values")) if line else [],
                              line_pct="%" in str(b.get("unit", "")) or True,
                              forecast_from=forecast,
                              bar_name=bar.get("name") if bar else None,
                              line_name=line.get("name") if line else None)
        return _svg_bars(x, series, forecast_from=forecast)
    except Exception:
        return ""


def _block_scenario_table(b):
    name = b.get("scenario", "")
    cls = {"optimistinen": "pos", "pessimistinen": "neg"}.get(str(name).lower(), "")
    drivers = "".join(
        f'<div class="driver"><span class="dk">{_esc(d.get("key"))}</span>'
        f'<span class="dv">{_esc(d.get("value"))}</span></div>'
        for d in _as_records(b.get("drivers"), ("key", "value")))
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


def _brand(report):
    """(display name, legal name) — defaults keep the Valuatum branding, but a
    white-label tenant can override via meta.brand_name / meta.brand_legal_name."""
    m = report.get("meta") or {}
    name = (str(m.get("brand_name") or "").strip()) or "Valuatum"
    legal = (str(m.get("brand_legal_name") or "").strip()) or "Valuatum Oy"
    return name, legal


def _brandmark(report):
    return f'<span class="brandmark"><i></i>{_esc(_brand(report)[0])}</span>'


# Running header/footer + page numbers are now CSS @page margin boxes (see
# _page_css) so they repeat on every page including continuations. The old
# baked-in HTML header/footer would only appear on a section's first page.
def _header(report):
    return ""


def _footer():
    return ""


def _cover(report, derived):
    """Cover v2 (CEO-approved mockup 2026-07-08): dark brand band, ONE hero
    figure, a scenario range track with labelled markers, plain-language legend
    rows, an edit-your-assumptions note, and the sign-off colophon."""
    cover = report.get("cover") or {}
    meta = report.get("meta") or {}
    _, legal = _brand(report)
    scale = _report_scale(report, derived)
    div, unit_lab, dec = scale or (1.0, "tEUR", 0)
    hv = cover.get("headline_value")
    bcv = cover.get("base_case_value")  # realistic base case (DCF/EVA-based)
    rng = derived.get("range")
    industry = _display_industry(meta)

    # Lead with one valuation: the realistic base case. Scenario expected value
    # is a scenario-analysis output, not a competing cover valuation.
    hero_val = bcv if bcv not in (None, "") else hv
    hero_label = ("Oman pääoman arvo (realistinen perusskenaario)" if bcv not in (None, "")
                  else (cover.get("headline_label") or "Arvonmäärityksen tulos"))
    base_num = _to_num(_short(bcv)) if bcv not in (None, "") else None
    exp_num = _to_num(_short(hv)) if hv not in (None, "") else None

    zero_floor = base_num is not None and base_num <= 0
    if zero_floor:
        note = ("Realistinen perusskenaario ei tue positiivista omistaja-arvoa. "
                "Mahdollinen arvo on optio- tai strategista arvoa — arvoa, joka "
                "perustuu epävarman tulevaisuuden mahdollisuuden toteutumiseen, "
                "ei nykyiseen kassavirtaan. Se kuvataan skenaarioissa eikä "
                "esitetä yrityksen arvona.")
    else:
        note = ("Arvio omistajille kuuluvasta arvosta, jos yhtiön kehitys jatkuu "
                "toteutuneiden lukujen ja ennusteiden mukaisesti.")
        if base_num is not None and exp_num is not None:
            gap = max(1.0, 0.02 * abs(base_num))
            if exp_num > base_num + gap:
                note += (" Skenaarioanalyysissä optimistinen polku nostaa "
                         "odotusarvoa; yrityksen arvona esitetään realistinen "
                         "perusskenaario.")
            elif exp_num < base_num - gap:
                note += (" Skenaarioanalyysissä pessimistinen polku laskee "
                         "odotusarvoa; yrityksen arvona esitetään realistinen "
                         "perusskenaario.")

    # --- band -----------------------------------------------------------
    # "parent" is the default for every non-consolidated run, not a signal that
    # subsidiaries exist — so "emoyhtiö" reads as misleading for a standalone
    # company. "yhtiötaso" (erillistilinpäätös) is correct in both cases.
    level_fi = {"parent": "yhtiötaso", "consolidated": "konsernitaso",
                "group": "konsernitaso"}.get(str(meta.get("level") or "").lower())
    conf = (report.get("confidence") or {}).get("level")
    band_meta_bits = [
        f'Raportin päivä {_esc(meta.get("report_date"))}' if meta.get("report_date") else "",
        (f'Luvut: {level_fi}, {_esc(unit_lab)}' if level_fi else f'Yksikkö: {_esc(unit_lab)}'),
        f'Luottamustaso: {_esc(conf)}' if conf else "",
    ]
    band_meta = "".join(f"<div>{x}</div>" for x in band_meta_bits if x)
    doctype_bits = [f'Y-tunnus {_esc(meta.get("y_tunnus"))}' if meta.get("y_tunnus") else "",
                    _esc(industry)]
    doctype = " · ".join(x for x in doctype_bits if x)

    band = (
        '<div class="cv2-band">'
        '<div>'
        f'<div class="cv2-brand">{_esc(_brand(report)[0])} · Yritysanalyysi · AI</div>'
        f'<h1>{_esc(meta.get("company_name"))}</h1>'
        + (f'<div class="cv2-doctype">{doctype}</div>' if doctype else "")
        + '</div>'
        f'<div class="cv2-bandmeta">{band_meta}</div>'
        '</div>'
    )

    # --- hero -----------------------------------------------------------
    hero = (
        '<div class="cv2-hero">'
        f'<div class="cap">{_esc(hero_label)}</div>'
        f'<div class="val">{html.escape(_scaled_cover_str(hero_val, scale))}</div>'
        f'<div class="sub">{_esc(note)}</div>'
        '</div>'
    )

    # --- scenario range track + legend -----------------------------------
    track_html = legend_html = ""
    lo = rng.get("low") if rng else None
    hi = rng.get("high") if rng else None
    if lo is not None and hi is not None and hi > lo:
        span = hi - lo

        def _pos(v):
            return max(5.0, min(95.0, 5 + 90 * (v - lo) / span))

        def _val_lab(v):
            return f"{_fmt(v / div, dec)} {unit_lab}"

        marks = [(lo, "pess", "Pessimistinen skenaario"),
                 (hi, "opti", "Optimistinen skenaario")]
        if base_num is not None:
            marks.append((base_num, "base", "Realistinen perusskenaario"))
        if exp_num is not None and (base_num is None
                                    or abs(exp_num - base_num) > 0.01 * max(1.0, abs(base_num))):
            marks.append((exp_num, "odds", "Skenaarioiden odotusarvo"))
        marks.sort(key=lambda m: m[0])
        positions = [_pos(v) for v, _, _ in marks]
        # Centered labels are wide; two dots closer than this (in % of track)
        # collide horizontally. Fan the pair out: left label's text to the left
        # of its dot, right label's text to the right. Far-apart marks stay centered.
        CLOSE = 16.0
        anchor = [""] * len(marks)
        for i in range(1, len(marks)):
            if positions[i] - positions[i - 1] < CLOSE:
                anchor[i - 1] = " aleft"
                anchor[i] = " aright"
        mark_html = []
        for i, (v, css, lab) in enumerate(marks):
            row = "row1" if i % 2 == 0 else "row2"
            mark_html.append(
                f'<div class="cv2-mark {css}" style="left:{positions[i]:.1f}%">'
                f'<div class="dot"></div>'
                f'<div class="tag {row}{anchor[i]}"><b>{_esc(_val_lab(v))}</b>{_esc(lab)}</div></div>')
        track_html = (
            '<div class="cv2-range">'
            '<div class="cv2-sect">Arvion haarukka skenaarioittain</div>'
            f'<div class="cv2-track"><div class="cv2-fill"></div>{"".join(mark_html)}</div>'
            '</div>'
        )
        legend_rows = [
            ("var(--ink)", "Realistinen perusskenaario",
             "Laskettu yhtiön toteutuneista luvuista ja ennusteista kassavirta- (DCF) "
             "ja lisäarvomenetelmällä (EVA). Tämä on raportin pääluku."),
            ("var(--lime)", "Skenaarioiden odotusarvo",
             "Skenaarioiden todennäköisyyksillä painotettu keskiarvo. Todennäköisyydet "
             "ovat muokattavia oletuksia, eivät ennuste."),
            ("var(--red)", "Haarukan ääripäät",
             "Pessimistinen ja optimistinen skenaario kuvaavat arvion ala- ja ylärajan; "
             "oletukset ja perustelut skenaario-osiossa."),
        ]
        legend_html = '<div class="cv2-legend">' + "".join(
            f'<div class="row"><span class="term"><span class="chip" '
            f'style="background:{c}"></span>{_esc(t)}</span>'
            f'<span class="expl">{_esc(e)}</span></div>' for c, t, e in legend_rows) + "</div>"

    foot = ('<div class="cv2-foot"><b>Voit muuttaa oletuksia.</b> Skenaarioiden '
            'todennäköisyydet ja ennusteparametrit ovat muokattavissa Valuatumin '
            'järjestelmässä — muutokset päivittävät arvion, ja raportin voi tuottaa '
            'uudelleen omilla odotuksilla.</div>')

    return (
        '<section class="page cover">'
        + band
        + '<div class="cv2-body">'
        + hero + track_html + legend_html + foot
        + '</div></section>'
    )


def _snapshot(report, derived):
    cover = report.get("cover") or {}
    conf = report.get("confidence") or {}
    dq = (report.get("data_quality") or {}).get("class")
    rng = derived.get("range")
    cards = [("Oman pääoman arvo (estimaatti)", cover.get("headline_value")),
             ("Realistinen perusskenaario", cover.get("base_case_value"))]
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


_MANDATE_LABELS = [
    ("valuation_date", "Arvopäivä"),
    ("report_date", "Raportin päivä"),
    ("purpose", "Käyttötarkoitus"),
    ("intended_users", "Tarkoitetut käyttäjät"),
    ("standard_of_value", "Arvon standardi"),
    ("ownership_interest", "Arvostettava omistusosuus"),
    ("marketability", "Markkinoitavuus"),
    ("going_concern", "Jatkuvan toiminnan oletus"),
    ("currency_unit", "Valuutta / yksikkö"),
]


def _mandate(report):
    """Toimeksianto (engagement/mandate) block: valuation date, purpose,
    standard of value, etc. — shown before the ToC so a reader knows what kind
    of opinion they're holding before reaching any numbers."""
    meta = report.get("meta") or {}
    mandate = meta.get("mandate")
    if not isinstance(mandate, dict) or not mandate:
        return ""
    values = dict(mandate)
    values.setdefault("report_date", meta.get("report_date"))
    values.setdefault("currency_unit", meta.get("unit"))
    rows = "".join(
        f'<div class="kv"><span class="k">{_esc(label)}</span>'
        f'<span class="v" style="white-space:normal;text-align:right;max-width:110mm">{_esc(values[key])}</span></div>'
        for key, label in _MANDATE_LABELS if values.get(key)
    )
    if not rows:
        return ""
    return (
        '<div style="margin-bottom:20px">'
        '<h4 class="blk" style="margin-top:0">Toimeksianto</h4>'
        f'{rows}</div>'
    )


def _toc(report, sections):
    # Display numbers are sequential (1..N) regardless of the internal
    # section `id` — SECTION_ORDER has no "7" by design, and showing that raw
    # id would make the ToC jump 6→8 for no reason a reader can see.
    rows = "".join(
        f'<a class="toc-row" href="#sec-{i}"><span class="tn">{i}</span>'
        f'<span class="tt">{_esc(s.get("title"))}</span></a>'
        for i, s in enumerate(sections, start=1))
    return (
        '<section class="page">'
        f'{_header(report)}'
        '<div class="pbody">'
        f'{_mandate(report)}'
        '<div class="sec-head"><span class="sec-num" style="background:var(--green);color:#fff">·</span>'
        '<div class="sh-t"><h2>Sisällys</h2><div class="sh-sub">AI-Arvonmääritysraportti</div></div></div>'
        '<div class="sec-rule"></div>'
        f'<div class="toc">{rows}</div></div>{_footer()}</section>'
    )


def _method_visuals(derived):
    """Weights donut + method-value bars — the signature derived visuals. The
    standalone Snapshot page is no longer generated (per the design contract),
    so these live in the method-selection section instead."""
    donut = (derived or {}).get("weights_donut")
    methods = (derived or {}).get("methods")
    if not (donut or methods):
        return ""
    left = (f'<div><h4 class="blk">Menetelmäpainot</h4><div style="max-width:150px">'
            f'{_svg_donut(donut)}</div></div>') if donut else "<div></div>"
    right = (f'<div><h4 class="blk">Menetelmien arvot</h4>{_svg_hbars(methods)}</div>'
             if methods else "<div></div>")
    return ('<div class="two-col" style="margin-top:10px;grid-template-columns:0.7fr 1.3fr">'
            f'{left}{right}</div>')


def _norm_caption(t):
    return re.sub(r"[^a-z0-9åäö]+", " ", str(t or "").casefold()).strip()


def _dedup_captions(blocks):
    """The model often emits a heading ("Lähderekisteri") followed by a table
    whose title repeats it ("LÄHDEREKISTERI") — two stacked titles for one
    table. Drop the block title when it (nearly) repeats the heading above."""
    out, last_head = [], ""
    for b in blocks or []:
        if not isinstance(b, dict):
            out.append(b)
            continue
        if b.get("type") == "heading":
            last_head = _norm_caption(b.get("text"))
            out.append(b)
            continue
        title = _norm_caption(b.get("title"))
        if (title and last_head and b.get("type") in ("table", "key_value")
                and (title == last_head or last_head.startswith(title)
                     or title.startswith(last_head))):
            b = {**b, "title": None}
        if b.get("type") not in ("paragraph",):
            last_head = ""  # a heading only covers the block right after it
        out.append(b)
    return out


def _section(report, sec, derived=None, display_no=None):
    blocks = "".join(x for x in (_render_block(b)
                                 for b in _dedup_captions(sec.get("blocks"))) if x)
    # Section 8 (arvonmääritys) already carries the model's own method table +
    # method-value chart, so we do NOT inject derived visuals here — on distressed
    # companies the derived donut/bars duplicated and contradicted them (scenario
    # weights mislabelled as method weights; negative method values shown "hylätty").
    if not blocks.strip():
        blocks = ('<p class="muted" style="font-style:italic">'
                  'Tietoa ei ollut saatavilla tähän osioon.</p>')
    num = display_no if display_no is not None else sec.get("id")
    anchor = f' id="sec-{display_no}"' if display_no is not None else ""
    return (
        f'<section class="page report-section"{anchor}>'
        f'{_header(report)}'
        '<div class="pbody">'
        f'<div class="sec-head"><span class="sec-num">{_esc(num)}</span>'
        f'<div class="sh-t"><h2>{_esc(sec.get("title"))}</h2></div></div>'
        '<div class="sec-rule"></div>'
        f'{blocks}</div>{_footer()}</section>'
    )


def _appendix_divider(report):
    return (
        '<section class="page appendix-divider">'
        f'{_header(report)}'
        '<div class="pbody" style="display:flex;align-items:center;justify-content:center;flex:1 1 auto">'
        '<div style="max-width:110mm;text-align:center">'
        '<div style="font-size:8pt;text-transform:uppercase;letter-spacing:.14em;color:var(--lime-deep);'
        'font-weight:700;margin-bottom:8px">Liite</div>'
        '<h2 style="font-size:22pt;margin-bottom:10px">Liitteet</h2>'
        '<p class="muted">Ennusteen täydet vuositason luvut, lähderekisteri ja metodologiakuvaus. '
        'Tukiaineistoa raportin pääsisällölle, ei sen osa.</p>'
        f'</div></div>{_footer()}</section>'
    )


# --------------------------------------------------------------------------- #
# cover guard + assembly
# --------------------------------------------------------------------------- #
# Mandatory legal disclaimer (master spec §16, exact text). Guaranteed into the
# PDF even if the stage-6 model drops section 16 — selling an automated valuation
# with no "ei sijoitusneuvontaa" notice is real legal exposure.
def _disclaimer_text(legal="Valuatum Oy"):
    return (
        "Tämä raportti on tuotettu automaattisesti yleiseen tietoon perustuvana "
        "analyysinä. Se ei ole sijoitusneuvontaa, tilintarkastusta, käyvän arvon "
        "lausunto (fairness opinion) eikä sellaisenaan sovellu vero- tai "
        "oikeusriitojen perusteeksi ilman asiantuntijan erillistä arviota. "
        f"{legal} ei vastaa raportin perusteella tehdyistä päätöksistä."
    )


def _disclaimer_section(legal="Valuatum Oy"):
    return {"id": "16", "title": "Vastuuvapaus", "blocks": [
        {"type": "paragraph", "text": _disclaimer_text(legal)}]}


def _ensure_disclaimer(sections, legal="Valuatum Oy"):
    """Guarantee a section 16 carrying the legal disclaimer text."""
    out = list(sections)
    s16 = next((s for s in out if str(s.get("id")) == "16"), None)
    if s16 is None:
        out.append(_disclaimer_section(legal))
    elif "sijoitusneuvo" not in str(s16).lower():
        out[out.index(s16)] = _disclaimer_section(legal)
    return out


def _cover_guard(report, derived):
    cover = report.get("cover") or {}
    text = _norm_ws(_strip_tags(_cover(report, derived)))
    scale = _report_scale(report, derived)
    missing = []
    label = "base_case_value" if "base_case_value" in cover else "headline_value"
    val = cover.get(label)
    if val is None or str(val).strip() == "":
        missing.append(f"{label} puuttuu/tyhjä")
    # a figure is intact if its raw OR its report-unit-scaled form rendered
    elif (_norm_ws(_short(val)) not in text
          and _norm_ws(_scaled_cover_str(val, scale)) not in text):
        missing.append(f"{label}={val!r}")
    if missing:
        raise CoverGuardError("Kannen pääluku ei renderöitynyt ehjänä: "
                              + "; ".join(missing) + " — kansiteksti: " + text[:300])


def _css_str(v):
    """Sanitise a value for use inside a CSS `content:"..."` string."""
    return (str(v or "").replace("\\", "").replace('"', "'")
            .replace("<", "").replace(">", ""))


def _page_css(report):
    meta = report.get("meta") or {}
    name, legal = _brand(report)
    bits = [meta.get("company_name"), meta.get("y_tunnus"), meta.get("report_date")]
    head_right = _css_str(" · ".join(str(x) for x in bits if x))
    head_left = _css_str(f"{name} · AI-Arvonmääritysraportti")
    foot_left = _css_str(legal)
    # Running header/footer + page numbers live in @page margin boxes so they
    # repeat on EVERY page — including section continuations — which the baked-in
    # HTML header could not do. Suppressed on the full-bleed cover.
    return f"""
@page {{ size: A4; margin: 15mm 15mm 15mm;
  @top-left {{ content: "{head_left}"; font-family: {SANS};
    font-size: 7.4pt; color: {C['gray']}; }}
  @top-right {{ content: "{head_right}"; font-family: {SANS}; font-size: 7.4pt; color: {C['gray']}; }}
  @bottom-left {{ content: "{foot_left}"; font-family: {SANS}; font-size: 7.2pt; color: {C['gray']}; }}
  @bottom-right {{ content: counter(page); font-family: {SANS}; font-size: 8pt; color: {C['gray']}; }}
}}
@page cover {{ margin: 0;
  @top-left {{ content: none; }} @top-right {{ content: none; }}
  @bottom-left {{ content: none; }} @bottom-right {{ content: none; }}
}}
"""


def _collect_source_urls(report):
    """Walk every string in the report (table rows, key_value items, source
    registers...) and index each bare URL by domain, so prose citations that
    only name the domain can be linked back to the full URL found elsewhere."""
    domain_map = {}

    def walk(x):
        if isinstance(x, dict):
            for v in x.values():
                walk(v)
        elif isinstance(x, list):
            for v in x:
                walk(v)
        elif isinstance(x, str):
            m = _URL_CELL_RE.match(x.strip())
            if m:
                domain_map.setdefault(m.group(1).lower(), x.strip())

    walk(report.get("sections"))
    return domain_map


# Matches a Finnish "osio"-stem cross-reference + a section number, capturing the
# prefix/whitespace so only the number is rewritten and case is preserved:
# "osiossa 9" / "osion 10" / "Osio 17".
_SECTION_REF_RE = re.compile(r"\b(osio\w*)(\s+)(\d{1,2})\b", re.IGNORECASE)


def _resolve_section_refs(sections):
    """Rewrite hard "osio N" cross-references in prose from the prompt's internal
    section-id space to the sequential display numbers the reader actually sees.
    SECTION_ORDER skips id 7 and keeps 17, so every id >= 8 renders one lower —
    the LLM (which numbers by internal id) otherwise cites "osio 9" for the DCF
    section that prints as 8. One registry, resolved deterministically.

    # ponytail: single-reference only; a compound "osio 4 ja 11" fixes just the
    # first number. Under-fixing is safe (the stray number was already wrong);
    # over-matching a bare trailing number would risk corrupting a real figure.
    """
    display_by_id = {str(s.get("id")): i for i, s in enumerate(sections, start=1)
                     if isinstance(s, dict) and s.get("id") is not None}

    def _repl(m):
        disp = display_by_id.get(m.group(3))
        if disp is None or str(disp) == m.group(3):
            return m.group(0)
        return f"{m.group(1)}{m.group(2)}{disp}"

    for sec in sections:
        if not isinstance(sec, dict):
            continue
        for b in sec.get("blocks") or []:
            if isinstance(b, dict) and isinstance(b.get("text"), str):
                b["text"] = _SECTION_REF_RE.sub(_repl, b["text"])
    return sections


def render_html(report):
    if not isinstance(report, dict):
        raise ValueError("report ei ole objekti")
    _source_domain_map.set(_collect_source_urls(report))
    derived = _derive(report)
    try:
        _cover_guard(report, derived)
    except CoverGuardError:
        pass  # non-fatal: render with whatever the cover carries, never 500
    sections = _resolve_section_refs(
        _ensure_disclaimer(_ordered_sections(report), _brand(report)[1]))
    # Snapshot page intentionally omitted (design contract — section 1 TIIVISTELMÄ
    # carries the key figures; its derived visuals now live in section 8).
    # Insert one "Liitteet" divider right before the first appendix section
    # (source register / methodology / full forecast detail) so the main body
    # stays a coherent read and the appendix is clearly marked as such.
    section_html_parts = []
    divider_shown = False
    for i, s in enumerate(sections, start=1):
        if not divider_shown and str(s.get("id")) in APPENDIX_SECTION_IDS:
            section_html_parts.append(_appendix_divider(report))
            divider_shown = True
        section_html_parts.append(_section(report, s, derived, display_no=i))
    body = (_cover(report, derived)
            + _toc(report, sections)
            + "".join(section_html_parts))
    meta = report.get("meta") or {}
    title = _esc(meta.get("company_name") or "AI-Arvonmääritysraportti")
    # 2026-07 brand refresh: display face is Georgia; Gelasio (metric-compatible)
    # covers the PDF container where Georgia isn't installed. Body text is the
    # system sans stack — no webfont needed.
    font_block = ('<style>@import url("https://fonts.googleapis.com/css2?'
                  'family=Gelasio:wght@400;500;700;800&display=swap");</style>')
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
        # See Dockerfile: this runs Google Chrome, not Debian's `chromium`
        # package — the latter hard-crashed (SIGTRAP) on every single
        # --print-to-pdf call in this container. The noisy "Failed to
        # connect to the bus" dbus errors in the logs are unrelated — they
        # show up on successful runs too.
        cmd = [chrome, "--headless=new", f"--print-to-pdf={out_path}",
               "--no-pdf-header-footer", "--virtual-time-budget=12000",
               "--no-sandbox", "--disable-dev-shm-usage", f"file://{html_path}"]
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
        if proc.returncode != 0 or not os.path.exists(out_path):
            raise RuntimeError(
                f"PDF-renderöinti epäonnistui (exit {proc.returncode}):\n"
                + (proc.stderr or "")[:2000]
            )
    finally:
        try:
            os.unlink(html_path)
        except OSError:
            pass
    return out_path


_STATIC_CSS = """
:root{
  --bg:#FFFFFF; --ink:#1A1D1A; --lime:#4F7A6A; --lime-deep:#33604F;
  --green:#12352B; --green-soft:#E8EEEA; --green-line:#C3D2C9; --red:#C0504D;
  --red-soft:#F6E7E6; --gray:#6B7280; --gray-soft:#F2F3F1; --line:#E1E4DE;
  --line-strong:#CBD0C9; --paper:#EDEFEA;
  --sans:-apple-system, "Segoe UI", Roboto, "Helvetica Neue", system-ui, sans-serif;
  --head:Georgia, Gelasio, "Times New Roman", serif;
}
*{ box-sizing:border-box; }
html,body{ margin:0; padding:0; }
body{ background:#fff; color:var(--ink); font-family:var(--sans); font-size:9.6pt; line-height:1.5; }
p{ max-width:72ch; }
.page{ position:relative; padding:0; display:flex; flex-direction:column; }
@media print{ .page{ min-height:255mm; page-break-after:always; } }
@media screen{ .page{ padding-bottom:14mm; } }
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
.sec-num{ font-family:var(--head); font-weight:700; font-size:11pt; color:#fff; background:var(--lime);
  width:26px; height:26px; flex:0 0 26px; display:flex; align-items:center; justify-content:center; }
.sec-head .sh-t{ flex:1 1 auto; }
.sec-head h2{ font-size:17pt; font-weight:700; letter-spacing:-.01em; }
.sec-head .sh-sub{ font-size:8pt; color:var(--gray); margin-top:3px; letter-spacing:.06em; text-transform:uppercase; font-weight:700; }
.sec-rule{ height:2px; background:var(--green); margin:0 0 13px; }
h3.blk{ font-size:10.5pt; font-weight:700; color:var(--green); margin:14px 0 6px; }
h4.blk{ font-size:8pt; font-weight:700; color:var(--gray); text-transform:uppercase; letter-spacing:.08em; margin:13px 0 6px; }
.mgrid{ display:grid; gap:8px; }
.mcard{ border:1px solid var(--line-strong); border-top:3px solid var(--green); padding:9px 11px; min-height:22mm; }
.mcard.accent{ border-top-color:var(--lime); }
.mcard .mval{ font-family:var(--head); font-weight:700; font-size:13pt; color:var(--green); line-height:1.08;
  font-variant-numeric:tabular-nums lining-nums; letter-spacing:0; overflow-wrap:anywhere; }
.mcard .mval.long{ font-family:var(--sans); font-size:9.4pt; line-height:1.22; font-weight:700; color:var(--green); }
.mcard .mlabel{ font-size:7.8pt; color:var(--gray); margin-top:5px; line-height:1.25; }
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
.callout .co-list{ margin:4px 0 0; padding-left:18px; } .callout .co-list li{ margin:2px 0; }
.callout.kill{ border-left:4px solid var(--red); background:var(--red-soft); }
.callout.kill .co-t, .callout.kill .co-badge{ color:var(--red); background:initial; } .callout.kill .co-badge{ background:var(--red); }
.callout.reality{ border:2px solid var(--green); background:var(--green-soft); }
.callout.reality .co-t{ color:var(--green); } .callout.reality .co-badge{ background:var(--green); }
.callout.neutral{ border:1px solid var(--line-strong); border-left:4px solid var(--gray); background:var(--gray-soft); }
.callout.neutral .co-badge{ background:var(--gray); }
table.tbl{ width:100%; border-collapse:collapse; font-size:8.4pt; margin:6px 0 10px; }
table.tbl th, table.tbl td{ padding:4.5px 7px; text-align:right; border-bottom:1px solid var(--line);
  font-variant-numeric:tabular-nums lining-nums; overflow-wrap:break-word; }
table.tbl td:first-child{ max-width:80mm; }
table.tbl.wide{ table-layout:fixed; font-size:7.1pt; line-height:1.15; }
table.tbl.wide th, table.tbl.wide td{ padding:3px 4px; }
table.tbl.wide th:first-child, table.tbl.wide td:first-child{ width:38mm; max-width:38mm; white-space:normal; }
table.tbl.wide th:not(:first-child), table.tbl.wide td:not(:first-child){
  white-space:nowrap; overflow-wrap:normal; word-break:normal;
}
table.tbl thead th{ color:var(--green); font-weight:700; border-bottom:1.5px solid var(--green);
  font-family:var(--head); font-size:7.8pt; text-align:right; }
table.tbl.wide thead th{ font-size:6.8pt; }
/* Long column titles in a wide table (fixed layout) must wrap, not run past
   their cell and collide with the next header ("Oma pääoma ilman pääomalainoja"
   + "Korolliset velat" overlapped into garbage). Body number cells stay nowrap. */
table.tbl.wide thead th:not(:first-child){ white-space:normal; overflow-wrap:anywhere; word-break:break-word; }
table.tbl thead th:first-child{ text-align:left; }
table.tbl tbody tr:nth-child(even) td{ background:#FAFBFA; }
a.src{ color:var(--gray); text-decoration:none; border-bottom:1px solid var(--line-strong); }
.kv{ display:flex; justify-content:space-between; gap:10px; padding:3.5px 0; border-bottom:1px solid var(--line);
  font-size:8.6pt; align-items:baseline; }
.kv .k{ color:var(--gray); flex:1 1 auto; }
.kv .v{ font-variant-numeric:tabular-nums lining-nums; font-weight:600; white-space:nowrap; }
.kv.kvl{ flex-direction:column; align-items:flex-start; gap:1px; }
.kv.kvl .v{ white-space:normal; text-align:left; max-width:72ch; }
.chart-host{ width:100%; margin:8px 0 12px; page-break-inside:avoid; }
.two-col{ display:grid; grid-template-columns:1fr 1fr; gap:16px; align-items:start; }
.toc{ font-size:9.8pt; }
.toc-row{ display:flex; align-items:baseline; gap:8px; padding:7px 0; border-bottom:1px solid var(--line); text-decoration:none; color:inherit; }
.toc-row .tn{ font-family:var(--head); font-weight:700; color:var(--lime-deep); width:24px; flex:0 0 24px; }
.toc-row .tt{ color:var(--ink); font-weight:600; }
.toc-row .td{ flex:1 1 auto; border-bottom:1px dotted var(--line-strong); margin:0 4px 3px; }

/* cover */
.cover{ page:cover; padding:0; justify-content:flex-start; }
@media print{ .cover{ min-height:297mm; } }
.cv2-band{ background:var(--green); color:#F1F5F2; padding:16mm 22mm 12mm;
  display:flex; justify-content:space-between; align-items:flex-end; gap:10mm; flex-wrap:wrap; }
.cv2-brand{ font-size:8pt; letter-spacing:.16em; text-transform:uppercase; color:#9CB2A8; margin-bottom:9px; font-weight:700; }
.cv2-band h1{ font-family:var(--head); font-size:26pt; font-weight:500; color:#F1F5F2; margin:0; line-height:1.05; }
.cv2-doctype{ font-size:9.5pt; color:#9CB2A8; margin-top:5px; }
.cv2-bandmeta{ text-align:right; font-size:8.5pt; color:#9CB2A8; line-height:1.7; }
.cv2-body{ padding:12mm 22mm 14mm; flex:1 1 auto; display:flex; flex-direction:column; }
.cv2-hero{ text-align:center; padding:10mm 0 2mm; }
.cv2-hero .cap{ font-size:8.5pt; letter-spacing:.13em; text-transform:uppercase; color:var(--gray); font-weight:700; }
.cv2-hero .val{ font-family:var(--head); font-size:40pt; color:var(--green); line-height:1.05; margin:5px 0 3px; font-variant-numeric:tabular-nums; }
.cv2-hero .sub{ font-size:9pt; color:var(--gray); max-width:120mm; margin:4px auto 0; line-height:1.5; }
.cv2-sect{ font-size:8pt; letter-spacing:.13em; text-transform:uppercase; color:var(--gray); font-weight:700;
  border-top:1px solid var(--line); padding-top:14px; }
.cv2-range{ margin-top:12mm; }
.cv2-track{ position:relative; height:5px; background:var(--green-soft); border-radius:3px; margin:11mm 6mm 24mm; }
.cv2-fill{ position:absolute; inset:0; border-radius:3px; opacity:.35;
  background:linear-gradient(90deg, var(--red), var(--lime) 45%, var(--green)); }
.cv2-mark{ position:absolute; top:50%; transform:translate(-50%,-50%); }
.cv2-mark .dot{ width:11px; height:11px; border-radius:50%; border:2.5px solid #fff; box-shadow:0 0 0 1px var(--line-strong); }
.cv2-mark.base .dot{ width:15px; height:15px; background:var(--green); }
.cv2-mark.pess .dot{ background:var(--red); }
.cv2-mark.opti .dot{ background:var(--lime-deep); }
.cv2-mark.odds .dot{ background:var(--lime); }
.cv2-mark .tag{ position:absolute; left:50%; transform:translateX(-50%); white-space:nowrap; text-align:center;
  font-size:7.6pt; color:var(--gray); line-height:1.35; }
.cv2-mark .tag b{ display:block; color:var(--green); font-family:var(--head); font-size:9.5pt; font-variant-numeric:tabular-nums; }
.cv2-mark .tag.row1{ top:14px; } .cv2-mark .tag.row2{ top:46px; }
.cv2-mark .tag.aleft{ left:auto; right:50%; transform:none; text-align:right; padding-right:11px; }
.cv2-mark .tag.aright{ left:50%; transform:none; text-align:left; padding-left:11px; }
.cv2-legend{ border-top:1px solid var(--line); margin-top:4mm; }
.cv2-legend .row{ display:grid; grid-template-columns:52mm 1fr; gap:6mm; padding:8px 0;
  border-bottom:1px solid var(--line); align-items:baseline; }
.cv2-legend .term{ display:flex; align-items:baseline; gap:7px; font-size:9pt; color:var(--green); font-weight:700; }
.cv2-legend .term .chip{ width:8px; height:8px; border-radius:50%; flex:none; position:relative; top:-1px; }
.cv2-legend .expl{ font-size:8.5pt; color:var(--gray); line-height:1.45; }
.cv2-foot{ margin-top:6mm; font-size:8.2pt; color:var(--gray); border-top:1px solid var(--line); padding-top:10px; line-height:1.5; }
.cv2-foot b{ color:var(--ink); }

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
