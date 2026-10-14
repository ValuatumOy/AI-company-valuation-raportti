import asyncio
import copy
import json
import threading
from pathlib import Path

import pytest
from pydantic import ValidationError

from app import db, openrouter, peers, report_context, runner, store, validators
from app.models import DATA_FETCHER_MODEL, RunIn
from scripts.prepare_report_demo import generation_readiness, prepare
from valuatum_kit.export_modeldata_json import build_payload

BACKEND = Path(__file__).resolve().parents[1]


@pytest.fixture
def isolated_run_db(monkeypatch, tmp_path):
    monkeypatch.setattr(db, "_DB_PATH", str(tmp_path / "report-context.db"))
    monkeypatch.setattr(db, "_local", threading.local())
    monkeypatch.setattr(db, "_initialized", False)
    yield
    if getattr(db._local, "conn", None):
        db._local.conn.close()


def _input(currency="DKK", country="DK"):
    return {"meta": {"company_name": "Example", "country_code": country,
                     "currency": currency, "unit": "t" + currency},
            "actuals": {"unit": "t" + currency, "years": [2025],
                        "income_statement": {"net_sales": [12500]}},
            "forecast": {"unit": "t" + currency, "years": []}, "peers": []}


@pytest.mark.parametrize("country,currency", [("FI", "EUR"), ("DK", "DKK"), ("US", "USD")])
def test_canonical_input_is_copied_without_fx(country, currency):
    source = _input(currency, country)
    before = copy.deepcopy(source)
    normalized = report_context.normalize_input(source, {"report_language": "en"})
    assert source == before
    assert normalized["unit"] == normalized["meta"]["unit"] == "t" + currency
    assert normalized["meta"]["report_language"] == "en"
    assert normalized["actuals"]["income_statement"]["net_sales"] == [12500]
    normalized["actuals"]["years"].append(2026)
    assert source == before


@pytest.mark.parametrize("params", [{"report_language": "da"}, {"country_code": "GB"},
                                    {"source_currency": "GBP"}])
def test_unsupported_context_rejected(params):
    with pytest.raises(ValueError):
        report_context.normalize_input(_input(), params)


def test_conflicting_currency_or_units_never_relabelled():
    with pytest.raises(ValueError, match="currency"):
        report_context.normalize_input(_input(), {"source_currency": "USD"})
    source = _input()
    source["forecast"]["unit"] = "tEUR"
    with pytest.raises(ValueError, match="forecast.unit"):
        report_context.normalize_input(source)
    with pytest.raises(ValueError, match="verified"):
        report_context.resolve_context(None, {"country_code": "DK"})
    with pytest.raises(ValueError, match="explicitly"):
        report_context.normalize_input({"meta": {"currency": "USD", "country_code": "US"}})
    source = _input()
    source["valuation_engine"] = {"dcf": {"unit": "tEUR"}}
    with pytest.raises(ValueError, match="dcf.unit"):
        report_context.normalize_input(source)


def test_foreign_peers_require_verified_matching_money_unit():
    data = _input()
    data["peers"] = [{"name": "Finnish peer", "revenue_teur": 1000}]
    with pytest.raises(ValueError, match="peer"):
        report_context.normalize_input(data)


@pytest.mark.parametrize("currency,country", [("DKK", "DK"), ("USD", "US"), ("EUR", "FI")])
def test_raw_model_millions_scaled_to_thousands_of_source_currency(currency, country):
    model = {"companyName": "Example", "currency": currency, "currentYear": 2026,
             "dataMap": {"2025": {"ns": 12.5, "cr_cash_and_bank_deposits": 1.2},
                         "2026": {"ns": 13.0, "value_of_equity_fcff": 42.5}}}
    payload = build_payload(model, report_language="en", country_code=country,
                            source_currency=currency)
    assert payload["actuals"]["income_statement"]["net_sales"] == [12500]
    assert payload["valuation_engine"]["dcf"]["equity_value_before_floor"] == 42500
    for block in (payload, payload["meta"], payload["actuals"], payload["forecast"],
                  payload["valuation_engine"]):
        assert block["unit"] == "t" + currency
    assert payload["meta"]["currency"] == currency
    assert payload["forecast"]["is_system_deterministic"] is (country == "FI")
    with pytest.raises(ValueError, match="conflicts"):
        build_payload(model, country_code=country,
                      source_currency="USD" if currency != "USD" else "DKK")


def test_default_finland_templates_and_validator_are_unchanged():
    context = report_context.resolve_context()
    for name in ("singlewriter.txt", "1_enrichment.txt"):
        original = (BACKEND / "prompts" / name).read_text(encoding="utf-8")
        assert report_context.adapt_template(original, context) == original
    code = (BACKEND / "validators_seed/stage6_final.py").read_text(encoding="utf-8")
    assert report_context.adapt_validator(code, context) == code
    assert report_context.system_directive(context) is None


