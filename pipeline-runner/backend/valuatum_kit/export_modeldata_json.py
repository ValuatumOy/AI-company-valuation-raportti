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
import urllib.parse
import urllib.request
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

try:
    from .config import api_base_url
except ImportError:  # Direct script execution.
    from config import api_base_url


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
    # Statement rows. Valuatum's canonical names for these are cr_-prefixed;
    # only a handful (ns, ebit, gross_profit, interest_expenses, net_earnings,
    # inventories) also resolve unprefixed. The un-prefixed spellings for
    # everything else — personnel_costs, cash_and_equivalents, capital_loans,
    # dep_total_nega, non_interest_bearing_debt … — are not Valuatum variables
    # at all: /modeldata silently drops unknown varNames, so they came back
    # empty on every model and the holes were filled by a second Profinder MCP
    # integration. Each name below was verified value-for-value against that
    # MCP backfill (fid 184362, 9 years) before it replaced it.
    "cr_other_operating_income",
    "cr_gross_profit",
    "gross_profit",
    "cr_employee_expenses",
    "cr_other_oper_expenses",
    "cr_depreciation",
    "ebitda",
    "ebit_without_extras",
    "extras_in_ebit",
    "cr_interest_expenses",
    "interest_expenses",
    "cr_net_earnings",
    "net_earnings",
    "cr_development_expenditure",
    "cr_intangible_assets_total",
    "other_intangible_rights",
    "cr_tangibles_assets_total",
    "inventories",
    "cr_curr_trade_debtors",
    "cr_cash_and_bank_deposits",
    # Y+0 only — the DCF bridge's cash figure, not an actuals series.
    "cash_prev_year",
    "fundu_equity_incl_cap_loans",
    "cr_capital_loan_lt",
    "cr_capital_loan_st",
    "cr_non_current_loans_from_credit_ins",
    "cr_current_loans_from_credit_ins",
    "cr_non_current_owed_to_participating",
    "cr_current_owed_to_participating",
    "cr_non_current_advances_received",
    "cr_current_advances_received",
    "cr_current_trade_creditors",
    "non_interest_bearing_liabilities_calc",
    # Credit risk. Fetched per fid like everything else — the Profinder MCP this
    # replaced was keyed by company code and returned the KONSERNI figures
    # whenever a group existed, so emo reports carried group risk (verified:
    # "16123988" and "16123988K" returned the same companyId and the same
    # bankruptcyRisk for every year).
    "brm_ValuBooster2",
    "industryRiskBankruptcy",
    "text_brc_Kirjain_luokitus_front-brm_ValuBooster2",
    "cr_credit_score_vb2",
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


def apply_level_suffix(override: str, model_code: str) -> str:
    """Keep a consolidated model's K suffix when the operator overrides the code.

    The override exists to correct a wrong company code, not to change WHICH
    model was fetched. Its value becomes meta.level (K = consolidated) and keys
    the credit-risk lookup, so a K-stripped override relabels a konserni report
    as emo and fetches parent-level credit risk — while the forecasts stay
    consolidated. The suffix therefore follows the fetched model, never the
    override. (This is how a saved-companies round trip broke konserni runs:
    upsert_company stores meta.y_tunnus, which has the K stripped, and feeds it
    straight back as an override on the next fetch.)
    """
    code = override.strip()
    if model_code.strip().upper().endswith("K") and not code.upper().endswith("K"):
        return code + "K"
    return code


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


def arr_sum(
    data_map: dict[str, dict[str, Any]],
    years: list[int],
    vars_: list[str],
    *,
    money: bool = False,
) -> list[Any]:
    """Sum several variables per year, for rows Valuatum splits in two.

    Capital loans and loans from credit institutions are each stored as a
    current + non-current pair; the report wants the combined figure. A year
    stays None only when EVERY component is missing, so a company that has
    only the current half still gets a value.
    """
    out = []
    for year in years:
        values = [as_num(raw(data_map, year, var)) for var in vars_]
        values = [v for v in values if isinstance(v, (int, float))]
        if not values:
            out.append(None)
            continue
        total = sum(values)
        out.append(roundish(total * 1000 if money else total))
    return out


def text_arr(data_map: dict[str, dict[str, Any]], years: list[int], var: str) -> list[Any]:
    """Per-year values that are text, not numbers (the credit rating letter).

    arr() runs everything through as_num, which turns "BBB" into None — most
    /modeldata variables are numeric, but the rating varname returns a string.
    """
    out = []
    for year in years:
        value = raw(data_map, year, var)
        out.append(str(value) if isinstance(value, str) and value.strip() else None)
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


