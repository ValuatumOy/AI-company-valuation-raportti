"""Deterministic DCF sensitivity matrices (WACC x terminal growth, revenue x
EBIT margin) — computed in code, never by the LLM. The hard rule in every
stage prompt is "don't invent sensitivity matrices unless given"; this module
is what makes them available to give.

The discounting/bridge convention used by the valuation engine (mid-year vs.
end-of-year, stub periods, non-operating adjustments) isn't documented in the
data — so instead of guessing it, every quantity here is *calibrated* to the
engine's own given outputs (cumulative_discounted_fcff, equity_value_before_
floor) rather than re-derived from formula. This guarantees the center cell
of each matrix reproduces the reported base-case equity value exactly.
"""
import math

_WACC_STEPS_PP = [-2.0, -1.0, 0.0, 1.0, 2.0]
_GROWTH_STEPS_PP = [-1.0, -0.5, 0.0, 0.5, 1.0]
_REV_SCALES = [0.8, 0.9, 1.0, 1.1, 1.2]
_MARGIN_STEPS_PP = [-4.0, -2.0, 0.0, 2.0, 4.0]


def _is_num(x):
    return isinstance(x, (int, float)) and not isinstance(x, bool)


def _fmt_pct(v, decimals=1):
    s = f"{v:.{decimals}f}".rstrip("0").rstrip(".") if decimals else f"{v:.0f}"
    return s.replace(".", ",").replace("-", "−") + " %"


def _fmt_teur(v):
    return f"{round(v):,}".replace(",", " ") + " tEUR"


def _year_exponents(fcff, discounted_fcff, wacc_pct):
    """Empirically derive each year's discount exponent from the engine's own
    fcff/discounted_fcff pair, rather than assuming end-of-year or mid-year
    convention — this captures whatever stub-period timing the engine used
    for this specific valuation date."""
    wacc = wacc_pct / 100.0
    exps = []
    for i, (n, d) in enumerate(zip(fcff, discounted_fcff)):
        try:
            ratio = n / d
            if ratio > 0:
                exps.append(math.log(ratio) / math.log(1 + wacc))
                continue
        except (ZeroDivisionError, ValueError):
            pass
        exps.append(float(i + 1))  # fallback: plain end-of-year
    return exps


def _implied_growth_pct(tv_undiscounted, wacc_pct, fcff_last):
    wacc = wacc_pct / 100.0
    denom = tv_undiscounted + fcff_last
    if denom == 0:
        return None
    return ((tv_undiscounted * wacc - fcff_last) / denom) * 100.0


def _wacc_growth_matrix(fcff, disc, cum, equity_gt, wacc_base, bridge_adj):
    pv_forecast_base = sum(x for x in disc if _is_num(x))
    pv_terminal_base = cum[0] - pv_forecast_base
    fcff_last = fcff[-1]
    if pv_terminal_base <= 0 or not fcff_last:
        return None

    exps = _year_exponents(fcff, disc, wacc_base)
    t_terminal = exps[-1] + 1.0
    tv_undiscounted_base = pv_terminal_base * (1 + wacc_base / 100.0) ** t_terminal
    g_base = _implied_growth_pct(tv_undiscounted_base, wacc_base, fcff_last)
    if g_base is None or g_base >= wacc_base:
        return None

    # Compute on the unrounded axis values and round only the labels: rounding
    # the implied growth first put the centre cell a few tEUR off the reported
    # value it is supposed to reproduce.
    wacc_values = [wacc_base + d for d in _WACC_STEPS_PP if wacc_base + d > 0]
    growth_values = [g_base + d for d in _GROWTH_STEPS_PP]

    series = []
    for w in wacc_values:
        pv_fc_w = sum(f / (1 + w / 100.0) ** e for f, e in zip(fcff, exps) if _is_num(f))
        row = []
        for g in growth_values:
            if w <= g:
                row.append(None)
                continue
            tv_w_g = fcff_last * (1 + g / 100.0) / (w / 100.0 - g / 100.0)
            pv_tv_w_g = tv_w_g / (1 + w / 100.0) ** t_terminal
            equity = max(0.0, pv_fc_w + pv_tv_w_g + bridge_adj)
            row.append(round(equity))
        series.append({"name": f"WACC {_fmt_pct(w, 2)}", "values": row})

    # A deeply negative base case floors nearly every cell to 0 (SaaShop: 24 of
    # 25 cells) — an all-zero grid carries no sensitivity information and reads
    # as a rendering error, so suppress the matrix entirely.
    cells = [v for s in series for v in s["values"] if _is_num(v)]
    if cells and sum(1 for v in cells if v == 0) / len(cells) > 0.8:
        return None

    return {
        "type": "chart", "chart_id": "wacc_growth_sensitivity",
        "title": "Herkkyys: WACC x pitkän aikavälin kasvu (oman pääoman arvo, tEUR) "
                 "— likimääräinen malli, kalibroitu DCF-laskelman lukuihin",
        "chart_type": "heatmap_or_matrix", "unit": "tEUR",
        "x_axis": [_fmt_pct(g) for g in growth_values],
        "series": series, "status": "available",
        # The renderer frames this cell and colours every other cell relative
        # to it, so the reader can find "today's assumptions" in the grid.
        "base_row": wacc_values.index(wacc_base),
        "base_col": growth_values.index(g_base),
        "_wacc_values": wacc_values, "_growth_values": growth_values,
    }


