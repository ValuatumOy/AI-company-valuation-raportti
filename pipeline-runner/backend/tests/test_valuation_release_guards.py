import asyncio
from copy import deepcopy
from types import SimpleNamespace

import httpx
import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from app import assemble, debt_quality, fiscal_periods, main, runner, store, valuatum
from app.models import ForecastEdit, RedeemRoundIn, Round2In


def _irregular_source():
    return {"meta": {"data_source": "profinder", "country_code": "FI"},
            "actuals": {"years": [2025], "periods": [
                {"start": "2025-07-01", "end": "2025-12-31"}],
                "income_statement": {"net_sales": [3000]}}}


def test_legacy_finnish_input_without_identifier_cannot_bypass_source_verification():
    data = _irregular_source()
    del data["actuals"]["periods"]
    stage = {"id": "0", "order": 0, "name": "FAKTAT", "model": runner.DATA_FETCHER_MODEL}
    result = asyncio.run(runner._execute_stage(stage, {}, data, None, {}))
    assert result["status"] == "error"
    assert "tunnistetta" in result["error_message"]


@pytest.mark.parametrize("periods", [None, []])
def test_explicitly_missing_periods_are_not_treated_as_verified(periods):
    data = _irregular_source()
    data["actuals"]["periods"] = periods
    assert fiscal_periods.blockers(data)


def test_overlapping_annual_periods_are_blocked():
    data = {"actuals": {"years": [2024, 2025], "periods": [
        {"start": "2024-01-01", "end": "2024-12-31"},
        {"start": "2024-07-01", "end": "2025-06-30"}],
        "income_statement": {"net_sales": [100, 200]}}}
    assert any("päällekkäin" in item for item in fiscal_periods.blockers(data))


def test_positive_debt_bridge_is_not_silently_treated_as_a_deduction():
    data = {"actuals": {"years": [2025], "balance_sheet": {
        "interest_bearing_debt_source_total": [1000], "capital_loans": [100]}},
        "valuation_engine": {"dcf": {"bridge": {"interest_bearing_debt": 900}}}}
    assert any("etumerkki" in item for item in debt_quality.blockers(data))


def test_capital_loans_already_in_full_debt_bridge_are_not_subtracted_twice():
    data = {"actuals": {"years": [2025], "balance_sheet": {
        "interest_bearing_debt_source_total": [3000], "capital_loans": [1000]}},
        "valuation_engine": {"dcf": {"bridge": {"interest_bearing_debt": -3000}}}}
    assert debt_quality.blockers(data) == []


def test_negative_capital_loan_cannot_cancel_an_excess_debt_deduction():
    data = {"actuals": {"years": [2025], "balance_sheet": {
        "interest_bearing_debt_source_total": [1000], "capital_loans": [-200]}},
        "valuation_engine": {"dcf": {"bridge": {"interest_bearing_debt": -1200}}}}
    assert any("etumerkki" in item for item in debt_quality.blockers(data))


@pytest.mark.parametrize("values, bridge", [([10, "missing"], 0), ([10], 0), ([10, float("nan")], 0),
                                             ([10, 0], float("inf")), ([10, 0], True)])
def test_finnish_source_requires_finite_debt_for_same_actual_year(values, bridge):
    data = {"meta": {"data_source": "profinder", "country_code": "FI"},
            "actuals": {"years": [2024, 2025], "periods": [
                {"start": "2024-01-01", "end": "2024-12-31"},
                {"start": "2025-01-01", "end": "2025-12-31"}],
                "income_statement": {"net_sales": [10, 10]},
                "balance_sheet": {"interest_bearing_debt_source_total": values}},
            "valuation_engine": {"dcf": {"bridge": {"interest_bearing_debt": bridge}}}}
    with pytest.raises(ValueError, match="korollisia velkoja"):
        asyncio.run(runner.prepare_stage0(data, None))


