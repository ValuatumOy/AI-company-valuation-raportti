"""Use source accounting dates without changing financial model indices."""

from datetime import date


def bounds(actuals, index):
    periods = actuals.get("periods")
    if not isinstance(periods, list) or not 0 <= index < len(periods):
        return None
    period = periods[index]
    if not isinstance(period, dict):
        return None
    try:
        start = date.fromisoformat(period.get("start"))
        end = date.fromisoformat(period.get("end"))
    except (TypeError, ValueError):
        return None
    return (start, end) if start <= end else None


def annual(period):
    return period is not None and 365 <= (period[1] - period[0]).days + 1 <= 366


def label(actuals, index):
    if "periods" not in actuals:
        return str(actuals["years"][index])
    period = bounds(actuals, index)
    if period is None:
        return "—"
    start, end = period
    if start == date(end.year, 1, 1) and end == date(end.year, 12, 31):
        return str(end.year)
    return f"{start.day}.{start.month}.{start.year} – {end.day}.{end.month}.{end.year}"


def forecast_baseline_comparable(actuals, forecast):
    period = bounds(actuals, len(actuals.get("years") or []) - 1)
    years = forecast.get("years") or []
    return (annual(period) and bool(years)
            and period[1].month == 12 and period[1].day == 31
            and str(period[1].year + 1) == str(years[0]))


def omit_incomparable_forecast_growth(data):
    actuals = data.get("actuals") or {}
    forecast = data.get("forecast") or {}
    if "periods" not in actuals or forecast_baseline_comparable(actuals, forecast):
        return
    for key in ("forecast", "forecast_parameters"):
        growth = (data.get(key) or {}).get("net_sales_growth_pct")
        if isinstance(growth, list) and growth:
            growth[0] = None


def writer_directive(data):
    if not isinstance(data, dict):
        return ""
    actuals = data.get("actuals") or {}
    if "periods" not in actuals:
        return ""
    directive = (
        "Historical actuals.periods are aligned by index with actuals.years. "
        "Annual model slots are internal keys. Identify actuals by the source period dates; "
        "include both dates for a shortened, extended or fiscal-year-shifted period. "
        "If source dates are unavailable, say so and avoid assigning a calendar year. "
        "Only calculate historical annual growth across consecutive full fiscal years. "
        "Do not annualize partial-period revenue or invent missing data. "
    )
    if not forecast_baseline_comparable(actuals, (data or {}).get("forecast") or {}):
        directive += (
            "The first forecast year has no comparable verified prior annual period. "
            "Omit first-year growth against historical actuals and explain the source forecast "
            "still depends on its starting assumptions."
        )
    return directive
