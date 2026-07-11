"""Deterministic personnel-efficiency block (Henkilöstötehokkuus).

Built from headcount plus the per-employee ratios the Valuatum engine already
computes (`actuals.per_employee`). EBIT/employee has no confirmed engine
field name yet, so it's derived locally from ebit and headcount instead of
guessing a varname. Never invented — a row is dropped entirely if none of its
years have data.
"""


def _is_num(x):
    return isinstance(x, (int, float)) and not isinstance(x, bool)


def _fmt_num(v):
    if not _is_num(v):
        return ""
    sign = "-" if v < 0 else ""
    a = abs(v)
    return f"{sign}{round(a):,.0f}".replace(",", " ")


def _get_list(obj, key):
    v = (obj or {}).get(key)
    return v if isinstance(v, list) else []


def _align(values, n):
    return [(values[i] if i < len(values) else None) for i in range(n)]


def _row(label, values):
    cells = [_fmt_num(v) for v in values]
    if not any(c for c in cells):
        return None
    return [label] + cells


def _ebit_per_employee(ebit_teur, headcount):
    out = []
    for e, h in zip(ebit_teur, headcount):
        if _is_num(e) and _is_num(h) and h != 0:
            out.append(e * 1000 / h)  # tEUR -> EUR/employee
        else:
            out.append(None)
    return out


def _to_eur(values_teur):
    """actuals.per_employee is fetched in tEUR/employee (see export_modeldata_json.py) —
    scale to a plain EUR/employee figure, same as the locally-derived EBIT row."""
    return [v * 1000 if _is_num(v) else None for v in values_teur]


# Personnel cost per head used to sanity-check reported headcount.
# ponytail: flat 50 tEUR/head heuristic (Esa's rule); refine with sector data if needed.
COST_PER_HEAD_TEUR = 50.0


def _implied_headcounts(hc, personnel_costs):
    """Years where reported headcount is implausible vs personnel costs.

    Source headcount is sometimes plain wrong (Valuatum 2017–18: 60 reported
    against ~260 tEUR personnel costs, so ~5 real people). When the implied
    headcount (|personnel costs| / 50 tEUR) is less than half the reported
    figure, use the implied value instead — same cross-check rule 29 already
    tells the prose writer to apply, now applied to the deterministic table.
    """
    corrected = {}
    for i, (h, pc) in enumerate(zip(hc, personnel_costs)):
        if _is_num(h) and h > 0 and _is_num(pc):
            implied = abs(pc) / COST_PER_HEAD_TEUR
            if implied >= 1 and h > 2 * implied:
                corrected[i] = max(1, round(implied))
    return corrected


def build_headcount_efficiency_blocks(input_data):
    headcount = (input_data or {}).get("headcount") or {}
    years = headcount.get("years")
    hc = headcount.get("values")
    if not isinstance(years, list) or not isinstance(hc, list) or not any(_is_num(v) for v in hc):
        return []

    n = len(years)
    hc = _align(hc, n)

    actuals = (input_data or {}).get("actuals") or {}
    inc = actuals.get("income_statement") or {}
    per_emp = actuals.get("per_employee") or {}

    corrected = _implied_headcounts(hc, _align(_get_list(inc, "personnel_costs"), n))
    # Engine per_employee ratios were computed with the reported headcount, so
    # a corrected year rescales them by reported/implied instead of needing
    # the underlying source values.
    scale = {i: hc[i] / corrected[i] for i in corrected}
    hc = [corrected.get(i, v) for i, v in enumerate(hc)]

    # Even after the personnel-cost cross-check some years stay implausible
    # (SaaShop 2018: 30 employees against 2 tEUR revenue -> 67 EUR
    # revenue/person). Under 1 tEUR of revenue per person no real company
    # operates — blank that year's per-person ratios; the headcount row itself
    # stays visible so the reader still sees the suspect source figure.
    net_sales = _align(_get_list(inc, "net_sales"), n)
    bad = {i for i in range(n)
           if _is_num(net_sales[i]) and _is_num(hc[i]) and hc[i] > 0
           and net_sales[i] / hc[i] < 1.0}

    def _masked(values):
        return [None if i in bad else v for i, v in enumerate(values)]

    def _rescaled(values):
        return [v * scale[i] if i in scale and _is_num(v) else v
                for i, v in enumerate(values)]

    rows = []
    hc_row = _row("Henkilöstö", hc)
    if hc_row:
        rows.append(hc_row)
    for label, key in (
        ("Liikevaihto / henkilö", "net_sales"),
        ("Jalostusarvo / henkilö", "value_added"),
        ("Henkilökulut / henkilö", "personnel_costs"),
        ("Käyttökate / henkilö", "ebitda"),
    ):
        r = _row(label, _masked(_to_eur(_rescaled(_align(_get_list(per_emp, key), n)))))
        if r:
            rows.append(r)

    ebit_row = _row("Liiketulos / henkilö",
                    _masked(_ebit_per_employee(_align(_get_list(inc, "ebit"), n), hc)))
    if ebit_row:
        rows.append(ebit_row)

    net_row = _row("Nettotulos / henkilö",
                   _masked(_to_eur(_rescaled(_align(_get_list(per_emp, "net_earnings"), n)))))
    if net_row:
        rows.append(net_row)

    if len(rows) <= 1:  # only headcount, no ratio actually has data
        return []

    blocks = [{
        "type": "table",
        "table_id": "deterministic_headcount_efficiency",
        "title": "Henkilöstötehokkuus",
        "unit": "Henkilöstö: lkm, muut rivit €/henkilö",
        "columns": ["Erä"] + [str(y) for y in years],
        "rows": rows,
    }]
    if corrected:
        yrs = ", ".join(str(years[i]) for i in sorted(corrected))
        blocks.append({
            "type": "callout",
            "variant": "info",
            "title": "Henkilöstömäärä korjattu",
            "text": (
                f"Lähdedatan ilmoittama henkilöstömäärä vuosilta {yrs} on "
                "epäuskottava suhteessa henkilöstökuluihin. Taulukossa on "
                "käytetty henkilöstökuluista johdettua arviota "
                f"(noin {_fmt_num(COST_PER_HEAD_TEUR)} tEUR/henkilö)."
            ),
        })
    return blocks