def _creditrisk_rows(company_code: str, token: str) -> list[dict[str, Any]]:
    """GET /creditrisk?companyCode=… — one entry per company in the hierarchy.

    Payment defaults are a nice-to-have, so a failure here degrades to an empty
    list rather than failing the export: everything else in credit_risk already
    came back from /modeldata.
    """
    if not company_code or not token:
        return []
    url = f"{api_base_url()}/creditrisk?companyCode={urllib.parse.quote(company_code)}"
    req = urllib.request.Request(
        url,
        headers={"accept": "application/json", "authorization": f"Bearer {token}"},
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            rows = json.loads(resp.read().decode("utf-8"))
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, json.JSONDecodeError):
        return []
    return [r for r in rows if isinstance(r, dict)] if isinstance(rows, list) else []


def rest_payment_defaults(company_code: str, token: str) -> list[dict[str, Any]]:
    """Payment defaults (maksuhäiriöt) from GET /creditrisk.

    This is the ONLY thing taken from this endpoint, and the only Valuatum call
    still keyed by company code rather than fid. That is safe here specifically
    because payment defaults are a property of the company code itself: the emo
    and konserni entries carry identical ones, so which entry we read does not
    matter. Everything else in credit_risk comes from /modeldata by fid, where
    emo and konserni genuinely differ.

    So: first entry that actually has defaults wins. Do NOT filter by fid —
    when the company is not in the database the API still returns a minimal
    entry with the defaults and a null followedModelId (openapi.yaml:1229), and
    matching on fid would silently drop exactly those.
    """
    for row in _creditrisk_rows(company_code, token):
        defaults = row.get("paymentDefaults")
        if isinstance(defaults, list):
            cleaned = [d for d in defaults if isinstance(d, dict)]
            if cleaned:
                return cleaned
    return []


