from copy import deepcopy
import asyncio

import pytest

from app import assemble, dcf_quality, runner, store, valuatum


def _input():
    return {"forecast": {"years": [2026, 2027, 2028], "income_statement": {
        "depreciation_total": [-400, -600, -800]}},
        "valuation_engine": {"dcf": {"years": [2026, 2027, 2028],
                                      "depreciation_total": [None, 0, 800]}}}


def test_missing_addbacks_require_model_review_without_changing_values():
    data = _input()
    original = deepcopy(data)
    errors = dcf_quality.blockers(data)
    assert len(errors) == 1 and "2026, 2027" in errors[0] and "2028" not in errors[0]
    with pytest.raises(ValueError, match="kassavirta vaatii tarkistuksen"):
        dcf_quality.require_review(data)
    assert data == original


def test_years_are_matched_by_value_not_array_position():
    data = _input()
    data["valuation_engine"]["dcf"] = {"years": [2028, 2026, 2027],
                                         "depreciation_total": [800, 400, None]}
    assert "2027" in dcf_quality.blockers(data)[0]
    assert "2026" not in dcf_quality.blockers(data)[0]


def test_no_claim_when_depreciation_is_unavailable_or_immaterial():
    data = _input()
    data["forecast"]["income_statement"]["depreciation_total"] = [None, 0, -1]
    data["valuation_engine"]["dcf"]["depreciation_total"] = [None, 0, 0]
    assert dcf_quality.blockers(data) == []


@pytest.mark.parametrize("addback", [float("nan"), "unknown", True])
def test_invalid_addback_is_not_treated_as_verified(addback):
    data = _input()
    data["valuation_engine"]["dcf"]["depreciation_total"][2] = addback
    assert "2028" in dcf_quality.blockers(data)[0]


def test_checkout_preflight_detects_a_known_missing_addback(monkeypatch):
    model = {"currentYear": 2026, "dataMap": {"2025": {
        "cr_fiscal_year_start": 20250101, "cr_fiscal_year_end": 20251231,
        "ns": 1, "ebit": 0.1, "bs_total_assets": 2,
        "liab_ib_total": 1, "interest_bearing_liabilities_calc": 1},
        "2026": {"cr_depreciation": -0.4}}}
    async def source(*args):
        return {"42": model}
    monkeypatch.setattr(valuatum, "modeldata", source)
    with pytest.raises(ValueError, match="poistojen ja investointien käsittely"):
        asyncio.run(valuatum.preflight_fiscal_periods(42))


def test_release_readiness_blocks_unresolved_new_version_depreciation(monkeypatch):
    data = _input()
    data["meta"] = {"scenario_timing_version": 2}
    run = {"status": "ok", "results": [{"order": 0, "status": "ok", "parsed_json": data}]}
    monkeypatch.setattr(store, "get_run", lambda _: run)
    monkeypatch.setattr(assemble, "assemble", lambda _: {
        "sections": [{"id": sid} for sid in runner.SECTION_ORDER]})
    result = store.report_readiness("local")
    assert not result["ready"]
    assert any("poistojen ja investointien käsittely" in issue for issue in result["issues"])


@pytest.mark.parametrize("addback", [5, -400, 200])
def test_nonzero_but_inconsistent_or_negative_addback_needs_review(addback):
    data = _input()
    data["valuation_engine"]["dcf"]["depreciation_total"] = [addback, 600, 800]
    assert "2026" in dcf_quality.blockers(data)[0]


def test_a_missing_year_and_duplicate_year_are_not_silently_skipped():
    data = _input()
    data["valuation_engine"]["dcf"]["years"] = [2026, 2028]
    assert "2027" in dcf_quality.blockers(data)[0]
    data["valuation_engine"]["dcf"]["years"] = [2026, 2027, 2027]
    assert "toistuvat" in dcf_quality.blockers(data)[0]


def test_cash_flow_with_unknown_forecast_depreciation_needs_review():
    data = _input()
    data["forecast"]["income_statement"]["depreciation_total"] = [400, None, 800]
    data["valuation_engine"]["dcf"]["depreciation_total"] = [400, 600, 800]
    data["valuation_engine"]["dcf"]["fcff"] = [1, 2, 3]
    assert "2027" in dcf_quality.blockers(data)[0]