def test_new_execution_persists_validated_timing_with_its_own_snapshot(monkeypatch):
    saved = {"meta": {"data_source": "profinder", "country_code": "FI"},
             "actuals": {"years": [2025], "periods": [
                 {"start": "2025-01-01", "end": "2025-12-31"}],
                 "income_statement": {"net_sales": [100]},
                 "balance_sheet": {"interest_bearing_debt_source_total": [100], "capital_loans": [0]}},
             "forecast": {"years": [2030], "income_statement": {"depreciation_total": [-10]}},
             "valuation_engine": {"wacc_parameters": {"wacc_pct": 10}, "dcf": {
                 "bridge": {"interest_bearing_debt": -100}, "years": [2030],
                 "depreciation_total": [10], "ebit": [100], "taxes_paid": [0],
                 "change_in_working_capital": [0], "investing_cash_flow": [-10],
                 "fcff": [100], "discounted_fcff": [100 / 1.1 ** 4.5]}}}
    existing = {"results": [{"order": 0, "name": "FAKTAT", "status": "ok", "parsed_json": saved}],
                "total_cost_usd": 0}
    writes, contexts = [], []
    monkeypatch.setattr(store, "get_run", lambda rid: existing)
    monkeypatch.setattr(store, "upsert_result", lambda rid, res: writes.append(deepcopy(res)))
    monkeypatch.setattr(store, "set_run_status", lambda *args: None)
    monkeypatch.setattr(store, "add_run_cost", lambda *args: None)
    monkeypatch.setattr(runner, "_spend_cap_exceeded", lambda rid: None)
    async def fake_stage(stage, context, *args, **kwargs):
        contexts.append(deepcopy(context["input_data"]))
        return {"order": stage["order"], "name": stage["name"], "status": "ok", "parsed_json": {}}
    monkeypatch.setattr(runner, "_execute_stage", fake_stage)
    stages = [{"id": "zero", "order": 0, "name": "FAKTAT", "enabled": True},
              {"id": "two", "order": 2, "name": "Writer", "enabled": True,
               "model": "local-test", "prompt_template": "test"}]
    async def execute():
        return [event async for event in runner._run_stages(
            {"id": "child", "identifier": "42", "params": {}}, stages, from_order=2)]
    asyncio.run(execute())
    persisted = next(row["parsed_json"] for row in writes if row["order"] == 0)
    assert persisted["meta"]["scenario_timing_version"] == 2
    assert contexts[0] == persisted
    assert "scenario_timing_version" not in saved["meta"]


def test_fresh_statement_replaces_stale_reused_deterministic_table():
    sections = [{"id": "6", "blocks": [{"type": "table",
        "table_id": "deterministic_income_statement_forecast", "rows": [["Myyntikate", "9"]]}]}]
    data = {"forecast": {"years": [2026], "income_statement": {
        "gross_profit": [9], "personnel_costs": [-700], "other_operating_costs": [-600],
        "other_operating_income": [0], "ebitda": [500]}}}
    assemble._inject_financial_statement_blocks(sections, data)
    blocks = sections[0]["blocks"]
    assert not any(row[0] == "Myyntikate" for b in blocks for row in b.get("rows", []))
    assert sum(b.get("table_id") == "deterministic_forecast_gross_profit_quality" for b in blocks) == 1


def test_readiness_enforces_stage_zero_and_assembled_blockers(monkeypatch):
    run = {"status": "ok", "results": [{"order": 0, "name": "FAKTAT", "status": "error"}]}
    monkeypatch.setattr(store, "get_run", lambda rid: run)
    monkeypatch.setattr(assemble, "assemble", lambda _: {
        "sections": [{"id": sid} for sid in runner.SECTION_ORDER],
        "_release_blockers": ["skenaarion luvut eivät täsmää"]})
    readiness = store.report_readiness("test")
    assert not readiness["ready"]
    assert any("stage 0" in issue for issue in readiness["issues"])
    assert "skenaarion luvut eivät täsmää" in readiness["issues"]


@pytest.mark.parametrize("function", [main.report_html, main.report_pdf])
def test_customer_cannot_force_an_unready_report(monkeypatch, function):
    monkeypatch.setattr(main, "_require_run_access", lambda *args: {})
    monkeypatch.setattr(store, "final_report_json", lambda rid: {"report_type": "test"})
    monkeypatch.setattr(store, "report_readiness", lambda rid: {"ready": False, "issues": ["invalid"]})
    request = SimpleNamespace(state=SimpleNamespace(access_key="exp_customer"))
    with pytest.raises(HTTPException) as exc:
        function("test", request, force=1)
    assert exc.value.status_code == 409


