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


def _bridge_rows(dcf, pv_forecast):
    cumulative = _first_num(_get_list(dcf, "cumulative_discounted_fcff"))
    bridge = dcf.get("bridge") or {}
    equity = dcf.get("equity_value_before_floor")
    rows = [["PV, ennustejakso yhteensä", _fmt_num(pv_forecast)]]
    if _is_num(cumulative):
        rows.append(["Terminaaliarvon nykyarvo", _fmt_num(cumulative - pv_forecast)])
        rows.append(["Yritysarvo (EV)", _fmt_num(cumulative)])
    if _is_num(bridge.get("interest_bearing_debt")):
        rows.append(["Korolliset velat", _fmt_num(bridge.get("interest_bearing_debt"))])
    if _is_num(bridge.get("cash")):
        rows.append(["Kassa", _fmt_num(bridge.get("cash"))])
    if _is_num(bridge.get("associated_market_value")):
        rows.append(["Osakkuusyhtiöt / muut erät", _fmt_num(bridge.get("associated_market_value"))])
    if _is_num(bridge.get("minority_market_value")):
        rows.append(["Vähemmistöosuudet", _fmt_num(bridge.get("minority_market_value"))])
    if _is_num(bridge.get("prev_year_dividends")):
        rows.append(["Edellisen vuoden osingot", _fmt_num(bridge.get("prev_year_dividends"))])
    if _is_num(equity):
        rows.append(["Oman pääoman arvo ennen lattiaa", _fmt_num(equity)])
    return rows


def build_dcf_detail_blocks(input_data):
    ve = (input_data or {}).get("valuation_engine") or {}
    dcf = ve.get("dcf") or {}
    forecast = (input_data or {}).get("forecast") or {}
    years = _pick_years(dcf, forecast)
    fcff = _get_list(dcf, "fcff")
    discounted = _get_list(dcf, "discounted_fcff")
    if not years or not fcff or not discounted:
        return []

    n = min(len(years), len(fcff), len(discounted))
    years = years[:n]
    terminal_pv = _terminal_pv(dcf, discounted, n)
    terminal_label = str(dcf.get("terminal") or "TRM")
    cols = ["Erä"] + [str(y) for y in years]
    if _is_num(terminal_pv):
        cols.append(terminal_label)

    rows = []

    def add(label, values, terminal_value=None):
        cells = _align(values, n)
        if _is_num(terminal_pv):
            cells.append(terminal_value if _is_num(terminal_value) else None)
        r = _row(label, cells, _fmt_num)
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
    add("Kumulatiivinen diskontattu FCFF", _get_list(dcf, "cumulative_discounted_fcff"), terminal_pv)

    if not rows:
        return []

    pv_forecast = sum(v for v in discounted[:n] if _is_num(v))
    blocks = [
        {
            "type": "table",
            "table_id": "deterministic_dcf_fcff_drivers",
            "title": "DCF-laskelma: FCFF ja nykyarvo vuosittain",
            "unit": "tEUR",
            "columns": cols,
            "rows": rows,
        },
        {
            "type": "table",
            "table_id": "deterministic_dcf_equity_bridge",
            "title": "Yritysarvosta oman pääoman arvoon",
            "unit": "tEUR",
            "columns": ["Erä", "Arvo"],
            "rows": _bridge_rows(dcf, pv_forecast),
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
                f"summa on {_fmt_num(pv_forecast)} tEUR. Tekoäly ei laske DCF:ää "
                "uudelleen, vaan esittää Valuatum-moottorin antamat rivit."
            ),
        },
    ]
    return blocks