def _revenue_margin_matrix(pv_forecast_base, pv_terminal_base, bridge_adj,
                            base_rev, base_margin):
    if not base_rev or not base_margin:
        return None
    # The linear proxy (equity ~ scale x base EV) assumes value moves WITH
    # rev x margin. When the base EV is negative or the base margin is
    # non-positive, a more negative margin flips `scale` negative and the
    # matrix claims "worse margin -> higher equity" (SaaShop 2026-07-10:
    # EBIT-% -3,2 % showed 2 334 tEUR while the base 0,8 % showed 0).
    # No proxy output beats a nonsense one — skip the matrix entirely.
    if base_margin <= 0 or (pv_forecast_base + pv_terminal_base) <= 0:
        return None
    # Unrounded, like the WACC grid: a margin rounded to 0,1 pp before scaling
    # left the centre cell 15 tEUR off the base value (Virnex 6,12 % -> 6,1 %).
    rev_values = [base_rev * s for s in _REV_SCALES]
    margin_values = [base_margin + d for d in _MARGIN_STEPS_PP]
    series = []
    for rev in rev_values:
        row = []
        for margin in margin_values:
            scale = (rev * margin) / (base_rev * base_margin)
            equity = max(0.0, scale * (pv_forecast_base + pv_terminal_base) + bridge_adj)
            row.append(round(equity))
        series.append({"name": _fmt_teur(rev), "values": row})
    return {
        "type": "chart", "chart_id": "revenue_ebit_sensitivity",
        "title": "Herkkyys: liikevaihto x EBIT-% (oman pääoman arvo, tEUR) "
                 "— likimääräinen malli, olettaa kassavirran skaalautuvan suoraan EBIT:n mukana",
        "chart_type": "heatmap_or_matrix", "unit": "tEUR",
        "x_axis": [_fmt_pct(m) for m in margin_values],
        "series": series, "status": "available",
        "base_row": _REV_SCALES.index(1.0),
        "base_col": _MARGIN_STEPS_PP.index(0.0),
        "_rev_values": rev_values, "_margin_values": margin_values,
    }


def _cell(matrix, row, col):
    try:
        v = matrix["series"][row]["values"][col]
    except (IndexError, KeyError, TypeError):
        return None
    return v if _is_num(v) else None


def _signed_pct(v, base):
    pct = round(100.0 * (v - base) / base)
    if pct == 0:
        return "±0 %"
    return f"{'+' if pct > 0 else '−'}{abs(pct)} %"


def _what_if_drivers(wg, rm, last_year):
    """One row per assumption: the value with that assumption moved one step
    down/up and everything else held at the base case. Every number is read
    from a cell of the two matrices (never recomputed), so the summary can
    never disagree with the grids printed right below it."""
    drivers = []
    if wg:
        r, c = wg["base_row"], wg["base_col"]
        w, g = wg["_wacc_values"], wg["_growth_values"]
        if 0 < r < len(w) - 1:
            # Higher WACC lowers the value: the "low value" side is the row below.
            drivers.append({
                "label": "Tuottovaatimus (WACC)", "base": _fmt_pct(w[r], 2),
                "low_label": _fmt_pct(w[r + 1], 2), "low": _cell(wg, r + 1, c),
                "high_label": _fmt_pct(w[r - 1], 2), "high": _cell(wg, r - 1, c),
            })
        if 0 < c < len(g) - 1:
            drivers.append({
                "label": "Kasvu ennustejakson jälkeen", "base": _fmt_pct(g[c]),
                "low_label": _fmt_pct(g[c - 1]), "low": _cell(wg, r, c - 1),
                "high_label": _fmt_pct(g[c + 1]), "high": _cell(wg, r, c + 1),
            })
    if rm:
        r, c = rm["base_row"], rm["base_col"]
        rev, m = rm["_rev_values"], rm["_margin_values"]
        yr = f" {last_year}" if last_year else ""
        drivers.append({
            "label": f"Kannattavuus (EBIT-%){yr}", "base": _fmt_pct(m[c]),
            "low_label": _fmt_pct(m[c - 1]), "low": _cell(rm, r, c - 1),
            "high_label": _fmt_pct(m[c + 1]), "high": _cell(rm, r, c + 1),
        })
        drivers.append({
            "label": f"Liikevaihto{yr}", "base": _fmt_teur(rev[r]),
            "low_label": "−10 %", "low": _cell(rm, r - 1, c),
            "high_label": "+10 %", "high": _cell(rm, r + 1, c),
        })
    drivers = [d for d in drivers if _is_num(d["low"]) and _is_num(d["high"])]
    drivers.sort(key=lambda d: abs(d["high"] - d["low"]), reverse=True)
    return drivers


