"""Deterministic DCF detail blocks.

The LLM used to emit a vertical table with only Year / FCFF / Discounted FCFF.
That showed the final cash-flow output but not the moving parts a reader needs
to understand the DCF. This module builds a horizontal, year-by-year table from
the Valuatum input data so the report can show the drivers without inventing
missing components.
"""


def _is_num(x):
    return isinstance(x, (int, float)) and not isinstance(x, bool)


def _fmt_num(v, decimals=0):
    if not _is_num(v):
        return ""
    sign = "-" if v < 0 else ""
    a = abs(v)
    if decimals:
        s = f"{a:,.{decimals}f}"
        i, f = s.split(".")
        return f"{sign}{i.replace(',', ' ')},{f}"
    return f"{sign}{round(a):,.0f}".replace(",", " ")


def _fmt_pct(v, decimals=1):
    if not _is_num(v):
        return ""
    s = _fmt_num(v, decimals)
    if s.endswith(",0"):
        s = s[:-2]
    return f"{s} %"


def _get_list(obj, key):
    v = (obj or {}).get(key)
    return v if isinstance(v, list) else []


def _first_available_list(*lookups):
    for obj, key in lookups:
        values = _get_list(obj, key)
        if any(_is_num(v) for v in values):
            return values
    return []


def _pick_years(dcf, forecast):
    years = _get_list(dcf, "years") or _get_list(forecast, "years")
    return years if years else []


def _align(values, n):
    return [(values[i] if i < len(values) else None) for i in range(n)]


def _row(label, values, formatter):
    cells = [formatter(v) for v in values]
    if not any(c not in ("", None) for c in cells):
        return None
    return [label] + cells


def _first_num(values):
    for v in values or []:
        if _is_num(v):
            return v
    return None


def _terminal_pv(dcf, discounted, n):
    cumulative = _first_num(_get_list(dcf, "cumulative_discounted_fcff"))
    if not _is_num(cumulative):
        return None
    pv_forecast = sum(v for v in discounted[:n] if _is_num(v))
    terminal = cumulative - pv_forecast
    return terminal if abs(terminal) > 1e-9 else None


def _bridge_rows(dcf, pv_forecast, fmt=_fmt_num):
    cumulative = _first_num(_get_list(dcf, "cumulative_discounted_fcff"))
    bridge = dcf.get("bridge") or {}
    equity = dcf.get("equity_value_before_floor")
    rows = [["PV, ennustejakso yhteensä", fmt(pv_forecast)]]
    if _is_num(cumulative):
        rows.append(["Terminaaliarvon nykyarvo", fmt(cumulative - pv_forecast)])
        rows.append(["Yritysarvo (EV)", fmt(cumulative)])
    if _is_num(bridge.get("interest_bearing_debt")):
        rows.append(["Korolliset velat", fmt(bridge.get("interest_bearing_debt"))])
    if _is_num(bridge.get("cash")):
        rows.append(["Kassa", fmt(bridge.get("cash"))])
    if _is_num(bridge.get("associated_market_value")):
        rows.append(["Osakkuusyhtiöt / muut erät", fmt(bridge.get("associated_market_value"))])
    if _is_num(bridge.get("minority_market_value")):
        rows.append(["Vähemmistöosuudet", fmt(bridge.get("minority_market_value"))])
    if _is_num(bridge.get("prev_year_dividends")):
        rows.append(["Edellisen vuoden osingot", fmt(bridge.get("prev_year_dividends"))])
    if _is_num(equity):
        rows.append(["Oman pääoman arvo ennen lattiaa", fmt(equity)])
    return rows