def test_unrelated_generic_pipeline_input_remains_unconstrained_and_unchanged():
    data = {"meta": ["arbitrary metadata"], "unit": "items", "records": [1, 2]}
    request = RunIn(pipeline_id="generic", input_data=data, params={"custom": "value"})
    assert request.input_data == data
    assert report_context.normalize_input(data, request.params) == data
    assert report_context.resolve_context(data, request.params) == report_context.resolve_context()


def test_persisted_writer_and_enrichment_adapt_all_explicit_locale_conflicts():
    context = report_context.resolve_context(_input(), {"report_language": "en"})
    original = (BACKEND / "prompts/singlewriter.txt").read_text(encoding="utf-8")
    template = report_context.adapt_template(original, context)
    for forbidden in ('"language": "fi"', "suomenkielinen AI", "ymmärrettävää suomea",
                      "Suomenkieliset termit:", '"Matala | Kohtalainen | Korkea"',
                      '"Heikko | Kohtalainen | Hyvä"', "tEUR", "M€"):
        assert forbidden not in template
    assert '"language": "en"' in template
    assert '"country_code": "DK"' in template
    assert '"currency": "DKK"' in template
    assert '"Low | Moderate | High"' in template
    assert '"Weak | Moderate | Good"' in template
    assert '"value_teur"' in template  # Technical keys remain compatible.
    assert "M DKK" in template
    assert "MDKK" not in template
    enrichment = report_context.adapt_template(
        (BACKEND / "prompts/1_enrichment.txt").read_text(encoding="utf-8"), context)
    assert "SUOMALAISET VERROKKIEHDOKKAAT" not in enrichment
    assert "Suomen-markkinan" not in enrichment
    directive = report_context.system_directive(context)
    for scope in ("metadata", "scoring", "clarifications", "machine_readable",
                  "Finnish corporate tax", "Translate every prescribed verbatim phrase"):
        assert scope in directive


def _stage(order=2, model="test/model", template="{{input_data}}"):
    return {"id": "stage", "order": order, "name": "writer", "model": model,
            "prompt_template": template, "temperature": 0.2, "max_tokens": 16000,
            "reasoning_effort": "medium", "expects_json": True, "validator_code": None}


def test_manual_stage_zero_never_fetches_or_mutates(monkeypatch):
    async def forbidden(*args, **kwargs):
        raise AssertionError("Network fetch forbidden")
    monkeypatch.setattr(runner, "fetch_company_data", forbidden)
    source = _input()
    result = asyncio.run(runner._execute_stage(
        _stage(0, DATA_FETCHER_MODEL), {}, source, "133268", {"report_language": "en"}))
    assert result["status"] == "ok"
    assert result["parsed_json"]["meta"]["report_language"] == "en"
    assert "report_language" not in source["meta"]
    result = asyncio.run(runner._execute_stage(
        _stage(0, DATA_FETCHER_MODEL), {}, None, "133268",
        {"country_code": "DK", "source_currency": "DKK"}))
    assert result["status"] == "error"
    assert "automatic Finland export is disabled" in result["error_message"]


def test_english_system_directive_survives_truncation_and_correction(monkeypatch):
    calls = []
    async def chat(**kwargs):
        calls.append(kwargs)
        return {"text": "{" if len(calls) == 1 else "{}", "request_payload": {},
                "finish_reason": "length" if len(calls) == 1 else "stop",
                "tokens_prompt": 1, "tokens_completion": 1}
    monkeypatch.setattr(openrouter, "chat", chat)
    result = asyncio.run(runner._execute_stage(
        _stage(template='"language": "fi" {{input_data}}'), {"input_data": _input()},
        _input(), None, {"report_language": "en"},
        correction={"feedback": "Fix value", "previous": {"sections": []}}))
    assert result["status"] == "ok"
    assert len(calls) == 2
    assert calls[0]["system_prompt"] == calls[1]["system_prompt"]
    assert "in English" in calls[0]["system_prompt"]
    assert '"language": "en"' in calls[0]["prompt"]
    assert "Fix value" in calls[1]["prompt"]


