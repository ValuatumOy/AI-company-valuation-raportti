"""Reconcile DCF depreciation under gross and net investment presentations."""

import math


def _number(value):
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        return False
    try:
        return math.isfinite(value)
    except OverflowError:
        return False


def _at(values, index):
    return values[index] if isinstance(values, list) and 0 <= index < len(values) else None


def _near(a, b):
    return math.isclose(a, b, abs_tol=0.05, rel_tol=1e-8)


def _operating_base(dcf, index):
    operating_parts = []
    for key in ("ebit", "taxes_paid", "change_in_working_capital", "tax_fin_expenses", "tax_fin_income"):
        value = _at(dcf.get(key), index)
        if key in ("ebit", "taxes_paid", "change_in_working_capital") and not _number(value):
            return None
        if value is not None and not _number(value):
            return None
        operating_parts.append(value or 0)
    return sum(operating_parts)


def _other_cashflows(dcf, index):
    extras = []
    for key in ("change_in_non_interest_bearing_financial_liabilities", "other_items_fcf"):
        value = _at(dcf.get(key), index)
        if value is not None and not _number(value):
            return None
        extras.append(value or 0)
    return sum(extras)


def _netted_year(dcf, index, depreciation):
    """Prove, from source rows, that adding depreciation also changes capex."""
    net = _at(dcf.get("gross_capex"), index)
    investing = _at(dcf.get("investing_cash_flow"), index)
    operating = _at(dcf.get("operating_cash_flow"), index)
    fcff = _at(dcf.get("fcff"), index)
    base, extras = _operating_base(dcf, index), _other_cashflows(dcf, index)
    if not all(_number(x) for x in (net, investing, operating, fcff, base, extras)):
        return False
    return (_near(net - investing, abs(depreciation))
            and _near(operating, base)
            and _near(fcff, operating + net + extras))


def _explicit_year(dcf, index, addback):
    """Check an explicit addback reaches the same source FCFF exactly once."""
    base, extras = _operating_base(dcf, index), _other_cashflows(dcf, index)
    investing = _at(dcf.get("investing_cash_flow"), index)
    fcff = _at(dcf.get("fcff"), index)
    if not all(_number(x) for x in (base, extras, investing, fcff)):
        return False
    operating = _at(dcf.get("operating_cash_flow"), index)
    capex = _at(dcf.get("gross_capex"), index)
    if operating is not None and (not _number(operating)
                                  or not (_near(operating, base) or _near(operating, base + addback))):
        return False
    if capex is not None and (not _number(capex) or not _near(capex, investing)):
        return False
    return _near(fcff, base + addback + investing + extras)


def netted_years(data):
    """Years where the source gross investment and DCF net line reconcile."""
    forecast = (data or {}).get("forecast") or {}
    statement = forecast.get("income_statement") or {}
    years, expense = forecast.get("years"), statement.get("depreciation_total")
    dcf = (((data or {}).get("valuation_engine") or {}).get("dcf") or {})
    dcf_years, addbacks = dcf.get("years"), dcf.get("depreciation_total")
    if not all(isinstance(x, list) for x in (years, expense, dcf_years)):
        return set()
    if (any(not isinstance(y, (int, str)) or isinstance(y, bool) for y in years + dcf_years)
            or len(set(years)) != len(years) or len(set(dcf_years)) != len(dcf_years)):
        return set()
    out = set()
    for i, year in enumerate(years):
        dep = _at(expense, i)
        if not _number(dep) or year not in dcf_years:
            continue
        j = dcf_years.index(year)
        addback = _at(addbacks, j)
        if (addback is None or (_number(addback) and _near(addback, 0))) and _netted_year(dcf, j, dep):
            out.add(year)
    return out


def blockers(data):
    forecast = (data or {}).get("forecast") or {}
    statement = forecast.get("income_statement") or {}
    years = forecast.get("years") or []
    expense = statement.get("depreciation_total")
    dcf = (((data or {}).get("valuation_engine") or {}).get("dcf") or {})
    dcf_years, addbacks = dcf.get("years"), dcf.get("depreciation_total")
    cashflows = dcf.get("fcff")
    has_cashflows = isinstance(cashflows, list) and any(_number(x) for x in cashflows)
    if not (isinstance(years, list) and years and isinstance(expense, list)
            and isinstance(dcf_years, list) and dcf_years):
        return (["DCF:n poistojen palautusta ei voi varmistaa: tuloslaskelman tai DCF:n vuosirivit puuttuvat"]
                if has_cashflows else [])
    if (any(not isinstance(y, (int, str)) or isinstance(y, bool) for y in years + dcf_years)
            or len(set(years)) != len(years) or len(set(dcf_years)) != len(dcf_years)):
        return ["DCF:n poistojen palautusta ei voi varmistaa: vuosirivit ovat virheelliset tai toistuvat"]
    addbacks = addbacks if isinstance(addbacks, list) else []
    netted = netted_years(data)
    missing = []
    for i, year in enumerate(years):
        amount = expense[i] if i < len(expense) else None
        if not _number(amount):
            if has_cashflows and year in dcf_years:
                j = dcf_years.index(year)
                if j < len(cashflows) and _number(cashflows[j]):
                    missing.append(str(year))
            continue
        if abs(amount) <= 1:
            if year in dcf_years:
                j = dcf_years.index(year)
                addback = _at(addbacks, j)
                if _number(addback) and abs(addback) > 2:
                    missing.append(str(year))
            continue
        try:
            j = dcf_years.index(year)
        except ValueError:
            missing.append(str(year))
            continue
        addback = addbacks[j] if j < len(addbacks) else None
        if year not in netted:
            matched = _number(addback) and addback > 1 and _near(addback, abs(amount))
            if not matched or (has_cashflows and not _explicit_year(dcf, j, addback)):
                missing.append(str(year))
    if has_cashflows:
        for j, year in enumerate(dcf_years):
            if j < len(cashflows) and _number(cashflows[j]) and year not in years:
                missing.append(str(year))
            operating_free = _at(dcf.get("free_operating_cash_flow"), j)
            if operating_free is not None:
                other = _at(dcf.get("other_items_fcf"), j)
                total = _at(cashflows, j)
                if (not _number(operating_free) or not _number(total)
                        or (other is not None and not _number(other))
                        or not _near(operating_free + (other or 0), total)):
                    missing.append(str(year))
    if not missing:
        return []
    return ["DCF:n poistojen ja investointien käsittely puuttuu, ei täsmää tai on epäselvä vuosina " + ", ".join(dict.fromkeys(missing))
            + "; ennusteen poistoja ja niiden käsittelyä ei voi varmistaa: "
            "lähdemallin kassavirta ja terminaalilaskelma on tarkistettava"]


def require_review(data):
    problems = blockers(data)
    if problems:
        raise ValueError("Arvonmäärityksen kassavirta vaatii tarkistuksen: " + "; ".join(problems))