def credit_risk_payload(
    data_map: dict[str, dict[str, Any]],
    years: list[int],
    payment_defaults: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Credit risk for the actual years, straight from the model's own data.

    Both risk variables come back as fractions (0.00964645), so pct=True scales
    them to the percent the schema and the prompts expect. The series is capped
    at /modeldata's 9 actual years, where the Profinder MCP reached back 20 —
    the tradeoff for figures that belong to THIS fid instead of whichever
    company in the group the MCP decided to answer with.
    """
    company = arr(data_map, years, "brm_ValuBooster2", pct=True)
    industry = arr(data_map, years, "industryRiskBankruptcy", pct=True)
    rating = text_arr(data_map, years, "text_brc_Kirjain_luokitus_front-brm_ValuBooster2")
    score = arr(data_map, years, "cr_credit_score_vb2")
    available = any(v is not None for v in company + industry + rating + score)
    if not available:
        return {
            "available": False,
            "years": [],
            "company_bankruptcy_risk_pct": [],
            "industry_bankruptcy_risk_pct": [],
            "rating": [],
            "credit_score": [],
            "payment_defaults": payment_defaults or [],
        }
    return {
        "available": True,
        "years": list(years),
        "company_bankruptcy_risk_pct": company,
        "industry_bankruptcy_risk_pct": industry,
        "rating": rating,
        "credit_score": score,
        "payment_defaults": payment_defaults or [],
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


def income_statement(data_map: dict[str, dict[str, Any]], years: list[int]) -> dict[str, Any]:
    """Statement rows for any year range — actuals AND forecast.

    /modeldata returns these varNames for estimate years too (verified live on
    fid 356362, Y+0..Y+9), so one mapping serves both.
    """
    return {
        "net_sales": arr(data_map, years, "ns", money=True),
        "other_operating_income": arr(data_map, years, "cr_other_operating_income", money=True),
        "gross_profit": arr(data_map, years, ["cr_gross_profit", "gross_profit"], money=True),
        # cr_employee_expenses is the absolute figure (= wages and
        # salaries + social security expenses). Do NOT derive it from
        # per_employee x headcount: headcount_efficiency.py uses this
        # row to sanity-check headcount, which that derivation would
        # make circular.
        "personnel_costs": arr(data_map, years, "cr_employee_expenses", money=True),
        "other_operating_costs": arr(data_map, years, "cr_other_oper_expenses", money=True),
        "depreciation_total": arr(data_map, years, "cr_depreciation", money=True),
        "ebitda": arr(data_map, years, ["cr_ebitda_xml", "ebitda"], money=True),
        "ebit": arr(data_map, years, "ebit", money=True),
        "ebit_without_extras": arr(data_map, years, "ebit_without_extras", money=True),
        "extras_in_ebit": arr(data_map, years, "extras_in_ebit", money=True),
        "interest_expenses": arr(data_map, years, ["cr_interest_expenses", "interest_expenses"], money=True),
        "net_earnings": arr(data_map, years, ["cr_net_earnings", "net_earnings"], money=True),
    }


def balance_sheet(data_map: dict[str, dict[str, Any]], years: list[int]) -> dict[str, Any]:
    return {
        "development_costs": arr(data_map, years, "cr_development_expenditure", money=True),
        "intangibles_total": arr(data_map, years, ["cr_intangible_assets_total", "other_intangible_rights"], money=True),
        "tangible_assets": arr(data_map, years, ["cr_tangibles_assets_total", "tangible_ass"], money=True),
        "inventories": arr(data_map, years, "inventories", money=True),
        "trade_receivables": arr(data_map, years, "cr_curr_trade_debtors", money=True),
        "cash_and_equivalents": arr(data_map, years, "cr_cash_and_bank_deposits", money=True),
        "total_assets": arr(data_map, years, "bs_total_assets", money=True),
        "equity_excl_capital_loans": arr(data_map, years, "cr_shareholders_equity", money=True),
        "equity_incl_capital_loans": arr(data_map, years, "fundu_equity_incl_cap_loans", money=True),
        "capital_loans": arr_sum(
            data_map, years, ["cr_capital_loan_lt", "cr_capital_loan_st"], money=True),
        "loans_from_fin_institutions": arr_sum(
            data_map, years,
            ["cr_non_current_loans_from_credit_ins", "cr_current_loans_from_credit_ins"],
            money=True),
        "loans_from_associated": arr_sum(
            data_map, years,
            ["cr_non_current_owed_to_participating", "cr_current_owed_to_participating"],
            money=True),
        "advances_received": arr_sum(
            data_map, years,
            ["cr_non_current_advances_received", "cr_current_advances_received"],
            money=True),
        "trade_payables": arr(data_map, years, "cr_current_trade_creditors", money=True),
        # liab_ib_total EXCLUDES capital loans; interest_bearing_liabilities_calc
        # is the same series plus them. The valuation engine treats a capital
        # loan as equity-like (singlewriter.txt rule 25), so the debt line must
        # be the excluding one or the net-debt bridge double-counts it.
        "interest_bearing_debt": arr(data_map, years, "liab_ib_total", money=True),
        "non_interest_bearing_debt": arr(
            data_map, years, "non_interest_bearing_liabilities_calc", money=True),
    }


def build_payload(model: dict[str, Any], payment_defaults: list[dict[str, Any]] | None = None) -> dict[str, Any]:
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
        "income_statement": income_statement(data_map, forecast_years),
        "balance_sheet": balance_sheet(data_map, forecast_years),
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
            "income_statement": income_statement(data_map, actual_years),
            "balance_sheet": balance_sheet(data_map, actual_years),
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
                # Reverse remaining-PV series, terminal included — the EVA twin
                # of cumulative_discounted_fcff. `pv_of_eva_ty` comes back null
                # from /modeldata, so this row is the only source for the
                # terminal EVA: cum[0] - sum(discounted_eva).
                "cumulative_discounted_eva": arr(data_map, forecast_years, "cum_disc_eva", money=True),
                # The engine's own closing term, not a reconstruction:
                # -IB debt + cash + dividends + associates + minorities. The
                # `bridge` below carries only the first two, so rebuilding this
                # from it silently drops the last three — null on most SMEs,
                # not on a company with associates or minority interests.
                # value_of_equity_eva = prol_cap_invested + cum_disc_eva
                #                       + eva_additional (verified live, delta 0).
                "additional": scalar(data_map, first_forecast, "eva_additional", money=True),
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
        "credit_risk": credit_risk_payload(data_map, actual_years, payment_defaults),
        "peers": [],
        "client_reported_signals": [],
        "flags": build_flags(data_map, years),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--fetch-script", required=True, type=Path)
    parser.add_argument("--fid", required=True, type=int)
    parser.add_argument("--actuals", default=15, type=int)
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
            model["companyCode"] = apply_level_suffix(
                args.company_code_override, str(model.get("companyCode") or "")
            )
        if args.company_name_override:
            model["companyName"] = args.company_name_override
    payment_defaults = rest_payment_defaults(str(model.get("companyCode") or ""), token)
    payload = build_payload(model, payment_defaults)

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
