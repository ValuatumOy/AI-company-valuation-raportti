from copy import deepcopy

import pytest

from app import financials


def _forecast(income, years=None):
    return {"forecast": {"years": years or [2026], "income_statement": income}}


def _gross_row(blocks):
    table = next(b for b in blocks if b.get("table_id") ==
                 "deterministic_income_statement_forecast")
    return next((r for r in table["rows"] if r[0] == "Myyntikate"), None)


def _notices(blocks):
    return [b for b in blocks if b.get("table_id") ==
            "deterministic_forecast_gross_profit_quality"]


def test_inconsistent_forecast_cells_are_omitted_without_changing_history_or_valuation():
    income = {
        "gross_profit": [10, 25, 10000],
        "personnel_costs": [-700, -1600, -1500],
        "other_operating_costs": [-600, -1500, -1500],
        "other_operating_income": [0, 0, 0],
        "ebitda": [500, 5900, 7000],
    }
    data = _forecast(income, [2026, 2031, 2032])
    data["actuals"] = {"years": [2025], "income_statement": {
        key: [values[0]] for key, values in income.items()}}
    data["valuation_engine"] = {"dcf": {
        "bridge": {"interest_bearing_debt": -2200, "cash": 240},
        "equity_value_before_floor": 6000,
    }}
    before = deepcopy(data)

    sections = financials.build_financial_statement_blocks(data)

    assert _gross_row(sections["6"]) == ["Myyntikate", "", "", "10 000"]
    actual_gross = next(r for r in sections["5"][0]["rows"] if r[0] == "Myyntikate")
    assert actual_gross == ["Myyntikate", "10"]
    notice = _notices(sections["6"])[0]["text"]
    assert "2026e, 2031e" in notice
    assert "2032e" not in notice
    assert "Korvaavia lukuja ei ole päätelty" in notice
    assert data == before
    assert income["gross_profit"] == before["forecast"]["income_statement"]["gross_profit"]


@pytest.mark.parametrize("personnel,costs,other_income,ebitda", [
    (-200, -300, 0, 500),
    (200, 300, 0, 500),
    (-200, -300, 100, 600),  # Other income is outside gross profit.
    (-200, -300, 100, 500),  # Other income is already in gross profit.
    (200, 300, 100, 600),
    (-200, 30, 0, 830),  # A signed reversal in a cost row.
    (-200, -300, 0, 501.4),  # Rounded source rows need not foot exactly.
    (-200, -300, 100, 598.5),
    (-200, -300, 0, 460),  # A modest discrepancy is not this display guard's target.
])
def test_valid_or_modest_discrepancies_are_retained(personnel, costs, other_income, ebitda):
    data = _forecast({"gross_profit": [1000], "personnel_costs": [personnel],
                      "other_operating_costs": [costs],
                      "other_operating_income": [other_income], "ebitda": [ebitda]})
    blocks = financials.build_financial_statement_blocks(data)["6"]
    assert _gross_row(blocks) == ["Myyntikate", "1 000"]
    assert not _notices(blocks)


@pytest.mark.parametrize("key", ["personnel_costs", "other_operating_costs",
                                 "other_operating_income", "ebitda"])
@pytest.mark.parametrize("missing", [None, [], [None], [False]])
def test_incomplete_components_do_not_claim_validation_or_hide_source(key, missing):
    income = {"gross_profit": [7], "personnel_costs": [-600],
              "other_operating_costs": [-400], "other_operating_income": [4],
              "ebitda": [500]}
    if missing is None:
        del income[key]
    else:
        income[key] = missing
    blocks = financials.build_financial_statement_blocks(_forecast(income))["6"]
    assert _gross_row(blocks) == ["Myyntikate", "7"]
    assert not _notices(blocks)


@pytest.mark.parametrize("sign", [-1, 1])
def test_large_discrepancy_is_hidden_for_both_expense_sign_conventions(sign):
    income = {"gross_profit": [7], "personnel_costs": [sign * 600],
              "other_operating_costs": [sign * 400], "other_operating_income": [4],
              "ebitda": [500]}
    blocks = financials.build_financial_statement_blocks(_forecast(income))["6"]
    assert _gross_row(blocks) is None
    assert len(_notices(blocks)) == 1


def test_tiny_source_rounding_discrepancy_is_retained():
    income = {"gross_profit": [10], "personnel_costs": [-3],
              "other_operating_costs": [-2], "other_operating_income": [0],
              "ebitda": [6]}
    blocks = financials.build_financial_statement_blocks(_forecast(income))["6"]
    assert _gross_row(blocks) == ["Myyntikate", "10"]
    assert not _notices(blocks)


def test_notice_uses_english_for_an_english_report():
    data = _forecast({"gross_profit": [7], "personnel_costs": [-600],
                      "other_operating_costs": [-400], "other_operating_income": [4],
                      "ebitda": [500]})
    data["meta"] = {"report_language": "en", "country_code": "US", "currency": "USD"}
    notice = _notices(financials.build_financial_statement_blocks(data)["6"])[0]
    assert notice["title"] == "Forecast gross profit"
    assert "No replacement figures are inferred" in notice["text"]