def _waterfall_steps(dcf, ev):
    """Same numbers as _bridge_rows, shaped for the waterfall chart (raw floats,
    not formatted strings) — one source of truth for the EV -> equity bridge."""
    bridge = dcf.get("bridge") or {}
    equity = dcf.get("equity_value_before_floor")
    if not (_is_num(ev) and _is_num(equity)):
        return []
    steps = [{"label": "Yritysarvo (EV)", "value": ev, "kind": "start"}]
    for key, label in (
        ("interest_bearing_debt", "Korolliset velat"),
        ("cash", "Kassa"),
        ("associated_market_value", "Osakkuusyhtiöt / muut erät"),
        ("minority_market_value", "Vähemmistöosuudet"),
        ("prev_year_dividends", "Edellisen vuoden osingot"),
    ):
        v = bridge.get(key)
        if _is_num(v) and v != 0:
            steps.append({"label": label, "value": v, "kind": "delta"})
    steps.append({"label": "Oman pääoman arvo", "value": equity, "kind": "total"})
    return steps


def value_flow_figures(input_data):
    """EV / bridge delta / equity / WACC / forecast horizon for the plain-
    language value-flow diagram (Osa 2 divider) — same source as the EV→equity
    waterfall above, so the two can never show different numbers for the same
    bridge."""
    ve = (input_data or {}).get("valuation_engine") or {}
    dcf = ve.get("dcf") or {}
    wacc = ve.get("wacc_parameters") or {}
    forecast = (input_data or {}).get("forecast") or {}
    ev = _first_num(_get_list(dcf, "cumulative_discounted_fcff"))
    equity = dcf.get("equity_value_before_floor")
    out = {}
    if _is_num(ev):
        out["ev_teur"] = ev
    if _is_num(equity):
        out["equity_teur"] = equity
    if _is_num(ev) and _is_num(equity):
        out["bridge_delta_teur"] = equity - ev
    if _is_num(wacc.get("wacc_pct")):
        out["wacc_pct"] = wacc["wacc_pct"]
    years = [y for y in (forecast.get("years") or []) if y]
    if years:
        out["forecast_years"] = f"{years[0]}–{years[-1]}"
    return out or None


def _last(xs):
    for x in reversed(xs or []):
        if x not in (None, ""):
            return x
    return None


def _investment_grade(rating):
    """S&P-style: BBB- and up = investment grade; BB+ and below = speculative."""
    r = str(rating or "").strip().upper()
    if not r:
        return None
    if r.startswith(("AAA", "AA", "BBB")) or (r.startswith("A") and not r.startswith("B")):
        return True
    if r.startswith(("BB", "B", "CCC", "CC", "C", "D")):
        return False
    return None


def build_wacc_risk_caveat_blocks(input_data):
    """Transparency caveat when the engine's discount rate does not reflect the
    company's own credit risk (weak rating / high bankruptcy risk / negative
    equity paired with a benign cost of debt). Numbers are NOT changed — the WACC
    is an engine output; this only surfaces the tension the reader should weigh."""
    ve = (input_data or {}).get("valuation_engine") or {}
    wp = ve.get("wacc_parameters") or {}
    cr = (input_data or {}).get("credit_risk") or {}
    forecast = (input_data or {}).get("forecast") or {}
    cost_of_debt = wp.get("cost_of_debt_pct")
    liq = wp.get("liquidity_premium_pct")
    tgt = wp.get("target_d_to_de_pct")
    rating = _last(cr.get("rating"))
    bankruptcy = _first_num(list(reversed(cr.get("company_bankruptcy_risk_pct") or [])))
    equity_fc = [e for e in (forecast.get("equity_excl_capital_loans") or []) if _is_num(e)]
    neg_equity = bool(equity_fc) and all(e < 0 for e in equity_fc)

    weak = (_investment_grade(rating) is False) or (_is_num(bankruptcy) and bankruptcy >= 2.0) or neg_equity
    benign = (_is_num(cost_of_debt) and cost_of_debt <= 6.0) or neg_equity
    if not (weak and benign):
        return []

    risk_bits = []
    if rating:
        risk_bits.append(f"luottoluokitus {rating}")
    if _is_num(bankruptcy):
        risk_bits.append(f"konkurssiriski {_fmt_pct(bankruptcy)}")
    if neg_equity:
        risk_bits.append("oma pääoma on negatiivinen koko ennustejakson")
    param_bits = []
    if _is_num(cost_of_debt):
        param_bits.append(f"vieraan pääoman kustannus {_fmt_pct(cost_of_debt)}")
    if _is_num(liq):
        param_bits.append(f"likviditeettipreemio {_fmt_pct(liq)}")

    text = (
        "Diskonttokorko (WACC) tulee valuaatiomoottorista, eikä sitä ole tässä "
        "raportissa oikaistu yhtiön luottoriskillä. Huomioi jännite: "
        + (", ".join(risk_bits) or "kohonnut luottoriski")
        + ", mutta " + ("; ".join(param_bits) or "diskonttokoron komponentit")
        + " ovat maltilliset tähän riskitasoon nähden"
    )
    if _is_num(tgt) and neg_equity:
        text += (f". Lisäksi tavoitepääomarakenne D/(D+E) {_fmt_pct(tgt)} on "
                 "ristiriidassa negatiivisen oman pääoman kanssa (kiertokulkuoletus)")
    text += (". Todellinen tuottovaatimus voisi olla korkeampi, jolloin oman "
             "pääoman arvo olisi konservatiivista perusskenaariota matalampi.")
    return [{
        "type": "callout", "variant": "warning",
        "title": "Diskonttokoron ja luottoriskin suhde",
        "text": text,
    }]