def test_nonzero_addback_with_zero_source_depreciation_requires_review():
    data = _input()
    data["forecast"]["income_statement"]["depreciation_total"][2] = 0
    assert "2028" in dcf_quality.blockers(data)[0]


def test_cashflow_year_without_forecast_statement_cannot_be_silently_skipped():
    data = _input()
    data["forecast"]["years"] = []
    data["forecast"]["income_statement"]["depreciation_total"] = []
    data["valuation_engine"]["dcf"]["fcff"] = [100, 200, 300]
    assert dcf_quality.blockers(data)


def test_netted_investments_reconcile_without_adding_depreciation_twice():
    data = _input()
    dcf = data["valuation_engine"]["dcf"]
    dcf.update({"depreciation_total": [None, None, None],
                "gross_capex": [-1000, -1200, -1400],
                "investing_cash_flow": [-1400, -1800, -2200],
                "operating_cash_flow": [300, 400, 500],
                "ebit": [300, 400, 500], "taxes_paid": [0, 0, 0],
                "change_in_working_capital": [0, 0, 0],
                "fcff": [-700, -800, -900]})
    assert dcf_quality.blockers(data) == []
    assert dcf_quality.netted_years(data) == {2026, 2027, 2028}
    directive = runner._valuation_integrity_directive(data)
    assert "Do not call the DCF line gross capex" in directive
    dcf["investing_cash_flow"][1] = -1600
    assert "2027" in dcf_quality.blockers(data)[0]
    dcf["investing_cash_flow"][1] = -1800
    dcf["fcff"][2] = -850
    assert "2028" in dcf_quality.blockers(data)[0]


def test_netted_dcf_table_names_the_source_investment_line_accurately():
    from app import dcf_detail
    data = _input()
    dcf = data["valuation_engine"]["dcf"]
    dcf.update({"depreciation_total": [None] * 3,
                "gross_capex": [-1000, -1200, -1400],
                "investing_cash_flow": [-1400, -1800, -2200],
                "operating_cash_flow": [300, 400, 500],
                "ebit": [300, 400, 500], "taxes_paid": [0, 0, 0],
                "change_in_working_capital": [0, 0, 0],
                "fcff": [-700, -800, -900],
                "discounted_fcff": [-650, -700, -750],
                "cumulative_discounted_fcff": [100]})
    blocks = dcf_detail.build_dcf_detail_blocks(data)
    table = next(b for b in blocks if b.get("table_id") == "deterministic_dcf_fcff_drivers")
    assert any(row[0] == "- Nettoinvestoinnit (lähdemalli)" for row in table["rows"])
    assert any(b.get("title") == "Nettoinvestoinnit ja poistot" for b in blocks)


def test_netted_investment_label_survives_a_zero_depreciation_year():
    from app import dcf_detail
    data = _input()
    data["forecast"]["income_statement"]["depreciation_total"][1] = 0
    dcf = data["valuation_engine"]["dcf"]
    dcf.update({"depreciation_total": [None] * 3,
                "gross_capex": [-1000, -1200, -1400],
                "investing_cash_flow": [-1400, -1200, -2200],
                "operating_cash_flow": [300, 400, 500],
                "ebit": [300, 400, 500], "taxes_paid": [0, 0, 0],
                "change_in_working_capital": [0, 0, 0],
                "fcff": [-700, -800, -900],
                "discounted_fcff": [-650, -700, -750],
                "cumulative_discounted_fcff": [100]})
    assert dcf_quality.netted_years(data) == {2026, 2027, 2028}
    blocks = dcf_detail.build_dcf_detail_blocks(data)
    table = next(b for b in blocks if b.get("table_id") == "deterministic_dcf_fcff_drivers")
    assert any(row[0] == "- Nettoinvestoinnit (lähdemalli)" for row in table["rows"])


