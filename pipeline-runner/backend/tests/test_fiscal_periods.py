import asyncio

from app import financials, fiscal_periods, revenue_anomalies, runner
from valuatum_kit.export_modeldata_json import fiscal_periods as export_periods


def test_short_accounting_period_keeps_report_eligible_and_removes_false_growth():
    years = [2023, 2024, 2025]
    periods = export_periods({
        "2023": {"cr_fiscal_year_start": 20230601, "cr_fiscal_year_end": 20240531},
        "2024": {"cr_fiscal_year_start": 20240601, "cr_fiscal_year_end": 20250531},
        "2025": {"cr_fiscal_year_start": 20250601, "cr_fiscal_year_end": 20251231},
    }, years)
    source = {
        "actuals": {"years": years, "periods": periods,
                    "income_statement": {"net_sales": [1026, 2166, 5000]}},
        "forecast": {"years": [2026, 2027], "net_sales_growth_pct": [63, 12]},
        "forecast_parameters": {"net_sales_growth_pct": [63, 12]},
        "valuation_engine": {"dcf": {"fcff": [-5000, 1000]}},
    }
    stage = {"id": "source", "order": 0, "name": "Source", "model": runner.DATA_FETCHER_MODEL}

    result = asyncio.run(runner._execute_stage(stage, {}, source, None, {}))

    assert result["status"] == "ok", result
    prepared = result["parsed_json"]
    assert prepared["actuals"]["years"] == years
    assert prepared["forecast"]["net_sales_growth_pct"] == [None, 12]
    assert prepared["forecast_parameters"]["net_sales_growth_pct"] == [None, 12]
    assert prepared["valuation_engine"]["dcf"] == source["valuation_engine"]["dcf"]
    assert source["forecast"]["net_sales_growth_pct"] == [63, 12]
    table = financials.build_financial_statement_blocks(prepared)["5"][0]
    assert table["columns"][1:] == [
        "1.6.2023 – 31.5.2024", "1.6.2024 – 31.5.2025", "1.6.2025 – 31.12.2025",
    ]
    anomalies = revenue_anomalies.detect(prepared)["anomalies"]
    assert [(point["from_year"], point["to_year"]) for point in anomalies] == [(2024, 2025)]
    assert "Omit first-year growth" in fiscal_periods.writer_directive(prepared)


def test_calendar_year_baseline_keeps_comparable_forecast_growth():
    source = {
        "actuals": {"years": [2025], "periods": [{"start": "2025-01-01", "end": "2025-12-31"}]},
        "forecast": {"years": [2026], "net_sales_growth_pct": [10]},
    }
    fiscal_periods.omit_incomparable_forecast_growth(source)
    assert source["forecast"]["net_sales_growth_pct"] == [10]
    assert fiscal_periods.label(source["actuals"], 0) == "2025"


def test_missing_accounting_dates_do_not_stop_report_preparation():
    source = {
        "actuals": {"years": [2025], "periods": [{"start": None, "end": None}],
                    "income_statement": {"net_sales": [2000]}},
        "forecast": {"years": [2026], "net_sales_growth_pct": [15]},
    }
    stage = {"id": "source", "order": 0, "name": "Source", "model": runner.DATA_FETCHER_MODEL}

    result = asyncio.run(runner._execute_stage(stage, {}, source, None, {}))

    assert result["status"] == "ok", result
    prepared = result["parsed_json"]
    assert prepared["forecast"]["net_sales_growth_pct"] == [None]
    table = financials.build_financial_statement_blocks(prepared)["5"][0]
    assert table["columns"][1] == "—"
    assert next(row for row in table["rows"] if row[0] == "Liikevaihto")[1] == "2 000"


def test_young_company_charts_and_headcount_table_drop_empty_leading_slots():
    from app import headcount_efficiency, render

    empty = [None] * 5
    periods = [{"start": None, "end": None}] * 5 + [
        {"start": "2021-07-06", "end": "2022-10-31"},
        {"start": "2022-11-01", "end": "2023-10-31"},
        {"start": "2023-11-01", "end": "2024-10-31"},
        {"start": "2024-11-01", "end": "2025-10-31"},
    ]
    years = list(range(2017, 2026))
    data = {
        "actuals": {"years": years, "periods": periods,
                    "income_statement": {"net_sales": empty + [2578, 1791, 1701, 1817],
                                         "ebit": empty + [67, 72, 18, 23]},
                    "per_employee": {"net_sales": empty + [644.5, 447.75, 582.534, 605.667]}},
        "headcount": {"years": years, "values": empty + [4, 4, None, 3]},
    }
    table = headcount_efficiency.build_headcount_efficiency_blocks(data)[0]
    assert table["columns"][1:] == [fiscal_periods.label(data["actuals"], i) for i in range(5, 9)]
    assert all(len(r) == 5 for r in table["rows"])

    chart = {
        "type": "chart", "chart_type": "bar_line", "unit": "tEUR / %",
        "x_axis": [f"Lähdekausi puuttuu, paikka {i}" for i in range(1, 6)]
                  + ["6.7.2021–31.10.2022", "1.11.2022–31.10.2023",
                     "1.11.2023–31.10.2024", "1.11.2024–31.10.2025"],
        "series": [{"name": "Liikevaihto", "type": "bar", "values": empty + [2578, 1791, 1701, 1817]},
                   {"name": "EBIT-%", "type": "line", "values": empty + [2.6, 4.02, 1.06, 1.27]}],
    }
    svg = render._chart_svg(chart)
    assert "paikka" not in svg
    assert "ennuste alkaa" not in svg
    forecast = {**chart, "x_axis": ["2024", "2025", "2026e"],
                "series": [{"type": "bar", "values": [1, 2, 3]}]}
    assert "ennuste alkaa" in render._chart_svg(forecast)


def test_writer_table_drops_leading_empty_year_columns():
    from app import render
    cols = ["Mittari", "P1", "P2", "6.7.2021–31.10.2022", "1.11.2024–31.10.2025"]
    rows = [["Liikevaihto", "–", None, "2 578", "1 817"], ["Kassa", "", "—", "6", ""]]
    assert render._drop_leading_empty_columns(cols, rows) == (
        ["Mittari", "6.7.2021–31.10.2022", "1.11.2024–31.10.2025"],
        [["Liikevaihto", "2 578", "1 817"], ["Kassa", "6", ""]])
    assert render._drop_leading_empty_columns(["A", "B"], [["x", ""]]) == (["A", "B"], [["x", ""]])
