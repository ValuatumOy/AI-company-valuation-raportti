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
        r = _row(label, _align(_get_list(per_emp, key), n))
        if r:
            rows.append(r)

    ebit_row = _row("Liiketulos / henkilö", _ebit_per_employee(_align(_get_list(inc, "ebit"), n), hc))
    if ebit_row:
        rows.append(ebit_row)

    net_row = _row("Nettotulos / henkilö", _align(_get_list(per_emp, "net_earnings"), n))
    if net_row:
        rows.append(net_row)

    if len(rows) <= 1:  # only headcount, no ratio actually has data
        return []

    return [{
        "type": "table",
        "table_id": "deterministic_headcount_efficiency",
        "title": "Henkilöstötehokkuus",
        "unit": "Henkilöstö: lkm, muut rivit €/henkilö",
        "columns": ["Erä"] + [str(y) for y in years],
        "rows": rows,
    }]