def _what_if_blocks(wg, rm, last_year, equity):
    """'Mitä jos?' summary: a tornado chart plus one plain-language sentence
    per assumption, ordered by how much the value moves. The base is the
    reported equity value, not a matrix centre cell: the centre can sit a few
    tEUR off because the implied growth axis is rounded."""
    if not (wg or rm) or not _is_num(equity) or equity <= 0:
        return []
    base = round(equity)
    drivers = _what_if_drivers(wg, rm, last_year)
    if len(drivers) < 2:
        return []

    def effect(v):
        # A floored cell is not a "-100 %" loss estimate; say what happened.
        if v == 0:
            return f"{_fmt_teur(v)} (arvo rajattu nollaan)"
        return f"{_fmt_teur(v)} ({_signed_pct(v, base)})"

    lines = [f"**{d['label']}** (oletus {d['base']}): "
             f"{d['low_label']} → {effect(d['low'])}, {d['high_label']} → {effect(d['high'])}."
             for d in drivers]
    top = drivers[0]
    # "DCF-arvo", not "perusskenaario": when the base case blends several
    # methods (AWAKE.AI: DCF 1 144 vs base case 762 tEUR) these matrices only
    # describe the DCF leg.
    intro = (
        f"Kuvaaja näyttää, miten DCF-laskelman oman pääoman arvo "
        f"{_fmt_teur(base)} muuttuu, kun yhtä oletusta muutetaan kerrallaan ja "
        f"muut pidetään ennallaan. Arvo on herkin oletukselle "
        f"**{top['label']}**: kohtuullinen muutos siinä liikuttaa arvoa "
        f"{_fmt_teur(top['low'])}–{_fmt_teur(top['high'])}. Luvut ovat samat kuin "
        f"alla olevien herkkyystaulukoiden soluissa, ja ne ovat likimääräisiä."
    )
    return [
        {
            "type": "chart", "chart_id": "what_if_sensitivity",
            "chart_type": "tornado", "unit": "tEUR",
            "title": "Mitä jos? Yhden oletuksen muutoksen vaikutus DCF-arvoon "
                     "(oman pääoman arvo, tEUR)",
            "base_value": base, "items": drivers, "status": "available",
        },
        {"type": "paragraph", "block_id": "what_if_intro", "text": intro},
    ] + [{"type": "paragraph", "block_id": "what_if_line", "text": t} for t in lines]


def _historical_best_margin(input_data):
    inc = ((input_data or {}).get("actuals") or {}).get("income_statement") or {}
    ebit, ns = inc.get("ebit") or [], inc.get("net_sales") or []
    margins = [100.0 * e / s for e, s in zip(ebit, ns)
               if _is_num(e) and _is_num(s) and s > 0]
    return max(margins) if margins else None