@pytest.mark.parametrize("provider", ["openrouter", "google"])
@pytest.mark.parametrize("directive", [None, "Write in English"])
def test_provider_system_payload_is_optional_and_uses_real_system_role(monkeypatch, provider, directive):
    sent = []
    class Client:
        def __init__(self, **kwargs):
            pass
        async def __aenter__(self):
            return self
        async def __aexit__(self, *args):
            return False
        async def post(self, endpoint, headers, json):
            sent.append(json)
            body = ({"choices": [{"message": {"content": "{}"}, "finish_reason": "stop"}]}
                    if provider == "openrouter" else
                    {"candidates": [{"content": {"parts": [{"text": "{}"}]}, "finishReason": "STOP"}]})
            return openrouter.httpx.Response(200, json=body)
    monkeypatch.setattr(openrouter.httpx, "AsyncClient", Client)
    monkeypatch.setattr(openrouter, "_google_headers", lambda: {})
    method = openrouter._openrouter_chat if provider == "openrouter" else openrouter._google_chat
    asyncio.run(method(model="test/model", prompt="data", system_prompt=directive))
    if provider == "openrouter":
        assert sent[0]["messages"] == ([{"role": "system", "content": directive}]
                                       if directive else []) + [{"role": "user", "content": "data"}]
    else:
        assert sent[0].get("systemInstruction") == ({"parts": [{"text": directive}]} if directive else None)


def test_english_final_validator_accepts_names_disclaimer_and_decimal_point():
    code = (BACKEND / "validators_seed/stage6_final.py").read_text(encoding="utf-8")
    context = report_context.resolve_context(_input(), {"report_language": "en"})
    report = {"cover": {"headline_value": "1 250 tDKK", "base_case_value": "1 000 tDKK"},
              "expected_value": {"value": 1250},
              "machine_readable": {"scenarios": [
                  {"name": "Pessimistic", "value_teur": 500, "probability_pct": 25},
                  {"name": "Conservative", "value_teur": 1000, "probability_pct": 50},
                  {"name": "Optimistic", "value_teur": 2500, "probability_pct": 25}]},
              "sections": [{"id": "16", "blocks": [{"type": "paragraph",
                               "text": "This report is not investment advice."}]}]}
    adapted = report_context.adapt_validator(code, context)
    result = validators.run_validator(adapted, report, {})
    assert result["passed"], result
    scenario_check = next(c for c in result["checks"] if "scenarios schema" in c["name"])
    assert scenario_check["detail"] == "ok"
    namespace = {}
    exec(adapted, namespace)
    assert namespace["_first_num"]("9.46 %") == 9.46
    assert namespace["_first_num"]("1 250 tDKK") == 1250
    assert namespace["_first_num"]("1,500 tDKK") == 1500
    assert namespace["_first_num"]("1,500,000 tDKK") == 1500000
    assert namespace["_first_num"]("1,500.25 tDKK") == 1500.25
    original = {}
    exec(code, original)
    assert original["_first_num"]("9,46 %") == 9.46


def test_foreign_runner_skips_finland_peer_resolution_and_keeps_caller_data(monkeypatch, isolated_run_db):
    db.init_db()
    pipeline = store.create_pipeline("foreign-demo-test")
    pid = pipeline["id"]
    store.add_stage(pid, {"order": 0, "name": "facts", "model": DATA_FETCHER_MODEL})
    store.add_stage(pid, {"order": 1, "name": "enrichment", "model": "test/model",
                          "prompt_template": "{{input_data}}"})
    async def no_peers(*args, **kwargs):
        raise AssertionError("Automatic Finnish peer resolution forbidden")
    async def chat(**kwargs):
        assert "tDKK" in kwargs["prompt"]
        assert "Country is DK" in kwargs["system_prompt"]
        return {"text": '{"competitors": []}', "request_payload": {}, "finish_reason": "stop",
                "tokens_prompt": 1, "tokens_completion": 1}
    monkeypatch.setattr(peers, "resolve", no_peers)
    monkeypatch.setattr(openrouter, "chat", chat)
    source = _input()
    rid = store.create_run(pid, source, True, params={"report_language": "en"})
    run = store.get_run(rid)
    before = copy.deepcopy(run)
    async def drain():
        return [ev async for ev in runner._run_stages(run, store.get_pipeline(pid)["stages"])]
    asyncio.run(drain())
    assert run == before
    result = store.get_run(rid)
    assert result["status"] == "ok"
    assert result["results"][0]["parsed_json"]["meta"]["report_language"] == "en"


def test_admin_schema_rejects_unsafe_foreign_fetch_and_conflicting_units():
    with pytest.raises(ValidationError):
        RunIn(pipeline_id="local", identifier="133268",
              params={"country_code": "DK", "source_currency": "DKK"})
    data = _input()
    data["unit"] = "tEUR"
    with pytest.raises(ValidationError):
        RunIn(pipeline_id="local", input_data=data)
    assert RunIn(pipeline_id="local", input_data=_input(), params={"report_language": "en"})


def test_offline_preparation_rejects_stale_amazon_and_does_not_require_app_token(monkeypatch):
    monkeypatch.delenv("APP_TOKEN", raising=False)
    data = _input("USD", "US")
    request = prepare(data, input_format="normalized", pipeline_id="isolated-local",
                      country_code="US", source_currency="USD", minimum_actual_year=2024)
    assert request["input_data"]["unit"] == "tUSD"
    assert "identifier" not in request
    assert request["params"]["report_language"] == "en"
    data["actuals"]["years"] = [2018]
    with pytest.raises(ValueError, match="Stale"):
        prepare(data, input_format="normalized", pipeline_id="isolated-local",
                country_code="US", source_currency="USD", minimum_actual_year=2024)


