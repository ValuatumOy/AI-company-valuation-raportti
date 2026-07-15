#!/usr/bin/env python3
"""Fetch Valuatum modeldata for the arvonmaaritys valuation tables and print
each one as a Markdown table (one row per variable, one column per fiscal
year). The tables and their variable order are the CVSVTABLE/CVSVTABLEROW
definitions from the Valuatum DB (see TABLES below).

Standalone — depends only on the Python standard library.

Fetch mechanics mirror the report engine (see fetchRestClient.ts):
  - Positions are requested by relative position: `Y-1` is the newest
    ACTUAL year, `Y+0` is the FIRST ESTIMATE year (a forecast). A bare `Y`
    is rejected by the API.
  - In the response, `dataMap` is keyed by absolute year; any year
    >= `currentYear` is an estimate (rendered with a trailing "e").

Rendering:
  - `NULL` (spacer) source rows are dropped, and any variable the API
    returns no value for in any year is dropped too — only rows with data
    are printed.
  - Values are printed RAW, exactly as the /modeldata API returns them
    (absolute figures come back in millions; ratios/percent come back as
    fractions). These tables' variables are mostly NOT in VAR_CATALOG, so
    there is no reliable abs/pct type to scale by. Add a varName to PCT_VARS
    to render it as "xx.x %", or fill LABELS to relabel a row.

Usage:
    python3 fetch_modeldata.py --fid 12345 --actuals 5 --estimates 3

The API token is read from the VALUATUM_TOKEN env var or --token.
"""

import argparse
import json
import os
import sys
import urllib.error
import urllib.request

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

DEFAULT_MODELDATA_URL = "https://profinder.valuatum.com/rest/modeldata"

# Prefer the VALUATUM_TOKEN env var or --token at runtime.
TOKEN = ""


def modeldata_url():
    """Use the same ValuBuild environment as estimate generation.

    Production remains the default when estimate generation is disabled. This
    prevents local/test runs from generating estimates in profindertest and
    then accidentally reading the same FID's modeldata from production.
    """
    estimate_base = os.environ.get("VALU_ESTIMATE_GENERATION_URL", "").strip()
    if estimate_base:
        return estimate_base.rstrip("/") + "/modeldata"
    return DEFAULT_MODELDATA_URL


# ---------------------------------------------------------------------------
# Table definitions — (CVSVTABLEID, display title, [rows]).
# Each row is a CVSVTABLEROW SOURCE varname. Value-less rows are dropped at
# render time.
# ---------------------------------------------------------------------------