def build_dcf_detail_blocks(input_data):
    ve = (input_data or {}).get("valuation_engine") or {}
    dcf = ve.get("dcf") or {}
    forecast = (input_data or {}).get("forecast") or {}
    years = _pick_years(dcf, forecast)
    fcff = _get_list(dcf, "fcff")
    discounted = _get_list(dcf, "discounted_fcff")
    if not years or not fcff or not discounted:
        # A wholly empty dcf block is a legitimate no-DCF case (e.g. a company
        # without forecasts) — stay silent and let the section's own "DCF puuttuu"
        # text stand. But a partly-populated dcf that carries driver rows yet is
        # missing the core FCFF/discounted series is a data defect worth surfacing
        # instead of silently falling back to a thinner table.
        partial = any(
            _get_list(dcf, k)
            for k in ("ebit", "depreciation_total", "operating_cash_flow", "gross_capex")
        )
        if dcf and partial:
            return [{
                "type": "callout",
                "variant": "warning",
                "title": "DCF-erittelyä ei voitu koota",
                "text": (
                    "Valuaatiomoottorin dcf-lohkosta puuttuu vuosi-, FCFF- tai "
                    "diskontattu FCFF -sarja, joten vuosittaista FCFF-erittelyä ei "
                    "voitu rakentaa. Tarkista vaiheen 0 datan "
                    "valuation_engine.dcf-kentät."
                ),
            }]
        return []

    n = min(len(years), len(fcff), len(discounted))
    years = years[:n]
    terminal_pv = _terminal_pv(dcf, discounted, n)
    terminal_label = str(dcf.get("terminal") or "TRM")
    cols = ["Erä"] + [str(y) for y in years]
    if _is_num(terminal_pv):
        cols.append(terminal_label)

    # Large caps: 12 columns of 9-digit tEUR figures overflow the fixed-layout
    # wide table, so re-express the whole section in the same unit the cover
    # picks for this magnitude (tEUR / M€ / mrd. €). Anchor on EV — the biggest
    # number the section shows.
    from .render import _scale_from_teur
    ev = _first_num(_get_list(dcf, "cumulative_discounted_fcff"))
    div, unit, dec = _scale_from_teur(max(
        (abs(v) for v in [ev, terminal_pv] + fcff[:n] if _is_num(v)), default=0))

    def fmt(v, decimals=dec):
        return _fmt_num(v / div, decimals) if _is_num(v) else ""

    rows = []

    def add(label, values, terminal_value=None):
        cells = _align(values, n)
        if _is_num(terminal_pv):
            cells.append(terminal_value if _is_num(terminal_value) else None)
        r = _row(label, cells, fmt)
        if r:
            rows.append(r)

    add("EBIT", _first_available_list((dcf, "ebit"), (forecast, "ebit")))
    add("+ Poistot", _get_list(dcf, "depreciation_total"))
    add("- Maksetut verot", _get_list(dcf, "taxes_paid"))
    add("- Rahoituskulujen verovaikutus", _get_list(dcf, "tax_fin_expenses"))
    add("+ Rahoitustuottojen verovaikutus", _get_list(dcf, "tax_fin_income"))
    add("- Käyttöpääoman muutos", _get_list(dcf, "change_in_working_capital"))
    add("Liiketoiminnan kassavirta", _get_list(dcf, "operating_cash_flow"))
    add(
        "+ Korottomien rahoitusvelkojen muutos",
        _get_list(dcf, "change_in_non_interest_bearing_financial_liabilities"),
    )
    add("- Bruttoinvestoinnit", _get_list(dcf, "gross_capex"))
    add("Operatiivinen vapaa kassavirta", _get_list(dcf, "free_operating_cash_flow"))
    add("+/- Muut erät", _get_list(dcf, "other_items_fcf"))
    add("Vapaa kassavirta (FCFF)", _first_available_list((dcf, "fcff"), (forecast, "free_cash_flow_to_firm")))
    add("Diskontattu FCFF", discounted, terminal_pv)
    # A true forward running sum of the discounted-FCFF row. The engine's
    # `cumulative_discounted_fcff` field is a reverse remaining-PV series (its
    # first element is the EV, consumed as such at lines 73/88 and in
    # sensitivity.py), NOT a cumulative sum — showing it verbatim under a
    # "Kumulatiivinen" label produced a non-monotonic, mislabeled row. The
    # running sum ends at PV(ennustejakso); its terminal cell is the EV
    # (PV-forecast + terminal PV), so the label is now accurate.
    running, _acc = [], 0.0
    for v in discounted[:n]:
        if _is_num(v):
            _acc += v
        running.append(_acc)
    add("Kumulatiivinen diskontattu FCFF", running, ev)

    if not rows:
        return []

    pv_forecast = sum(v for v in discounted[:n] if _is_num(v))
    blocks = [
        {
            "type": "table",
            "table_id": "deterministic_dcf_fcff_drivers",
            "title": "DCF-laskelma: FCFF ja nykyarvo vuosittain",
            "unit": unit,
            "columns": cols,
            "rows": rows,
        },
        {
            "type": "chart",
            "chart_id": "deterministic_ev_equity_waterfall",
            "chart_type": "waterfall",
            "title": "Arvon muodostuminen: yritysarvosta oman pääoman arvoon",
            "unit": unit,
            "steps": [{**s, "value": s["value"] / div}
                      for s in _waterfall_steps(dcf, ev)],
        },
        {
            "type": "table",
            "table_id": "deterministic_dcf_equity_bridge",
            "title": "Yritysarvosta oman pääoman arvoon",
            "unit": unit,
            "columns": ["Erä", "Arvo"],
            "rows": _bridge_rows(dcf, pv_forecast, fmt),
        },
        {
            "type": "callout",
            "variant": "info",
            "title": "Mistä diskontattu FCFF tulee?",
            "text": (
                "Taulukko seuraa Valuatumin DCF-rivejä: EBITistä edetään veroihin, "
                "käyttöpääoman muutokseen, investointeihin ja muihin eriin, joista "
                "muodostuu vapaa kassavirta (FCFF). Diskontattu FCFF on saman "
                "DCF-taulukon nykyarvorivi, ja ennustejakson diskontattujen FCFF:ien "
                f"summa on {fmt(pv_forecast)} {unit}. Tekoäly ei laske DCF:ää "
                "uudelleen, vaan esittää Valuatum-moottorin antamat rivit. "
                "Miinusmerkillä (−) alkavat rivit vähennetään kassavirrasta; "
                "tappiollisena vuonna rivi “− Maksetut verot” voi "
                "silti näyttää positiivista lukua, jolloin kyse on verohyödystä, "
                "joka kasvattaa vapaata kassavirtaa."
            ),
        },
    ]
    return blocks