def build_terminal_margin_range_blocks(input_data):
    """Show the base-case equity value against an alternative computed at the
    company's best HISTORICALLY ACHIEVED EBIT margin, so a terminal assumption
    well above anything the company has reached is presented as a range, not a
    single point. Approximate: scales only the terminal value by the margin
    ratio, reusing the engine-calibrated PV split (see module docstring)."""
    ve = (input_data or {}).get("valuation_engine") or {}
    dcf = ve.get("dcf") or {}
    disc = dcf.get("discounted_fcff")
    cum = dcf.get("cumulative_discounted_fcff")
    equity_gt = dcf.get("equity_value_before_floor")
    if not (isinstance(disc, list) and disc and isinstance(cum, list) and cum
            and _is_num(cum[0]) and _is_num(equity_gt)):
        return []
    ebit_pct = ((input_data or {}).get("forecast") or {}).get("ebit_pct")
    base_margin = (ebit_pct[-1] if isinstance(ebit_pct, list) and ebit_pct
                   and _is_num(ebit_pct[-1]) else None)
    alt_margin = _historical_best_margin(input_data)
    # Only meaningful when the terminal assumption is materially above the best
    # margin the company has actually achieved (the reviewer's exact concern).
    if not (_is_num(base_margin) and base_margin > 0 and _is_num(alt_margin)
            and alt_margin < base_margin - 2.0):
        return []
    # The scaled alt value is floored at 0 below, but the base row shows the
    # UNFLOORED equity — when the base equity is already negative, the pair
    # reads inverted ("-9,3 % -> 0 tEUR" next to "0,8 % -> -1 508 tEUR",
    # SaaShop 2026-07-10). A negative best-ever margin also flips the terminal
    # value's sign, which the linear scaling can't represent. Skip both cases:
    # the range only informs when the base case is positive.
    if equity_gt <= 0 or alt_margin <= 0:
        return []
    pv_forecast = sum(x for x in disc if _is_num(x))
    pv_terminal = cum[0] - pv_forecast
    bridge_adj = equity_gt - cum[0]
    alt_equity = max(0.0, pv_forecast + pv_terminal * (alt_margin / base_margin) + bridge_adj)
    return [
        {
            "type": "table",
            "table_id": "deterministic_terminal_margin_range",
            "title": "Oman pääoman arvo vaihtoehtoisella terminaali-EBIT-marginaalilla",
            "unit": "tEUR",
            "columns": ["Terminaali-EBIT-%", "Oman pääoman arvo"],
            "rows": [
                [f"{_fmt_pct(base_margin)} (konservatiivinen perusskenaario)", _fmt_teur(equity_gt)],
                [f"{_fmt_pct(alt_margin)} (paras toteutunut)", _fmt_teur(alt_equity)],
            ],
        },
        {
            "type": "paragraph",
            "text": (
                f"Konservatiivinen perusskenaario olettaa terminaalivuoden EBIT-marginaaliksi "
                f"{_fmt_pct(base_margin)}. Jos terminaalimarginaali jää yhtiön "
                f"parhaaseen toteutuneeseen tasoon {_fmt_pct(alt_margin)}, DCF:n "
                f"oman pääoman arvo on likimäärin {_fmt_teur(alt_equity)} — arvon "
                f"herkkyys terminaalioletukselle on siis suuri. Laskelma on "
                f"likimääräinen: se skaalaa vain terminaaliarvon marginaalin "
                f"suhteessa eikä muuta ennustejakson kassavirtoja."
            ),
        },
    ]


def _matrices(input_data):
    """(wacc x growth matrix, revenue x margin matrix, last forecast year,
    base equity) — matrices still carry their private axis keys."""
    ve = (input_data or {}).get("valuation_engine") or {}
    dcf = ve.get("dcf") or {}
    wacc_base = (ve.get("wacc_parameters") or {}).get("wacc_pct")
    fcff = dcf.get("fcff")
    disc = dcf.get("discounted_fcff")
    cum = dcf.get("cumulative_discounted_fcff")
    equity_gt = dcf.get("equity_value_before_floor")

    have_basics = (
        isinstance(fcff, list) and isinstance(disc, list) and fcff and disc
        and len(fcff) == len(disc)
        and isinstance(cum, list) and cum and _is_num(cum[0])
        and _is_num(equity_gt) and _is_num(wacc_base)
    )
    if not have_basics:
        return None, None, None, None

    bridge_adj = equity_gt - cum[0]

    wg = _wacc_growth_matrix(fcff, disc, cum, equity_gt, wacc_base, bridge_adj)

    forecast = (input_data or {}).get("forecast") or {}
    net_sales = forecast.get("net_sales")
    ebit_pct = forecast.get("ebit_pct")
    rm = None
    if isinstance(net_sales, list) and net_sales and isinstance(ebit_pct, list) and ebit_pct:
        pv_forecast_base = sum(x for x in disc if _is_num(x))
        pv_terminal_base = cum[0] - pv_forecast_base
        rm = _revenue_margin_matrix(
            pv_forecast_base, pv_terminal_base, bridge_adj,
            net_sales[-1] if _is_num(net_sales[-1]) else None,
            ebit_pct[-1] if _is_num(ebit_pct[-1]) else None,
        )

    years = forecast.get("years")
    last_year = years[-1] if isinstance(years, list) and years else None
    return wg, rm, last_year, equity_gt


def build_sensitivity_blocks(input_data):
    """Returns a list of ready-to-render `chart` blocks (possibly empty if the
    given data doesn't support the calculation)."""
    wg, rm, _, _ = _matrices(input_data)
    # Axis values are only needed to label the what-if rows.
    return [{k: v for k, v in m.items() if not k.startswith("_")}
            for m in (wg, rm) if m]


def build_what_if_blocks(input_data):
    """'Mitä jos?' tornado + sentences, read off the same matrices that
    build_sensitivity_blocks returns (empty when they can't be built)."""
    wg, rm, last_year, equity = _matrices(input_data)
    return _what_if_blocks(wg, rm, last_year, equity)