TABLES = [
    (2715, "Base figures", [          # base_figures_with_currency
        "ns",
        "cr_ebitda_xml",
        "ebit",
        "tangible_ass",
        "cr_shareholders_equity",
        "liab_ib_total",
        "bs_total_assets",
        "gross_cap_expenditure",
    ]),
    (17, "Key ratios", [              # key_ratios
        "ns_growth",
        "ebitda_percent",
        "ebit_percent",
        "asset_turnover",
        "roi_before_tax_avg_cap",
        "roe_percent",
        "equity_ratio",
    ]),
    (18, "DCF valuation", [           # dcf_valuation_with_currency
        "ebit",
        "dep_total_nega",
        "taxes_paid",
        "tax_fin_expenses",
        "tax_fin_income",
        "change_in_wc_nega",
        "operating_cash_flow",
        "change_in_lt_liab_nib",
        "gross_cap_expenditure_nega",
        "free_operating_cash_flow",
        "other_items_fcf",
        "free_cash_flow_to_firm",
        "disc_fcff",
        "cum_disc_fcff",
        "ib_debt_nega_prev_year",
        "cash_prev_year",
        "dcf_dividends",
        "value_of_equity_fcff",
        "no_of_shares_total",
        "fair_value_fcff",
        "market_value_of_associated",
        "market_value_of_minorities_nega",
    ]),
    (19, "EVA valuation", [           # eva_valuation_with_currency
        "ebit",
        "taxes_eva_ebit",
        "noplat",
        "eva_other",
        "cost_of_cap_abs",
        "eva",
        "pv_of_eva_ty",
        "disc_eva",
        "pv_of_cap_base_change",
        "cum_disc_eva",
        "prol_cap_invested",
        "ib_debt_nega_prev_year",
        "cash_prev_year",
        "dcf_dividends",
        "value_of_equity_eva",
        "no_of_shares_total",
        "fair_value_eva",
        "market_value_of_associated",
        "market_value_of_minorities_nega",
    ]),
    (20, "Cost of capital (WACC)", [  # cost_of_capital_wacc
        "tax_rate_wacc",
        "target_dde",
        "cost_of_debt",
        "equity_beta",
        "market_risk_premium",
        "riskfree_interest_rate",
        "cost_of_equity",
        "wacc",
        "liquidity_premium",
    ]),
    (4936, "Estimate parameters", [   # estimate_parameters
        "default_10_percent",
        "ns_growth",
        "ebit_percent",
        "dep_without_gwa_percent",
        "acquisitions",
        "inv_gross_per_ns",
        "inv_gross",
        "tangible_ass_per_ns",
        "apum_fa_hall",
        "cr_raw_materials_pct",
        "cr_semifinished_products_pct",
        "cr_finished_goods_pct",
        "cr_curr_trade_debtors_pct",
        "cr_non_curr_trade_debtors_pct",
        "cr_current_trade_creditors_pct",
        "cr_non_current_trade_creditors_pct",
        "bs_allocated_share_lt",
        "lt_liab_ib_minlevel",
        "st_debt_minlevel",
        "cr_payout_ratio",
        "tax_rate",
    ]),
]

