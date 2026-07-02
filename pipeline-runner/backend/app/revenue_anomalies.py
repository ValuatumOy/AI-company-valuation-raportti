"""Detect abrupt historical revenue changes for targeted enrichment searches.

The LLM sees a lot of JSON and can miss a simple but important question:
"did this company actually grow organically, or did the revenue base change?"
This helper turns the historical revenue series into a compact investigation
brief that stage 1 can use for web searches.
"""

from __future__ import annotations


RATIO_UP = 2.0
RATIO_DOWN = 0.5
MIN_ABS_CHANGE_TEUR = 250.0


def _num(v):
    if isinstance(v, bool) or v is None:
        return None
    if isinstance(v, (int, float)):
        return float(v)
    if isinstance(v, str):
        s = (
            v.replace("\u2212", "-")
            .replace("\u00a0", "")
            .replace("\u202f", "")
            .replace(" ", "")
            .replace(",", ".")
        )
        try:
            return float(s)
        except ValueError:
            return None
    return None


def _series_from_parallel(years, values):
    if not isinstance(years, list) or not isinstance(values, list):
        return []
    out = []
    for y, v in zip(years, values):
        year = _num(y)
        val = _num(v)
        if year is not None and val is not None:
            out.append({"year": int(year), "revenue_teur": val})
    return out


def _series_from_records(records):
    if not isinstance(records, list):
        return []
    out = []
    revenue_keys = (
        "revenue_teur",
        "revenue",
        "net_sales",
        "liikevaihto",
        "turnover",
        "sales",
    )
    for item in records:
        if not isinstance(item, dict):
            continue
        year = _num(item.get("year") or item.get("vuosi"))
        value = None
        for key in revenue_keys:
            value = _num(item.get(key))
            if value is not None:
                break
        if year is not None and value is not None:
            out.append({"year": int(year), "revenue_teur": value})
    return out


def revenue_series(input_data):
    """Return sorted historical revenue points as tEUR when discoverable."""
    if not isinstance(input_data, dict):
        return []

    actuals = input_data.get("actuals")
    if isinstance(actuals, dict):
        years = actuals.get("years")
        income = actuals.get("income_statement") or {}
        for key in ("net_sales", "revenue", "liikevaihto", "turnover", "sales"):
            series = _series_from_parallel(years, income.get(key))
            if len(series) >= 2:
                return sorted(series, key=lambda x: x["year"])
        for key in ("net_sales", "revenue", "liikevaihto", "turnover", "sales"):
            series = _series_from_parallel(years, actuals.get(key))
            if len(series) >= 2:
                return sorted(series, key=lambda x: x["year"])

    for key in ("actuals", "financials", "history", "historical_financials"):
        series = _series_from_records(input_data.get(key))
        if len(series) >= 2:
            return sorted(series, key=lambda x: x["year"])

    return []


def _pct(prev, cur):
    if prev == 0:
        return None
    return (cur - prev) / abs(prev) * 100.0


def _search_terms(meta, from_year, to_year, direction):
    name = ""
    yid = ""
    if isinstance(meta, dict):
        name = meta.get("company_name") or meta.get("name") or ""
        yid = meta.get("y_tunnus") or meta.get("business_id") or ""
    target = f'"{name}"' if name else "yritys"
    year_terms = f"{from_year} {to_year}"
    if direction == "increase":
        event_terms = [
            "yrityskauppa",
            "acquisition",
            "fuusio",
            "uusi sopimus",
            "major contract",
            "liiketoimintakauppa",
            "revenue recognition",
            "IFRS 15",
        ]
    else:
        event_terms = [
            "liiketoiminnan myynti",
            "divestment",
            "discontinued operations",
            "menetti sopimuksen",
            "major customer lost",
            "uudelleenjarjestely",
            "revenue recognition",
            "IFRS 15",
        ]
    id_part = f" {yid}" if yid else ""
    return [f"{target}{id_part} {term} {year_terms}" for term in event_terms]


def detect(input_data):
    """Return a compact anomaly brief for prompt context.

    The threshold is intentionally conservative: roughly doubling, halving, or
    moving from/to zero, with at least a 250 tEUR absolute change.
    """
    series = revenue_series(input_data)
    anomalies = []
    meta = input_data.get("meta") if isinstance(input_data, dict) else {}
    for prev, cur in zip(series, series[1:]):
        p = prev["revenue_teur"]
        c = cur["revenue_teur"]
        change = c - p
        if abs(change) < MIN_ABS_CHANGE_TEUR:
            continue
        ratio = None if p == 0 else c / p
        pct = _pct(p, c)
        is_increase = (p <= 0 < c) or (ratio is not None and ratio >= RATIO_UP)
        is_decrease = (p > 0 and c <= 0) or (ratio is not None and ratio <= RATIO_DOWN)
        if not (is_increase or is_decrease):
            continue
        direction = "increase" if change > 0 else "decrease"
        anomalies.append({
            "from_year": prev["year"],
            "to_year": cur["year"],
            "from_revenue_teur": round(p, 1),
            "to_revenue_teur": round(c, 1),
            "change_teur": round(change, 1),
            "change_pct": None if pct is None else round(pct, 1),
            "direction": direction,
            "search_terms": _search_terms(meta, prev["year"], cur["year"], direction),
        })
    return {
        "has_anomaly": bool(anomalies),
        "series": series,
        "threshold": {
            "increase_ratio_at_least": RATIO_UP,
            "decrease_ratio_at_most": RATIO_DOWN,
            "min_abs_change_teur": MIN_ABS_CHANGE_TEUR,
        },
        "anomalies": anomalies,
        "instruction": (
            "If has_anomaly is true, investigate public sources for structural "
            "or accounting explanations before treating the move as organic growth."
        ),
    }
