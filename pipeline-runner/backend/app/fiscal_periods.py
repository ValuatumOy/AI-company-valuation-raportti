"""Check whether model year slots can be used as annual accounting periods."""

import math
from datetime import date


def _has_data(actuals, index):
    for name in ("income_statement", "balance_sheet"):
        for values in (actuals.get(name) or {}).values():
            if isinstance(values, list) and index < len(values) and values[index] is not None:
                return True
    return False


def blockers(data):
    actuals = (data or {}).get("actuals") or {}
    periods = actuals.get("periods")
    years = actuals.get("years") or []
    if "periods" not in actuals:
        return []  # Older stored reports retain their original presentation.
    if not isinstance(periods, list) or (not periods and years):
        return ["tilikausien päivämäärät puuttuvat"]

    out = []
    spans = []
    if len(periods) != len(years):
        return ["tilikausien määrä ei vastaa historiallisten vuosipaikkojen määrää"]
    for i, period in enumerate(periods):
        if not isinstance(period, dict):
            if _has_data(actuals, i):
                out.append("tilikauden päivämäärät puuttuvat")
            continue
        start, end = period.get("start"), period.get("end")
        if start is None and end is None:
            if _has_data(actuals, i):
                out.append(f"vuosipaikan {period.get('model_year', '')} tilikauden päivämäärät puuttuvat")
            continue
        try:
            start_date, end_date = date.fromisoformat(start), date.fromisoformat(end)
        except (TypeError, ValueError):
            out.append("tilikauden alku tai loppu puuttuu tai on virheellinen")
            continue
        if end_date < start_date:
            out.append(f"tilikauden päivämäärät ovat väärässä järjestyksessä ({start}–{end})")
            continue
        if _has_data(actuals, i):
            spans.append((start_date, end_date))
        model_year = years[i] if i < len(years) else period.get("model_year")
        if model_year is not None and str(model_year) != str(end_date.year):
            out.append(f"mallin vuosipaikka {model_year} on tilikausi {start}–{end}")
        days = (end_date - start_date).days + 1
        if not 350 <= days <= 380:
            out.append(f"tilikausi {start}–{end} on {days} päivää")
    spans.sort()
    for previous, current in zip(spans, spans[1:]):
        if current[0] <= previous[1]:
            out.append(f"tilikaudet {previous[0]}–{previous[1]} ja {current[0]}–{current[1]} menevät päällekkäin")
    return list(dict.fromkeys(out))


def require_comparable(data):
    problems = blockers(data)
    if problems:
        raise ValueError(
            "Tilikaudet eivät ole automaattiseen arvonmääritykseen vertailukelpoisia: "
            + "; ".join(problems)
            + ". Tarkista tilikausien kohdistus ja ennusteen lähtötiedot ennen raportin tekemistä."
        )


async def add_legacy_source_periods(data, identifier):
    """Verify source periods for an old Profinder run before reusing its data."""
    meta = (data or {}).get("meta") or {}
    actuals = (data or {}).get("actuals") or {}
    if meta.get("data_source") != "profinder" or meta.get("country_code", "FI") != "FI":
        return data
    if not actuals.get("years"):
        raise ValueError("Profinder-ajon historialliset tilikaudet puuttuvat.")
    if isinstance(actuals.get("periods"), list) and actuals["periods"]:
        return data
    try:
        fid = int(identifier)
    except (TypeError, ValueError):
        raise ValueError("Vanhan Profinder-ajon tilikausia ei voida varmistaa ilman mallin tunnistetta.")

    from . import valuatum
    from valuatum_kit.export_modeldata_json import arr, fiscal_periods

    names = ("cr_fiscal_year_start", "cr_fiscal_year_end", "ns", "ebit", "bs_total_assets",
             "interest_bearing_liabilities_calc")
    poses = [{"varName": name, "relPos": f"Y-{offset}"}
             for name in names for offset in range(1, 10)]
    try:
        response = await valuatum.modeldata([fid], poses)
        source = response[str(fid)]["dataMap"]
    except Exception as exc:
        raise ValueError("Vanhan Profinder-ajon tilikausia ei voitu tarkistaa lähdedatasta.") from exc

    income = actuals.get("income_statement") or {}
    balance = actuals.get("balance_sheet") or {}
    anchors = ((income.get("net_sales") or [], "ns"),
               (income.get("ebit") or [], "ebit"),
               (balance.get("total_assets") or [], "bs_total_assets"))
    for i, year in enumerate(actuals["years"]):
        if not _has_data(actuals, i):
            continue
        row = source.get(str(year)) or {}
        matched = False
        for values, key in anchors:
            expected = values[i] if i < len(values) else None
            if not isinstance(expected, (int, float)) or isinstance(expected, bool):
                continue
            current = row.get(key)
            if (not isinstance(current, (int, float)) or isinstance(current, bool)
                    or not math.isfinite(current) or not math.isfinite(expected)
                    or not math.isclose(current * 1000, expected, abs_tol=0.01, rel_tol=1e-6)):
                raise ValueError("Vanhan ajon lähtötiedot eivät vastaa nykyistä lähdemallia; tilikausia ei voi yhdistää turvallisesti.")
            matched = True
        if not matched:
            raise ValueError("Vanhan ajon tilikausia ei voida yhdistää lähdemalliin ilman vertailukelpoisia lukuja.")
    actuals["periods"] = fiscal_periods(source, actuals["years"])
    if isinstance(actuals.get("balance_sheet"), dict):
        actuals["balance_sheet"]["interest_bearing_debt_source_total"] = arr(
            source, actuals["years"], "interest_bearing_liabilities_calc", money=True)
    return data
