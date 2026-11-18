import asyncio
from copy import deepcopy

import pytest

from app import legacy_investing, valuatum


def _saved():
    return {"meta": {"data_source": "profinder", "country_code": "FI"},
            "forecast": {"years": [2026, 2027]},
            "valuation_engine": {"dcf": {"years": [2026, 2027],
                "fcff": [-700, -800], "gross_capex": [-1000, -1200],
                "operating_cash_flow": [300, 400]}}}


def _model():
    return {"currentYear": 2026, "dataMap": {
        "2026": {"free_cash_flow_to_firm": -0.7, "gross_cap_expenditure_nega": -1,
                 "operating_cash_flow": 0.3, "cr_cf_gross_capex_nega": -1.4},
        "2027": {"free_cash_flow_to_firm": -0.8, "gross_cap_expenditure_nega": -1.2,
                 "operating_cash_flow": 0.4, "cr_cf_gross_capex_nega": -1.8}}}


def _source(monkeypatch, model):
    calls = []
    async def fetch(fids, poses):
        calls.append((fids, poses))
        return {"42": model}
    monkeypatch.setattr(valuatum, "modeldata", fetch)
    return calls


def test_hydrates_only_after_each_saved_forecast_row_matches_source(monkeypatch):
    data = _saved()
    original = deepcopy(data)
    calls = _source(monkeypatch, _model())
    result = asyncio.run(legacy_investing.add_source_investing_cashflow(data, "42"))
    assert result is data
    assert result["valuation_engine"]["dcf"]["investing_cash_flow"] == [-1400, -1800]
    del result["valuation_engine"]["dcf"]["investing_cash_flow"]
    assert result == original
    assert calls[0][0] == [42]
    assert {p["relPos"] for p in calls[0][1]} == {f"Y+{i}" for i in range(10)}
    assert {p["varName"] for p in calls[0][1]} == {
        "cr_cf_gross_capex_nega", "free_cash_flow_to_firm",
        "gross_cap_expenditure_nega", "operating_cash_flow"}


@pytest.mark.parametrize("key", ["free_cash_flow_to_firm", "gross_cap_expenditure_nega", "operating_cash_flow"])
def test_changed_forecast_cannot_supply_new_row_to_old_snapshot(monkeypatch, key):
    data, model = _saved(), _model()
    original = deepcopy(data)
    model["dataMap"]["2027"][key] += 0.01
    _source(monkeypatch, model)
    with pytest.raises(ValueError, match="eivät vastaa"):
        asyncio.run(legacy_investing.add_source_investing_cashflow(data, 42))
    assert data == original


@pytest.mark.parametrize("key", ["gross_capex", "operating_cash_flow"])
def test_missing_saved_anchor_does_not_borrow_a_current_source_value(monkeypatch, key):
    data = _saved()
    data["valuation_engine"]["dcf"][key][1] = None
    original = deepcopy(data)
    _source(monkeypatch, _model())
    with pytest.raises(ValueError, match="puuttuu lähdelukuja"):
        asyncio.run(legacy_investing.add_source_investing_cashflow(data, 42))
    assert data == original


def test_year_without_a_saved_cashflow_is_left_unknown(monkeypatch):
    data = _saved()
    data["valuation_engine"]["dcf"]["fcff"][1] = None
    _source(monkeypatch, _model())
    asyncio.run(legacy_investing.add_source_investing_cashflow(data, 42))
    assert data["valuation_engine"]["dcf"]["investing_cash_flow"] == [-1400, None]


@pytest.mark.parametrize("key", ["free_cash_flow_to_firm", "gross_cap_expenditure_nega",
                                  "operating_cash_flow", "cr_cf_gross_capex_nega"])
def test_missing_source_value_is_not_invented(monkeypatch, key):
    data, model = _saved(), _model()
    original = deepcopy(data)
    del model["dataMap"]["2027"][key]
    _source(monkeypatch, model)
    with pytest.raises(ValueError):
        asyncio.run(legacy_investing.add_source_investing_cashflow(data, 42))
    assert data == original


@pytest.mark.parametrize("identifier", [None, "", True, "42.5", 0])
def test_identifier_is_required_before_source_lookup(monkeypatch, identifier):
    calls = _source(monkeypatch, _model())
    with pytest.raises(ValueError, match="tunnistetta"):
        asyncio.run(legacy_investing.add_source_investing_cashflow(_saved(), identifier))
    assert calls == []


@pytest.mark.parametrize("country,source", [("DK", "profinder"), ("FI", "manual")])
def test_unrelated_inputs_are_untouched_without_source_call(monkeypatch, country, source):
    data = _saved()
    data["meta"].update(country_code=country, data_source=source)
    original = deepcopy(data)
    calls = _source(monkeypatch, _model())
    assert asyncio.run(legacy_investing.add_source_investing_cashflow(data, None)) is data
    assert data == original and calls == []


def test_complete_existing_investing_series_does_not_fetch_or_replace_it(monkeypatch):
    data = _saved()
    data["valuation_engine"]["dcf"]["investing_cash_flow"] = [-1400, -1800]
    original = deepcopy(data)
    calls = _source(monkeypatch, _model())
    assert asyncio.run(legacy_investing.add_source_investing_cashflow(data, None)) == original
    assert calls == []


def test_partial_series_is_replaced_only_after_source_verification(monkeypatch):
    data = _saved()
    data["valuation_engine"]["dcf"]["investing_cash_flow"] = [-1400, None]
    _source(monkeypatch, _model())
    asyncio.run(legacy_investing.add_source_investing_cashflow(data, 42))
    assert data["valuation_engine"]["dcf"]["investing_cash_flow"] == [-1400, -1800]


def test_partial_series_cannot_overwrite_a_known_disagreeing_old_value(monkeypatch):
    data = _saved()
    data["valuation_engine"]["dcf"]["investing_cash_flow"] = [77777, None]
    original = deepcopy(data)
    _source(monkeypatch, _model())
    with pytest.raises(ValueError, match="ei vastaa"):
        asyncio.run(legacy_investing.add_source_investing_cashflow(data, 42))
    assert data == original


def test_source_zero_investing_cashflow_is_preserved(monkeypatch):
    data, model = _saved(), _model()
    model["dataMap"]["2027"]["cr_cf_gross_capex_nega"] = 0
    _source(monkeypatch, model)
    asyncio.run(legacy_investing.add_source_investing_cashflow(data, 42))
    assert data["valuation_engine"]["dcf"]["investing_cash_flow"] == [-1400, 0]


def test_years_must_be_unique_aligned_and_within_requested_source_horizon(monkeypatch):
    data = _saved()
    model = _model()
    model["currentYear"] = 2027
    calls = _source(monkeypatch, model)
    with pytest.raises(ValueError, match="ennustevuosia"):
        asyncio.run(legacy_investing.add_source_investing_cashflow(data, 42))
    assert len(calls) == 1
    data["valuation_engine"]["dcf"]["years"] = [2026, 2026]
    with pytest.raises(ValueError, match="kohdistu"):
        asyncio.run(legacy_investing.add_source_investing_cashflow(data, 42))
    assert len(calls) == 1


def test_source_failure_leaves_snapshot_untouched(monkeypatch):
    data = _saved()
    original = deepcopy(data)
    async def unavailable(*args):
        raise RuntimeError("offline")
    monkeypatch.setattr(valuatum, "modeldata", unavailable)
    with pytest.raises(ValueError, match="ei voitu tarkistaa"):
        asyncio.run(legacy_investing.add_source_investing_cashflow(data, 42))
    assert data == original