def test_offline_raw_envelope_preparation_filters_quarters_keeps_cvr_and_gates_forecasts():
    model = {"companyName": "JYSK A/S", "companyCode": "13590400", "currency": "DKK",
             "currentYear": 2026, "dataMap": {"0": {"ns": 999}, "20251": {"ns": 5},
             "2025": {"ns": 125}, "2026": {"ns": 130, "ebit": 13, "wacc": .10,
                                                       "value_of_equity_fcff": 999}}}
    envelope = {"133268": model}
    before = copy.deepcopy(envelope)
    request = prepare(envelope, input_format="valuatum-model", pipeline_id="local-demo",
                      country_code="DK", source_currency="DKK", minimum_actual_year=2024,
                      source_metadata={"currency": "DKK", "provenance": {
                          "endpoint_host": "upcloudtest.valuatum.com"}})
    assert envelope == before
    data = request["input_data"]
    assert data["meta"]["y_tunnus"] == "13590400"
    assert data["actuals"]["years"] == [2025]
    assert data["forecast"]["years"] == [2026]
    readiness = generation_readiness(data)
    assert readiness["numerical_input_ready"]
    assert readiness["source_country"] == "DK"
    assert readiness["source_provenance"]["endpoint_host"] == "upcloudtest.valuatum.com"
    assert readiness["generation_approved"] is False
    assert readiness["production_verified"] is False
    data["forecast"]["years"] = []
    assert not generation_readiness(data)["numerical_input_ready"]
    assert "No source forecast years supplied" in generation_readiness(data)["blockers"]


def test_selected_sec_history_is_scaled_without_inventing_valuation_or_aggregate_debt():
    source = {"company": {"name": "Amazon.com, Inc.", "cik": "0001018724",
                          "isin": "US0231351067", "ticker": "AMZN"},
              "filing": {"form": "10-K", "filed": "2026-02-06"},
              "reporting": {"currency": "USD", "prepared_unit": "USD millions"},
              "historical": {"years": [2023, 2024, 2025], "currency": "USD", "unit": "USD millions",
                 "income_statement": {"net_sales": [100, 110, 120], "operating_income": [10, 11, 12],
                                      "net_income": [5, 6, 7], "depreciation_amortization": [4, 5, 6]},
                 "balance_sheet": {"stockholders_equity": [20, 21, 22],
                                   "long_term_debt_current": [1, 2, 3],
                                   "long_term_debt_noncurrent": [10, 20, 30]},
                 "cash_flow": {"operating_cash_flow": [40, 50, 60]}},
              "fact_provenance": {"net_sales": {"tag": "RevenueFromContractWithCustomerExcludingAssessedTax"}}}
    before = copy.deepcopy(source)
    request = prepare(source, input_format="sec-companyfacts", pipeline_id="local-demo",
                      country_code="US", source_currency="USD", minimum_actual_year=2024)
    assert source == before
    data = request["input_data"]
    assert data["actuals"]["income_statement"]["net_sales"] == [100000, 110000, 120000]
    assert data["actuals"]["income_statement"]["ebit"] == [10000, 11000, 12000]
    assert data["meta"]["isin"] == "US0231351067"
    assert data["source_provenance"]["fact_provenance"] == source["fact_provenance"]
    assert "interest_bearing_debt" not in data["actuals"]["balance_sheet"]
    assert "ebitda" not in data["actuals"]["income_statement"]
    assert data["forecast"]["years"] == []
    assert data["valuation_engine"] == {"unit": "tUSD"}
    assert not generation_readiness(data)["numerical_input_ready"]
    assert "Source WACC assumption is missing" in generation_readiness(data)["blockers"]
    source["reporting"]["prepared_unit"] = "USD"
    with pytest.raises(ValueError, match="USD millions"):
        prepare(source, input_format="sec-companyfacts", pipeline_id="local-demo",
                country_code="US", source_currency="USD", minimum_actual_year=2024)


@pytest.mark.parametrize("invalid", [float("nan"), float("inf"), float("-inf")])
def test_readiness_rejects_nonfinite_forecasts_and_valuation_values(invalid):
    data = _input()
    data["forecast"] = {"years": [2026], "net_sales": [invalid], "ebit": [10]}
    data["valuation_engine"] = {"wacc_parameters": {"wacc_pct": 10},
                                "dcf": {"equity_value_before_floor": invalid}}
    readiness = generation_readiness(data)
    assert readiness["numerical_input_ready"] is False
    assert "Complete source forecast net_sales is missing" in readiness["blockers"]
    assert "Source DCF equity value is missing" in readiness["blockers"]