# Row labels (I18N DEFAULTSTRING, group 5). Any varname not listed falls back
# to the varname itself (e.g. market_value_of_associated, lt_liab_ib_minlevel,
# default_10_percent — no I18N entry was provided for these).
LABELS = {
    # Base figures
    "ns": "Net sales",
    "cr_ebitda_xml": "EBITDA",
    "ebit": "EBIT",
    "tangible_ass": "Tangible assets",
    "cr_shareholders_equity": "Shareholders' equity (excl. Cap loans)",
    "liab_ib_total": "Interest-bearing liabilities",
    "bs_total_assets": "Balance sheet total (assets)",
    "gross_cap_expenditure": "Gross capex",
    # Key ratios
    "ns_growth": "Net sales growth",
    "ebitda_percent": "EBITDA %",
    "ebit_percent": "EBIT %",
    "asset_turnover": "Asset turnover",
    "roi_before_tax_avg_cap": "ROI % (teoll.)",
    "roe_percent": "ROE %",
    "equity_ratio": "Equity ratio",
    # DCF valuation
    "dep_total_nega": "Depreciation, amortisation & write-downs",
    "taxes_paid": "- Paid taxes",
    "tax_fin_expenses": "Taxes on financial expenses",
    "tax_fin_income": "Taxes on financial income",
    "change_in_wc_nega": "Change in working capital",
    "operating_cash_flow": "Operating cash flow",
    "change_in_lt_liab_nib": "Change in other long-term liabs",
    "gross_cap_expenditure_nega": "Cash flow from investing activities",
    "free_operating_cash_flow": "Free oper. cash flow",
    "other_items_fcf": "Other items (FCF)",
    "free_cash_flow_to_firm": "Free cash flow",
    "disc_fcff": "Discounted FCFF",
    "cum_disc_fcff": "Cum. disc. FCFF",
    "ib_debt_nega_prev_year": "Interest bearing debt (beg. of year)",
    "cash_prev_year": "Cash at bank (beg. of year)",
    "dcf_dividends": "Cash dividend",
    "value_of_equity_fcff": "Value of equity",
    "no_of_shares_total": "No of shares total",
    "fair_value_fcff": "Fair value DCF",
    # EVA valuation
    "taxes_eva_ebit": "Taxes on EBIT",
    "noplat": "NOPLAT",
    "eva_other": "Other items (EVA)",
    "cost_of_cap_abs": "Cost of capital (abs.)",
    "eva": "EVA",
    "pv_of_eva_ty": "PV of TRM EVA",
    "disc_eva": "Discounted EVA",
    "pv_of_cap_base_change": "PV of cap. base change",
    "cum_disc_eva": "Cum. disc. EVA",
    "prol_cap_invested": "Prolonged capital invested",
    "value_of_equity_eva": "Value of equity, EVA",
    "fair_value_eva": "Fair value EVA",
    # Cost of capital (WACC)
    "tax_rate_wacc": "Tax rate (WACC) %",
    "target_dde": "Target D/(D+E)",
    "cost_of_debt": "Cost of debt %",
    "equity_beta": "Equity beta",
    "market_risk_premium": "Equity market risk premium (%-points)",
    "riskfree_interest_rate": "Risk-free interest rate",
    "cost_of_equity": "Cost of equity",
    "wacc": "WACC %",
    "liquidity_premium": "Liquidity premium",
    # Estimate parameters
    "dep_without_gwa_percent": "Depreciation % (fa ord, excl. GWA)",
    "acquisitions": "Acquisitions",
    "inv_gross_per_ns": "Investments/Net sales %",
    "inv_gross": "Investments",
    "tangible_ass_per_ns": "Tangible assets/Net sales %",
    "apum_fa_hall": "Is Tangible assets % dominating?",
    "cr_raw_materials_pct": "Raw materials (% of net sales)",
    "cr_semifinished_products_pct": "Semifinished products (% of net sales)",
    "cr_finished_goods_pct": "Finished goods (% of net sales)",
    "cr_curr_trade_debtors_pct": "Trade Receivables (% of net sales)",
    "cr_non_curr_trade_debtors_pct": "Non-current trade debtors (% of net sales)",
    "cr_current_trade_creditors_pct": "Current trade creditors (% of net sales)",
    "cr_non_current_trade_creditors_pct": "Non-current trade creditors (% of net sales)",
    "bs_allocated_share_lt": "Share of gener. debt allocated to long-term debt",
    "st_debt_minlevel": "Minimum level of short-term debt",
    "cr_payout_ratio": "Payout ratio",
    "tax_rate": "Tax rate % (actual)",
}
PCT_VARS = set()           # varNames whose fraction value should print as "xx.x %"


# ---------------------------------------------------------------------------
# Relative positions
# ---------------------------------------------------------------------------

def distinct_vars():
    """Every non-spacer varname across all tables, first-seen order."""
    seen = []
    for _id, _title, rows in TABLES:
        for r in rows:
            if r is not None and r not in seen:
                seen.append(r)
    return seen


def build_var_poses(actuals, estimates):
    """One {varName, relPos} tuple per variable x requested position.

    Actuals:   Y-1 (newest actual) .. Y-<actuals>
    Estimates: Y+0 (first estimate) .. Y+<estimates-1>

    The modeldata API only accepts SINGLE-DIGIT relative positions
    (pattern Y[+-][0-9]); Y-10+/Y+10+ return HTTP 400. So cap the requested
    positions at 9 here. Deeper history (10-15+ years) is not fetched from
    modeldata — it comes from the separate Profinder backfill, which is driven
    by the full `actuals` value via its own --limit.
    """
    rel_poses = [f"Y-{i}" for i in range(1, min(actuals, 9) + 1)]
    rel_poses += [f"Y+{i}" for i in range(0, min(estimates, 10))]
    return [
        {"varName": name, "relPos": rel}
        for name in distinct_vars()
        for rel in rel_poses
    ]


# ---------------------------------------------------------------------------
# HTTP
# ---------------------------------------------------------------------------

