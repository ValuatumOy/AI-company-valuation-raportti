"""Normalize the reviewed SEC facts snapshot, preserving its statement coverage."""
import copy
from datetime import date


def selected_facts_input(snapshot):
    reporting = snapshot.get("reporting") or {}
    history = snapshot.get("historical") or {}
    if reporting.get("currency") != "USD" or reporting.get("prepared_unit") != "USD millions" \
            or history.get("currency") != "USD" or history.get("unit") != "USD millions":
        raise ValueError("Selected SEC facts must explicitly be USD millions")
    company = snapshot.get("company") or {}
    filing = snapshot.get("filing") or {}
    if not company.get("cik") or not company.get("name") or filing.get("form") != "10-K":
        raise ValueError("SEC issuer and annual filing provenance are required")
    years = list(history.get("years") or [])
    if not years:
        raise ValueError("SEC annual years are required")

    def statement(name):
        result = {}
        for key, values in (history.get(name) or {}).items():
            if not isinstance(values, list) or len(values) != len(years) or any(
                    value is not None and (not isinstance(value, (int, float))
                                           or isinstance(value, bool)) for value in values):
                raise ValueError(f"Invalid SEC annual series: {name}.{key}")
            result[key] = [value * 1000 if value is not None else None for value in values]
        return result

    income, balance, cashflow = (statement(name) for name in
                                ("income_statement", "balance_sheet", "cash_flow"))
    return {
        "unit": "tUSD",
        "meta": {"company_name": company["name"], "y_tunnus": company.get("isin") or company["cik"],
                 "isin": company.get("isin"), "cik": company["cik"], "ticker": company.get("ticker"),
                 "industry": None, "report_date": date.today().isoformat(), "level": "consolidated",
                 "report_language": "en", "country_code": "US", "currency": "USD", "unit": "tUSD",
                 "data_source": "SEC 10-K annual facts", "source_url": snapshot.get("filing_url"),
                 "latest_filing_date": filing.get("filed")},
        "actuals": {"years": years, "unit": "tUSD",
                    "income_statement": {"net_sales": income.get("net_sales"),
                                         "ebit": income.get("operating_income"),
                                         "net_earnings": income.get("net_income")},
                    "balance_sheet": {"cash_and_equivalents": balance.get("cash_and_equivalents"),
                                      "total_assets": balance.get("total_assets"),
                                      "equity_excl_capital_loans": balance.get("stockholders_equity"),
                                      "long_term_debt_current": balance.get("long_term_debt_current"),
                                      "long_term_debt_noncurrent": balance.get("long_term_debt_noncurrent")},
                    "cash_flow": cashflow,
                    "source_statement_items": {"income_statement": income, "balance_sheet": balance}},
        "forecast": {"years": [], "unit": "tUSD"},
        "valuation_engine": {"unit": "tUSD"}, "key_ratios": {}, "credit_risk": {},
        "peers": [], "client_reported_signals": [], "flags": [],
        "source_provenance": copy.deepcopy({
            "filing": filing, "filing_url": snapshot.get("filing_url"),
            "fact_provenance": snapshot.get("fact_provenance"),
            "statement_coverage": snapshot.get("statement_coverage"),
            "conversion": "USD millions multiplied by 1000 to tUSD; no FX conversion",
            "limitations": "Selected historical facts only. No forecast, WACC, DCF, EBITDA "
                           "or aggregate interest-bearing debt has been inferred."}),
    }