def test_checkout_preflight_binds_selected_company_and_model_before_source_check(monkeypatch):
    monkeypatch.setattr(main, "_APP_TOKEN", "admin-secret")
    monkeypatch.setattr(main, "_rate_ok", lambda *args: True)
    checked = []
    async def search(query):
        return [{"fid": 42, "company_code": "1234567-8"}]
    async def preflight(fid):
        checked.append(fid)
    monkeypatch.setattr(valuatum, "search_company", search)
    monkeypatch.setattr(valuatum, "preflight_fiscal_periods", preflight)
    client = TestClient(main.app)
    assert client.post("/api/public/valuation-preflight", json={"fid": 43, "business_id": "1234567-8"}).status_code == 422
    assert checked == []
    response = client.post("/api/public/valuation-preflight", json={"fid": 42, "business_id": "1234567-8"})
    assert response.status_code == 200 and response.json() == {"ready": True}
    assert checked == [42]


@pytest.mark.parametrize("error, status", [(ValueError("bad source"), 422),
                                             (httpx.ConnectError("offline"), 503)])
def test_checkout_preflight_fails_closed_without_exposing_source_error(monkeypatch, error, status):
    monkeypatch.setattr(main, "_rate_ok", lambda *args: True)
    async def search(query):
        return [{"fid": 42, "company_code": "1234567-8"}]
    async def preflight(fid):
        raise error
    monkeypatch.setattr(valuatum, "search_company", search)
    monkeypatch.setattr(valuatum, "preflight_fiscal_periods", preflight)
    response = TestClient(main.app).post("/api/public/valuation-preflight", json={"fid": 42, "business_id": "1234567-8"})
    assert response.status_code == status
    assert str(error) not in response.text


def test_cold_model_preflight_can_use_historical_debt_without_inventing_forecast_bridge(monkeypatch):
    model = {"currentYear": 2026, "dataMap": {"2025": {
        "cr_fiscal_year_start": 20250101, "cr_fiscal_year_end": 20251231,
        "ns": 1, "ebit": 0.1, "bs_total_assets": 2,
        "liab_ib_total": 1, "interest_bearing_liabilities_calc": 1.2, "cr_capital_loan_lt": 0.2}}}
    async def source(*args):
        return {"42": model}
    monkeypatch.setattr(valuatum, "modeldata", source)
    asyncio.run(valuatum.preflight_fiscal_periods(42))
    model["dataMap"]["2025"]["interest_bearing_liabilities_calc"] = 1.5
    with pytest.raises(ValueError, match="täsmäyttämättä"):
        asyncio.run(valuatum.preflight_fiscal_periods(42))


def test_invalid_parent_is_refused_before_import_or_new_paid_round(monkeypatch):
    source = _irregular_source()
    parent = {"identifier": "42", "params": {}, "results": [{"order": 0,
              "status": "ok", "parsed_json": source}]}
    before = deepcopy(parent)
    async def no_import(*args, **kwargs):
        pytest.fail("Paid import must not run for an invalid parent")
    monkeypatch.setattr(main.forecast_import, "import_and_wait", no_import)
    with pytest.raises(HTTPException) as exc:
        asyncio.run(main._start_forecast_import_round("test", parent, [ForecastEdit(varname="ns", year=2026, value=5)]))
    assert exc.value.status_code == 422 and parent == before


def test_invalid_parent_is_refused_before_checkout_or_paid_token_claim(monkeypatch):
    parent = {"identifier": "42", "params": {}, "results": [{"order": 0,
              "status": "ok", "parsed_json": _irregular_source()}]}
    request = SimpleNamespace(state=SimpleNamespace(access_key=None))
    monkeypatch.setattr(main, "STRIPE_SECRET_KEY", "test")
    monkeypatch.setattr(main, "_require_run_access", lambda *args: parent)
    monkeypatch.setattr(main, "_check_not_paused", lambda: None)
    monkeypatch.setattr(store, "get_run", lambda rid: parent)
    monkeypatch.setattr(store, "get_pending_round", lambda token: {"run_id": "test", "consumed": False})
    monkeypatch.setattr(store, "claim_pending_round", lambda token: pytest.fail("must not claim token"))
    monkeypatch.setattr(store, "create_pending_round", lambda *a, **k: pytest.fail("must not create checkout"))
    async def stripe(*args):
        return {"payment_status": "paid", "metadata": {"token": "token-123"}}
    monkeypatch.setattr(main, "_stripe_get_checkout_session", stripe)
    with pytest.raises(HTTPException) as exc:
        asyncio.run(main.round2_checkout("test", Round2In(), request))
    assert exc.value.status_code == 422
    with pytest.raises(HTTPException) as exc:
        asyncio.run(main.round2_redeem("test", RedeemRoundIn(token="token-123", stripe_session_id="session-123"), request))
    assert exc.value.status_code == 422
