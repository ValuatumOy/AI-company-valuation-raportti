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
