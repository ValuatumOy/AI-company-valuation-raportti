"""Attach a missing source investment row only to a matching saved forecast."""

import math


def _number(value):
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        return False
    try:
        return math.isfinite(value)
    except OverflowError:
        return False


def _years(values):
    if not isinstance(values, list) or not values:
        return None
    years = []
    for value in values:
        if isinstance(value, bool) or not isinstance(value, (int, str)):
            return None
        text = str(value).strip()
        if len(text) != 4 or not text.isdigit():
            return None
        years.append(int(text))
    return years if len(set(years)) == len(years) else None


async def add_source_investing_cashflow(data, identifier):
    """Enrich the execution copy of a legacy FI snapshot, without rerunning it."""
    if not isinstance(data, dict):
        return data
    meta = data.get("meta") or {}
    if meta.get("data_source") != "profinder" or meta.get("country_code", "FI") != "FI":
        return data
    dcf = (data.get("valuation_engine") or {}).get("dcf") or {}
    cashflows = dcf.get("fcff")
    if not isinstance(cashflows, list) or not any(_number(value) for value in cashflows):
        return data
    saved_years = dcf.get("years")
    investing = dcf.get("investing_cash_flow")
    applicable = [i for i, value in enumerate(cashflows) if _number(value)]
    if (isinstance(saved_years, list) and isinstance(investing, list)
            and len(investing) == len(saved_years) == len(cashflows)
            and all(_number(investing[i]) for i in applicable)):
        return data

    years = _years(saved_years)
    if years is None or len(years) != len(cashflows):
        raise ValueError("Vanhan ajon ennustevuodet ja kassavirtarivit eivät kohdistu turvallisesti.")
    forecast_years = (data.get("forecast") or {}).get("years")
    if forecast_years is not None:
        normalized = _years(forecast_years)
        if normalized is None or set(normalized) != set(years):
            raise ValueError("Vanhan ajon ennustevuodet ja DCF-vuodet eivät vastaa toisiaan.")
    identifier_text = str(identifier).strip() if identifier is not None and not isinstance(identifier, bool) else ""
    if not identifier_text.isdigit() or int(identifier_text) <= 0:
        raise ValueError("Vanhan Profinder-ajon investointikassavirtaa ei voida varmistaa ilman mallin tunnistetta.")
    fid = int(identifier_text)

    from . import valuatum

    names = ("cr_cf_gross_capex_nega", "free_cash_flow_to_firm",
             "gross_cap_expenditure_nega", "operating_cash_flow")
    poses = [{"varName": name, "relPos": f"Y+{offset}"}
             for name in names for offset in range(10)]
    try:
        response = await valuatum.modeldata([fid], poses)
        model = response[str(fid)]
        source = model["dataMap"]
        current = model["currentYear"]
        if not isinstance(source, dict) or isinstance(current, bool) or not str(current).isdigit():
            raise ValueError("Invalid source forecast metadata")
        current = int(current)
    except Exception as exc:
        raise ValueError("Vanhan ajon investointikassavirtaa ei voitu tarkistaa lähdedatasta.") from exc

    anchors = (("fcff", "free_cash_flow_to_firm"),
               ("gross_capex", "gross_cap_expenditure_nega"),
               ("operating_cash_flow", "operating_cash_flow"))
    if isinstance(investing, list) and len(investing) > len(years):
        raise ValueError("Vanhan ajon investointikassavirran vuosirivit eivät kohdistu turvallisesti.")
    values = (list(investing) if isinstance(investing, list) else [])
    values.extend([None] * (len(years) - len(values)))
    for i in applicable:
        year = years[i]
        row = source.get(str(year))
        if not current <= year <= current + 9 or not isinstance(row, dict):
            raise ValueError("Vanhan ajon ennustevuosia ei voitu yhdistää nykyiseen lähdemalliin.")
        for saved_key, source_key in anchors:
            saved = dcf.get(saved_key)
            expected = saved[i] if isinstance(saved, list) and i < len(saved) else None
            actual = row.get(source_key)
            if not _number(expected) or not _number(actual):
                raise ValueError("Vanhan ajon investointikassavirran täsmäytyksestä puuttuu lähdelukuja.")
            actual_teur = actual * 1000
            if not _number(actual_teur) or not math.isclose(actual_teur, expected, abs_tol=0.01, rel_tol=1e-8):
                raise ValueError("Vanhan ajon kassavirrat eivät vastaa nykyistä lähdemallia; investointikassavirtaa ei voi yhdistää turvallisesti.")
        source_investing = row.get("cr_cf_gross_capex_nega")
        if not _number(source_investing) or not _number(source_investing * 1000):
            raise ValueError("Vanhan ajon investointikassavirtaa ei ollut saatavilla lähdedatassa.")
        source_value = source_investing * 1000
        known = values[i]
        if known is not None and (not _number(known) or not math.isclose(known, source_value, abs_tol=0.01, rel_tol=1e-8)):
            raise ValueError("Vanhan ajon investointikassavirta ei vastaa nykyistä lähdemallia.")
        if known is None:
            values[i] = source_value
    dcf["investing_cash_flow"] = values
    return data