def fetch_modeldata(fid, actuals, estimates, token):
    body = {
        "fids": [fid],
        "varPoses": build_var_poses(actuals, estimates),
        "includeHistoryData": True,
        "includeEstimates": True,
    }
    req = urllib.request.Request(
        modeldata_url(),
        data=json.dumps(body).encode("utf-8"),
        method="POST",
        headers={
            "accept": "application/json",
            "content-type": "application/json",
            "authorization": f"Bearer {token}",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", "replace")
        sys.exit(f"HTTP {e.code} from /modeldata: {detail}")
    except urllib.error.URLError as e:
        sys.exit(f"Request to /modeldata failed: {e.reason}")

    # Response is { "<fid>": RestModelData }.
    model = payload.get(str(fid))
    if model is None:
        sys.exit(f"No modeldata returned for fid {fid}. Keys: {list(payload)}")
    return model


# ---------------------------------------------------------------------------
# Formatting
# ---------------------------------------------------------------------------

def fmt_value(var, val):
    if val is None:
        return "-"
    if var in PCT_VARS and isinstance(val, (int, float)) and not isinstance(val, bool):
        return f"{val * 100:.1f} %"
    if isinstance(val, bool):
        return str(val)
    if isinstance(val, int):
        return f"{val:,}".replace(",", " ")
    if isinstance(val, float):
        if val.is_integer():
            return f"{int(val):,}".replace(",", " ")
        return f"{round(val, 4):g}"
    return str(val)


def year_columns(data_map, current_year):
    """Sorted year keys; estimate years (>= currentYear) get a trailing 'e'."""
    years = sorted(data_map.keys(), key=lambda y: int(y))
    headers = [f"{y}e" if int(y) >= current_year else y for y in years]
    return years, headers


def render_table(table, years, headers, data_map):
    """Markdown table for one CVSVTABLE, or None if no row has any value."""
    _id, title, rows = table
    body = []
    for r in rows:
        if r is None:
            continue  # drop NULL spacer rows
        vals = [data_map.get(y, {}).get(r) for y in years]
        if all(v is None for v in vals):
            continue  # drop rows the API has no value for
        label = LABELS.get(r, r)
        cells = [fmt_value(r, v) for v in vals]
        body.append(f"| {label} | {' | '.join(cells)} |")
    if not body:
        return None
    head = [f"## {title}", "",
            f"| Item | {' | '.join(headers)} |",
            "|" + "---|" * (len(headers) + 1)]
    return "\n".join(head + body)


def render_all(model):
    data_map = model.get("dataMap", {})
    current_year = model.get("currentYear")
    years, headers = year_columns(data_map, current_year)
    if not years:
        return "_No modeldata rows returned._"
    blocks = [render_table(t, years, headers, data_map) for t in TABLES]
    blocks = [b for b in blocks if b is not None]
    return "\n\n".join(blocks) if blocks else "_No modeldata rows returned._"


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--fid", type=int, required=True, help="Followed model id")
    ap.add_argument("--actuals", type=int, default=15,
                    help="Number of actualized years (Y-1..Y-N). Default 15.")
    ap.add_argument("--estimates", type=int, default=3,
                    help="Number of estimate years (Y+0..Y+M-1). Default 3.")
    ap.add_argument("--token", default=None,
                    help="API token (else VALUATUM_TOKEN env).")
    args = ap.parse_args()

    token = args.token or os.environ.get("VALUATUM_TOKEN") or TOKEN
    if not token:
        sys.exit("No API token. Pass --token or set VALUATUM_TOKEN.")

    model = fetch_modeldata(args.fid, args.actuals, args.estimates, token)

    name = model.get("companyName", "?")
    code = model.get("companyCode", "?")
    ccy = model.get("currency", "EUR")
    print(f"# {name} ({code}) — fid {args.fid}")
    print()
    print(f"Currency: {ccy}. Values are returned by the REST API as-is "
          f"(absolute figures in millions). Estimate years are marked with 'e'.")
    print()
    print(render_all(model))


if __name__ == "__main__":
    main()