def test_mixed_investment_methods_use_a_neutral_table_label():
    from app import dcf_detail
    data = _input()
    dcf = data["valuation_engine"]["dcf"]
    dcf.update({"depreciation_total": [400, None, None],
                "gross_capex": [-1400, -1200, -1400],
                "investing_cash_flow": [-1400, -1800, -2200],
                "operating_cash_flow": [700, 400, 500],
                "ebit": [300, 400, 500], "taxes_paid": [0, 0, 0],
                "change_in_working_capital": [0, 0, 0],
                "fcff": [-700, -800, -900], "discounted_fcff": [-650, -700, -750],
                "cumulative_discounted_fcff": [100]})
    assert dcf_quality.blockers(data) == []
    blocks = dcf_detail.build_dcf_detail_blocks(data)
    table = next(b for b in blocks if b.get("table_id") == "deterministic_dcf_fcff_drivers")
    assert any(row[0] == "- Investointierä (lähdemalli)" for row in table["rows"])
    callout = next(b for b in blocks if b.get("title") == "Nettoinvestoinnit ja poistot")
    assert "2027, 2028" in callout["text"] and "2026" not in callout["text"]


def test_conflicting_free_operating_cashflow_is_blocked():
    data = _input()
    dcf = data["valuation_engine"]["dcf"]
    dcf.update({"depreciation_total": [400, 600, 800],
                "ebit": [1000, 1000, 1000], "taxes_paid": [0, 0, 0],
                "change_in_working_capital": [0, 0, 0],
                "investing_cash_flow": [-500, -600, -700],
                "fcff": [900, 1000, 1100],
                "free_operating_cash_flow": [99999, 1000, 1100]})
    assert "2026" in dcf_quality.blockers(data)[0]
    dcf["free_operating_cash_flow"][0] = 900
    assert dcf_quality.blockers(data) == []


def test_large_absolute_amounts_do_not_hide_missing_depreciation():
    data = _input()
    dcf = data["valuation_engine"]["dcf"]
    dcf.update({"depreciation_total": [None] * 3,
                "gross_capex": [-1000000, -1200, -1400],
                "investing_cash_flow": [-1000000, -1800, -2200],
                "operating_cash_flow": [300, 400, 500], "ebit": [300, 400, 500],
                "taxes_paid": [0, 0, 0], "change_in_working_capital": [0, 0, 0],
                "fcff": [-999700, -800, -900]})
    assert "2026" not in dcf_quality.netted_years(data)
    assert "2026" in dcf_quality.blockers(data)[0]


def test_depreciation_already_in_operating_cashflow_is_not_counted_twice():
    data = _input()
    dcf = data["valuation_engine"]["dcf"]
    dcf.update({"depreciation_total": [None] * 3,
                "gross_capex": [-1000, -1200, -1400],
                "investing_cash_flow": [-1400, -1800, -2200],
                "operating_cash_flow": [700, 400, 500], "ebit": [300, 400, 500],
                "taxes_paid": [0, 0, 0], "change_in_working_capital": [0, 0, 0],
                "fcff": [-300, -800, -900]})
    assert "2026" not in dcf_quality.netted_years(data)
    assert "2026" in dcf_quality.blockers(data)[0]


def test_explicit_depreciation_addback_must_reach_free_cash_flow():
    data = _input()
    dcf = data["valuation_engine"]["dcf"]
    dcf.update({"depreciation_total": [400, 600, 800],
                "ebit": [1000, 1000, 1000], "taxes_paid": [0, 0, 0],
                "change_in_working_capital": [0, 0, 0],
                "investing_cash_flow": [-500, -600, -700],
                "fcff": [500, 1000, 1100]})
    assert "2026" in dcf_quality.blockers(data)[0]
    dcf["fcff"][0] = 900
    assert dcf_quality.blockers(data) == []


def test_explicit_method_rejects_conflicting_displayed_intermediate_rows():
    data = _input()
    dcf = data["valuation_engine"]["dcf"]
    dcf.update({"depreciation_total": [400, 600, 800],
                "ebit": [1000, 1000, 1000], "taxes_paid": [0, 0, 0],
                "change_in_working_capital": [0, 0, 0],
                "operating_cash_flow": [1200, 1600, 1800],
                "gross_capex": [-1000, -600, -700],
                "investing_cash_flow": [-500, -600, -700],
                "fcff": [900, 1000, 1100]})
    assert "2026" in dcf_quality.blockers(data)[0]
    dcf["operating_cash_flow"][0] = 1400
    assert "2026" in dcf_quality.blockers(data)[0]
    dcf["gross_capex"][0] = -500
    assert dcf_quality.blockers(data) == []
