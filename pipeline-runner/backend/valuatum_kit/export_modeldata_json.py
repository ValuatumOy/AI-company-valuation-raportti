#!/usr/bin/env python3
"""Export Valuatum /modeldata into a structured valuation JSON payload.

The source fetch script prints Markdown tables. This wrapper imports it, asks
for the same modeldata, and maps the available variables into the JSON shape
used by the valuation workflow.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import uuid
import urllib.error
import urllib.request
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any


EXTRA_VARS = [
    "cr_employees",
    "employees",
    "headcount",
    "number_of_employees",
    "no_of_employees",
    "avg_number_of_employees",
    "cr_ns_per_employee",
    "cr_added_value_per_employee",
    "cr_employee_expenses_per_employee",
    "cr_ebitda_per_employee",
    "cr_net_earnings_per_employee",
    "other_operating_income",
    "gross_profit",
    "personnel_costs",
    "other_operating_costs",
    "dep_total_nega",
    "ebitda",
    "ebit_without_extras",
    "extras_in_ebit",
    "interest_expenses",
    "net_earnings",
    "development_costs",
    "intangibles_total",
    "other_intangible_rights",
    "inventories",
    "trade_receivables",
    "cash_and_equivalents",
    "cash",
    "cash_prev_year",
    "equity_incl_capital_loans",
    "capital_loans",
    "loans_from_fin_institutions",
    "loans_from_associated",
    "advances_received",
    "trade_payables",
    "non_interest_bearing_debt",
    "gearing_percent",
    "market_value_of_associated",
    "market_value_of_minorities_nega",
    "dcf_dividends",
    "pv_of_eva_ty",
    "pv_of_cap_base_change",
]


def load_fetch_module(path: Path) -> Any:
    spec = importlib.util.spec_from_file_location("fetch_modeldata", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Could not import fetch script from {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def extend_fetch_vars(module: Any) -> None:
    module.TABLES.append((999999, "JSON export extras", EXTRA_VARS))


def as_num(value: Any) -> float | int | None:
    if value is None or isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def roundish(value: float | int | None) -> float | int | None:
    if value is None:
        return None
    rounded = round(float(value), 6)
    if rounded.is_integer():
        return int(rounded)
    return rounded


def y_tunnus(code: str | None) -> str | None:
    if not code:
        return None
    clean = code.removesuffix("K")
    if len(clean) == 8 and clean.isdigit():
        return f"{clean[:7]}-{clean[7]}"
    return code


def raw(data_map: dict[str, dict[str, Any]], year: int, var: str) -> Any:
    return data_map.get(str(year), {}).get(var)


def first_available(data_map: dict[str, dict[str, Any]], year: int, vars_: list[str]) -> Any:
    for var in vars_:
        value = raw(data_map, year, var)
        if value is not None:
            return value
    return None


def arr(
    data_map: dict[str, dict[str, Any]],
    years: list[int],
    vars_: str | list[str],
    *,
    money: bool = False,
    pct: bool = False,
) -> list[Any]:
    names = [vars_] if isinstance(vars_, str) else vars_
    out = []
    for year in years:
        value = as_num(first_available(data_map, year, names))
        if isinstance(value, (int, float)):
            if money:
                value = value * 1000
            if pct:
                value = value * 100
            value = roundish(value)
        out.append(value)
    return out


def scalar(
    data_map: dict[str, dict[str, Any]],
    year: int | None,
    vars_: str | list[str],
    *,
    money: bool = False,
    pct: bool = False,
) -> Any:
    if year is None:
        return None
    values = arr(data_map, [year], vars_, money=money, pct=pct)
    return values[0] if values else None


def coalesce(*values: Any) -> Any:
    for value in values:
        if value is not None:
            return value
    return None


def mcp_credit_risk(company_code: str, period_type: str = "annual", limit: int = 10) -> dict[str, Any] | None:
    url = os.environ.get("VALU_MCP_PROFINDER_URL")
    if not url:
        return None

    def post(payload: dict[str, Any]) -> dict[str, Any]:
        req = urllib.request.Request(
            url,
            data=json.dumps(payload).encode("utf-8"),
            method="POST",
            headers={
                "accept": "application/json, text/event-stream",
                "content-type": "application/json",
            },
        )
        with urllib.request.urlopen(req, timeout=60) as resp:
            return json.loads(resp.read().decode("utf-8"))

    try:
        post(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2025-03-26",
                    "capabilities": {},
                    "clientInfo": {"name": "modeldata-json-export", "version": "1.0.0"},
                },
            }
        )
        response = post(
            {
                "jsonrpc": "2.0",
                "id": 2,
                "method": "tools/call",
                "params": {
                    "name": "valu_creditrisk",
                    "arguments": {
                        "companyCode": company_code,
                        "period_type": period_type,
                        "limit": limit,
                    },
                },
            }
        )
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, json.JSONDecodeError):
        return None

    result = response.get("result", {})
    if result.get("isError"):
        return None
    text = result.get("structuredContent", {}).get("result")
    if text is None:
        content = result.get("content") or []
        text = content[0].get("text") if content else None
    if not text:
        return None
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return None


def credit_risk_payload(credit: dict[str, Any] | None) -> dict[str, Any]:
    if not credit or not credit.get("creditHistory"):
        return {
            "available": False,
            "years": [],
            "company_bankruptcy_risk_pct": [],
            "industry_bankruptcy_risk_pct": [],
            "rating": [],
        }

    rows = sorted(credit["creditHistory"], key=lambda item: item.get("year", 0))
    return {
        "available": True,
        "years": [item.get("year") for item in rows],
        "company_bankruptcy_risk_pct": [
            roundish(item.get("bankruptcyRisk") * 100) if item.get("bankruptcyRisk") is not None else None
            for item in rows
        ],
        "industry_bankruptcy_risk_pct": [None for _ in rows],
        "rating": [item.get("creditRating") for item in rows],
    }


def build_flags(data_map: dict[str, dict[str, Any]], years: list[int]) -> list[dict[str, str]]:
    flags: list[dict[str, str]] = []
    rois = arr(data_map, years, "roi_before_tax_avg_cap", pct=True)
    ebits = arr(data_map, years, "ebit", money=True)
    inconsistent_years = [
        year for year, roi, ebit in zip(years, rois, ebits) if roi is not None and ebit is not None and roi > 0 and ebit < 0
    ]
    if inconsistent_years:
        flags.append(
            {
                "field": "key_ratios.roi_pct",
                "issue": f"ROI positive while EBIT is negative in years {inconsistent_years}; check denominator/sign convention before using in analysis",
                "severity": "high",
            }
        )

    roe = arr(data_map, years, "roe_percent", pct=True)
    missing_roe = [year for year, value in zip(years, roe) if value is None]
    if missing_roe:
        flags.append(
            {
                "field": "key_ratios.roe_pct",
                "issue": f"ROE missing for years {missing_roe}",
                "severity": "medium",
            }
        )

    headcount = arr(data_map, years, ["cr_employees", "employees", "headcount", "number_of_employees", "no_of_employees"])
    if all(value is None for value in headcount):
        flags.append(
            {
                "field": "headcount.values",
                "issue": "Headcount was not available from /modeldata for the requested variable candidates",
                "severity": "medium",
            }
        )

    return flags


def build_payload(model: dict[str, Any], credit: dict[str, Any] | None) -> dict[str, Any]:
    data_map = model.get("dataMap", {})
    years = sorted((int(year) for year in data_map), key=int)
    current_year = int(model.get("currentYear") or min(years))
    actual_years = [year for year in years if year < current_year]
    forecast_years = [year for year in years if year >= current_year]
    first_forecast = forecast_years[0] if forecast_years else None
    latest_actual = actual_years[-1] if actual_years else None
    code = str(model.get("companyCode") or "")

    forecast = {
        "years": forecast_years,
        "terminal": "TRM",
        "unit": "tEUR",
        "is_system_deterministic": True,
        "net_sales": arr(data_map, forecast_years, "ns", money=True),
        "net_sales_growth_pct": arr(data_map, forecast_years, "ns_growth", pct=True),
        "ebitda": arr(data_map, forecast_years, ["cr_ebitda_xml", "ebitda"], money=True),
        "ebit": arr(data_map, forecast_years, "ebit", money=True),
        "ebit_pct": arr(data_map, forecast_years, "ebit_percent", pct=True),
        "free_cash_flow_to_firm": arr(data_map, forecast_years, "free_cash_flow_to_firm", money=True),
        "interest_bearing_debt": arr(data_map, forecast_years, "liab_ib_total", money=True),
        "equity_excl_capital_loans": arr(data_map, forecast_years, "cr_shareholders_equity", money=True),
    }

    return {
        "meta": {
            "company_name": model.get("companyName"),
            "y_tunnus": y_tunnus(code),
            "industry": None,
            "industry_code": None,
            "domicile": None,
            "founded": None,
            "report_date": date.today().isoformat(),
            "currency": model.get("currency", "EUR"),
            "unit": "tEUR",
            "level": "consolidated" if code.endswith("K") else "parent",
            "data_source": "profinder",
            "run_id": str(uuid.uuid4()),
            "run_timestamp": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        },
        "headcount": {
            "years": actual_years,
            "values": arr(
                data_map,
                actual_years,
                ["cr_employees", "employees", "headcount", "number_of_employees", "no_of_employees", "avg_number_of_employees"],
            ),
        },
        "actuals": {
            "years": actual_years,
            "unit": "tEUR",
            "income_statement": {
                "net_sales": arr(data_map, actual_years, "ns", money=True),
                "other_operating_income": arr(data_map, actual_years, "other_operating_income", money=True),
                "gross_profit": arr(data_map, actual_years, "gross_profit", money=True),
                "personnel_costs": arr(data_map, actual_years, "personnel_costs", money=True),
                "other_operating_costs": arr(data_map, actual_years, "other_operating_costs", money=True),
                "depreciation_total": arr(data_map, actual_years, "dep_total_nega", money=True),
                "ebitda": arr(data_map, actual_years, ["cr_ebitda_xml", "ebitda"], money=True),
                "ebit": arr(data_map, actual_years, "ebit", money=True),
                "ebit_without_extras": arr(data_map, actual_years, "ebit_without_extras", money=True),
                "extras_in_ebit": arr(data_map, actual_years, "extras_in_ebit", money=True),
                "interest_expenses": arr(data_map, actual_years, "interest_expenses", money=True),
                "net_earnings": arr(data_map, actual_years, "net_earnings", money=True),
            },
            "balance_sheet": {
                "development_costs": arr(data_map, actual_years, "development_costs", money=True),
                "intangibles_total": arr(data_map, actual_years, ["intangibles_total", "other_intangible_rights"], money=True),
                "tangible_assets": arr(data_map, actual_years, "tangible_ass", money=True),
                "inventories": arr(data_map, actual_years, "inventories", money=True),
                "trade_receivables": arr(data_map, actual_years, ["trade_receivables", "cr_curr_trade_debtors"], money=True),
                "cash_and_equivalents": arr(data_map, actual_years, ["cash_and_equivalents", "cash"], money=True),
                "total_assets": arr(data_map, actual_years, "bs_total_assets", money=True),
                "equity_excl_capital_loans": arr(data_map, actual_years, "cr_shareholders_equity", money=True),
                "equity_incl_capital_loans": arr(data_map, actual_years, "equity_incl_capital_loans", money=True),
                "capital_loans": arr(data_map, actual_years, "capital_loans", money=True),
                "loans_from_fin_institutions": arr(data_map, actual_years, "loans_from_fin_institutions", money=True),
                "loans_from_associated": arr(data_map, actual_years, "loans_from_associated", money=True),
                "advances_received": arr(data_map, actual_years, "advances_received", money=True),
                "trade_payables": arr(data_map, actual_years, ["trade_payables", "cr_current_trade_creditors"], money=True),
                "interest_bearing_debt": arr(data_map, actual_years, "liab_ib_total", money=True),
                "non_interest_bearing_debt": arr(data_map, actual_years, "non_interest_bearing_debt", money=True),
            },
            # Per-employee ratios the engine already computes, in tEUR/employee —
            # confirmed live: unscaled values rounded to 0/-0 across every year
            # (e.g. 66 200 €/employee came back as 0.0662), so these are millions
            # like every other money field. headcount_efficiency.py scales x1000
            # to a plain EUR/employee figure at render time.
            "per_employee": {
                "net_sales": arr(data_map, actual_years, "cr_ns_per_employee", money=True),
                "value_added": arr(data_map, actual_years, "cr_added_value_per_employee", money=True),
                "personnel_costs": arr(data_map, actual_years, "cr_employee_expenses_per_employee", money=True),
                "ebitda": arr(data_map, actual_years, "cr_ebitda_per_employee", money=True),
                "net_earnings": arr(data_map, actual_years, "cr_net_earnings_per_employee", money=True),
            },
        },
        "forecast": forecast,
        "forecast_parameters": {
            "years": forecast_years,
            "net_sales_growth_pct": arr(data_map, forecast_years, "ns_growth", pct=True),
            "ebit_pct": arr(data_map, forecast_years, "ebit_percent", pct=True),
            "capex_pct_of_sales": arr(data_map, forecast_years, "inv_gross_per_ns", pct=True),
            "working_capital": {
                "materials_pct_of_sales": arr(data_map, forecast_years, "cr_raw_materials_pct", pct=True),
                "trade_receivables_pct_of_sales": arr(data_map, forecast_years, "cr_curr_trade_debtors_pct", pct=True),
                "trade_payables_pct_of_sales": arr(data_map, forecast_years, "cr_current_trade_creditors_pct", pct=True),
            },
            "tax_rate_pct": arr(data_map, forecast_years, "tax_rate", pct=True),
            "dividend_payout_pct": arr(data_map, forecast_years, "cr_payout_ratio", pct=True),
        },
        "valuation_engine": {
            "unit": "tEUR",
            "wacc_parameters": {
                "risk_free_rate_pct": scalar(data_map, first_forecast, "riskfree_interest_rate", pct=True),
                "market_risk_premium_pct": scalar(data_map, first_forecast, "market_risk_premium", pct=True),
                "liquidity_premium_pct": scalar(data_map, first_forecast, "liquidity_premium", pct=True),
                "equity_beta": scalar(data_map, first_forecast, "equity_beta"),
                "cost_of_equity_pct": scalar(data_map, first_forecast, "cost_of_equity", pct=True),
                "cost_of_debt_pct": scalar(data_map, first_forecast, "cost_of_debt", pct=True),
                "tax_rate_wacc_pct": scalar(data_map, first_forecast, "tax_rate_wacc", pct=True),
                "target_d_to_de_pct": scalar(data_map, first_forecast, "target_dde", pct=True),
                "wacc_pct": scalar(data_map, first_forecast, "wacc", pct=True),
            },
            "dcf": {
                "years": forecast_years,
                "terminal": "TRM",
                "ebit": arr(data_map, forecast_years, "ebit", money=True),
                "depreciation_total": arr(data_map, forecast_years, "dep_total_nega", money=True),
                "taxes_paid": arr(data_map, forecast_years, "taxes_paid", money=True),
                "tax_fin_expenses": arr(data_map, forecast_years, "tax_fin_expenses", money=True),
                "tax_fin_income": arr(data_map, forecast_years, "tax_fin_income", money=True),
                "change_in_working_capital": arr(data_map, forecast_years, "change_in_wc_nega", money=True),
                "operating_cash_flow": arr(data_map, forecast_years, "operating_cash_flow", money=True),
                "change_in_non_interest_bearing_financial_liabilities": arr(
                    data_map, forecast_years, "change_in_lt_liab_nib", money=True
                ),
                "gross_capex": arr(data_map, forecast_years, "gross_cap_expenditure_nega", money=True),
                "free_operating_cash_flow": arr(data_map, forecast_years, "free_operating_cash_flow", money=True),
                "other_items_fcf": arr(data_map, forecast_years, "other_items_fcf", money=True),
                "fcff": arr(data_map, forecast_years, "free_cash_flow_to_firm", money=True),
                "discounted_fcff": arr(data_map, forecast_years, "disc_fcff", money=True),
                "cumulative_discounted_fcff": arr(data_map, forecast_years, "cum_disc_fcff", money=True),
                "bridge": {
                    "interest_bearing_debt": scalar(data_map, first_forecast, "ib_debt_nega_prev_year", money=True),
                    "cash": scalar(data_map, first_forecast, "cash_prev_year", money=True),
                    "associated_market_value": scalar(data_map, first_forecast, "market_value_of_associated", money=True),
                    "minority_market_value": scalar(data_map, first_forecast, "market_value_of_minorities_nega", money=True),
                    "prev_year_dividends": scalar(data_map, first_forecast, "dcf_dividends", money=True),
                },
                "equity_value_before_floor": scalar(data_map, first_forecast, "value_of_equity_fcff", money=True),
                "no_of_shares_total": scalar(data_map, first_forecast, "no_of_shares_total"),
                "fair_value_dcf": scalar(data_map, first_forecast, "fair_value_fcff", money=True),
            },
            "eva": {
                "years": forecast_years,
                "terminal": "TRM",
                "noplat": arr(data_map, forecast_years, "noplat", money=True),
                "cost_of_capital": arr(data_map, forecast_years, "cost_of_cap_abs", money=True),
                "eva": arr(data_map, forecast_years, "eva", money=True),
                "discounted_eva": arr(data_map, forecast_years, "disc_eva", money=True),
                "pv_of_trm_eva": scalar(data_map, first_forecast, "pv_of_eva_ty", money=True),
                "pv_of_cap_base_change": scalar(data_map, first_forecast, "pv_of_cap_base_change", money=True),
                "invested_capital": scalar(data_map, first_forecast, "prol_cap_invested", money=True),
                "bridge": {
                    "interest_bearing_debt": scalar(data_map, first_forecast, "ib_debt_nega_prev_year", money=True),
                    "cash": scalar(data_map, first_forecast, "cash_prev_year", money=True),
                },
                "equity_value_before_floor_raw": scalar(data_map, first_forecast, "value_of_equity_eva", money=True),
                "equity_value_before_floor": coalesce(
                    scalar(data_map, first_forecast, "value_of_equity_fcff", money=True),
                    scalar(data_map, first_forecast, "value_of_equity_eva", money=True),
                ),
                "equivalence_note": (
                    "EVA is normalized to DCF equity value when both use the same Valuatum forecast "
                    "and WACC; the raw EVA engine value is retained in equity_value_before_floor_raw."
                ),
            },
        },
        "key_ratios": {
            "years": years,
            "roi_pct": arr(data_map, years, "roi_before_tax_avg_cap", pct=True),
            "roe_pct": arr(data_map, years, "roe_percent", pct=True),
            "equity_ratio_pct": arr(data_map, years, "equity_ratio", pct=True),
            "gearing_pct": arr(data_map, years, "gearing_percent", pct=True),
            "capital_turnover": arr(data_map, years, "asset_turnover"),
            "eva": arr(data_map, years, "eva", money=True),
        },
        "credit_risk": credit_risk_payload(credit),
        "peers": [],
        "client_reported_signals": [],
        "flags": build_flags(data_map, years),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--fetch-script", required=True, type=Path)
    parser.add_argument("--fid", required=True, type=int)
    parser.add_argument("--actuals", default=5, type=int)
    parser.add_argument("--estimates", default=10, type=int)
    parser.add_argument("--token", default=None)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--company-code-override", default=None)
    parser.add_argument("--company-name-override", default=None)
    args = parser.parse_args()

    module = load_fetch_module(args.fetch_script)
    extend_fetch_vars(module)
    token = args.token or os.environ.get("VALUATUM_TOKEN") or module.TOKEN
    model = module.fetch_modeldata(args.fid, args.actuals, args.estimates, token)
    if args.company_code_override or args.company_name_override:
        model = dict(model)
        if args.company_code_override:
            model["companyCode"] = args.company_code_override
        if args.company_name_override:
            model["companyName"] = args.company_name_override
    credit = mcp_credit_risk(str(model.get("companyCode") or ""))
    payload = build_payload(model, credit)

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
