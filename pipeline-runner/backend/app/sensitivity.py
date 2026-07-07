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


def _fmt_pct(v):
    s = f"{v:.1f}"
    if s.endswith(".0"):
        s = s[:-2]
    return s.replace(".", ",") + " %"


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

    wacc_values = sorted({round(wacc_base + d, 2) for d in _WACC_STEPS_PP if wacc_base + d > 0})
    growth_values = sorted({round(g_base + d, 2) for d in _GROWTH_STEPS_PP})

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
        series.append({"name": f"WACC {_fmt_pct(w)}", "values": row})

    return {
        "type": "chart", "chart_id": "wacc_growth_sensitivity",
        "title": "Herkkyys: WACC x pitkän aikavälin kasvu (oman pääoman arvo, tEUR) "
                 "— likimääräinen malli, kalibroitu perusskenaarion lukuihin",
        "chart_type": "heatmap_or_matrix", "unit": "tEUR",
        "x_axis": [_fmt_pct(g) for g in growth_values],
        "series": series, "status": "available",
    }


def _revenue_margin_matrix(pv_forecast_base, pv_terminal_base, bridge_adj,
                            base_rev, base_margin):
    if not base_rev or not base_margin:
        return None
    rev_values = [round(base_rev * s) for s in _REV_SCALES]
    margin_values = [round(base_margin + d, 1) for d in _MARGIN_STEPS_PP]
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
    }


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
                [f"{_fmt_pct(base_margin)} (perusskenaario)", _fmt_teur(equity_gt)],
                [f"{_fmt_pct(alt_margin)} (paras toteutunut)", _fmt_teur(alt_equity)],
            ],
        },
        {
            "type": "paragraph",
            "text": (
                f"Perusskenaario olettaa terminaalivuoden EBIT-marginaaliksi "
                f"{_fmt_pct(base_margin)}. Jos terminaalimarginaali jää yhtiön "
                f"parhaaseen toteutuneeseen tasoon {_fmt_pct(alt_margin)}, DCF:n "
                f"oman pääoman arvo on likimäärin {_fmt_teur(alt_equity)} — arvon "
                f"herkkyys terminaalioletukselle on siis suuri. Laskelma on "
                f"likimääräinen: se skaalaa vain terminaaliarvon marginaalin "
                f"suhteessa eikä muuta ennustejakson kassavirtoja."
            ),
        },
    ]


def build_sensitivity_blocks(input_data):
    """Returns a list of ready-to-render `chart` blocks (possibly empty if the
    given data doesn't support the calculation)."""
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
        return []

    bridge_adj = equity_gt - cum[0]
    blocks = []

    wg = _wacc_growth_matrix(fcff, disc, cum, equity_gt, wacc_base, bridge_adj)
    if wg:
        blocks.append(wg)

    forecast = (input_data or {}).get("forecast") or {}
    net_sales = forecast.get("net_sales")
    ebit_pct = forecast.get("ebit_pct")
    if isinstance(net_sales, list) and net_sales and isinstance(ebit_pct, list) and ebit_pct:
        pv_forecast_base = sum(x for x in disc if _is_num(x))
        pv_terminal_base = cum[0] - pv_forecast_base
        rm = _revenue_margin_matrix(
            pv_forecast_base, pv_terminal_base, bridge_adj,
            net_sales[-1] if _is_num(net_sales[-1]) else None,
            ebit_pct[-1] if _is_num(ebit_pct[-1]) else None,
        )
        if rm:
            blocks.append(rm)

    return blocks
