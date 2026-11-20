"""Swedish automatic orders: own source, organisation-number identity, SEK end to end."""
import asyncio
import json
from pathlib import Path

import httpx
import pytest
from starlette.testclient import TestClient

from app import estimate_trigger, forecast_import, main, seed, store, valuatum
from valuatum_kit import config, export_modeldata_json as exporter


def _enabled(monkeypatch):
    for key, value in {
        "VALUATUM_API_BASE_URL": "https://finnish-source.example/rest",
        "VALUATUM_TOKEN": "fi-test-token",
        "VALUATUM_SE_API_BASE_URL": "https://swedish-source.example/rest",
        "VALUATUM_SE_TOKEN": "se-test-token",
        "VALUATUM_SE_ORDERS_ENABLED": "1",
        "VALUATUM_SE_FORECASTS_VERIFIED": "1",
        "VALUATUM_SE_SOURCE_UNIT": "millions",
        "INTERNATIONAL_CLIENT_SITE_URL": "https://company-valuation.example",
    }.items():
        monkeypatch.setenv(key, value)
    monkeypatch.setattr(main, "STRIPE_SECRET_KEY", "test-only")
    main._RATE_HITS.clear()


def _model():
    data = {"2017": {"equity_beta": 1.0}, "2024": {"ns": 19, "ebit": 1}, "2025": {"ns": 25, "ebit": 2}}
    for year in range(2026, 2036):
        data[str(year)] = {
            "ns": 28, "ebit": 3, "wacc": .0946, "operating_cash_flow": 3,
            "gross_cap_expenditure_nega": 1, "free_operating_cash_flow": 2,
            "free_cash_flow_to_firm": 2, "disc_fcff": 1.8, "cum_disc_fcff": 40,
            "ib_debt_nega_prev_year": -25, "cash_prev_year": 1,
            "value_of_equity_fcff": 16, "noplat": 2, "cost_of_cap_abs": -1,
            "eva": 1, "disc_eva": .9, "cum_disc_eva": 10,
            "prol_cap_invested": 20, "value_of_equity_eva": 16,
        }
    return {"companyName": "Husqvarna Stockholm Aktiebolag", "companyCode": "556983-9805",
            "currency": "SEK", "currentYear": 2026, "dataMap": data}


def test_organisation_number_forms_and_gate(monkeypatch):
    for typed in ("556983-9805", "5569839805", "SE 556983 9805"):
        assert valuatum._normalize_query(typed, "SE") == ("code", "556983-9805")
    assert valuatum._normalize_query("Husqvarna", "SE") == ("name", "Husqvarna")
    monkeypatch.setenv("VALUATUM_SE_API_BASE_URL", "https://swedish-source.example/rest")
    monkeypatch.setenv("VALUATUM_SE_TOKEN", "se-test-token")
    monkeypatch.delenv("VALUATUM_SE_ORDERS_ENABLED", raising=False)
    with pytest.raises(ValueError, match="Swedish automatic orders await"):
        config.require_country_orders("SE")
    assert main.public_capabilities()["countries"]["SE"] == {
        "enabled": False, "currency": "SEK", "currencies": ["SEK"], "language": "en"}


def test_search_and_estimates_use_only_the_swedish_source(monkeypatch):
    _enabled(monkeypatch)
    hosts = []
    def handler(request):
        hosts.append(request.url.host)
        assert request.headers["authorization"] == "Bearer se-test-token"
        if request.url.path.endswith("/company"):
            assert request.url.params["code"] == "556983-9805"
            return httpx.Response(200, json=[
                {"companyCode": "556983-9805", "companyName": "Husqvarna Stockholm Aktiebolag",
                 "models": [{"followedModelId": 585240, "analystName": "user_estimates_saver user_estimates_saver"},
                            {"followedModelId": 52623, "analystName": "Data Import"}]},
                {"companyCode": "5569839805", "companyName": "Malformed", "models": [{"followedModelId": 1}]}])
        return httpx.Response(202, json={"jobId": 5, "status": "OK"})
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    monkeypatch.setattr(valuatum, "_CLIENT", client)
    monkeypatch.setattr(valuatum, "_ROWS_CACHE", {})
    rows = asyncio.run(valuatum.search_company("5569839805", country="SE"))
    assert [(r["fid"], r["country_code"], r["report_language"]) for r in rows] == [(52623, "SE", "en")]
    real_client = httpx.AsyncClient
    monkeypatch.setattr(estimate_trigger.httpx, "AsyncClient",
                        lambda **kwargs: real_client(transport=httpx.MockTransport(handler), **kwargs))
    asyncio.run(estimate_trigger.trigger_and_wait(52623, country="SE"))
    assert set(hosts) == {"swedish-source.example"}


def test_readiness_generates_estimates_before_reading_the_model(monkeypatch):
    _enabled(monkeypatch)
    monkeypatch.setattr(valuatum, "_GENERATED", {})
    calls = []
    async def search(query, country="FI"):
        calls.append(("search", query, country))
        return [{"fid": 52623, "company_name": "Husqvarna Stockholm Aktiebolag",
                 "company_code": "556983-9805", "country_code": "SE"}]
    async def generate(fid, country="FI"):
        calls.append(("generate", fid, country))
    async def data(fids, var_poses, country="FI"):
        calls.append(("modeldata", fids[0], country))
        return {"52623": _model()}
    monkeypatch.setattr(valuatum, "search_company", search)
    monkeypatch.setattr(estimate_trigger, "trigger_and_wait", generate)
    monkeypatch.setattr(valuatum, "modeldata", data)
    result = asyncio.run(valuatum.company_readiness("SE", 52623, "556983-9805", generate=True))
    assert result["eligible"], result["reason"]
    assert result["currency"] == "SEK" and result["actual_year"] == 2025
    assert result["actual_years"] == [2024, 2025] and result["negative_equity"] is False
    assert calls == [("search", "556983-9805", "SE"), ("generate", 52623, "SE"), ("modeldata", 52623, "SE")]
    # The confirmation after the pre-purchase notices re-checks without generating again.
    asyncio.run(valuatum.company_readiness("SE", 52623, "556983-9805", generate=True))
    assert [c[0] for c in calls].count("generate") == 1
    distressed = _model()
    distressed["dataMap"]["2026"].update(ib_debt_nega_prev_year=-100, value_of_equity_fcff=-59, value_of_equity_eva=-59)
    async def distressed_data(fids, var_poses, country="FI"):
        return {"52623": distressed}
    monkeypatch.setattr(valuatum, "modeldata", distressed_data)
    assert asyncio.run(valuatum.company_readiness("SE", 52623, "556983-9805"))["negative_equity"] is True
    with pytest.raises(ValueError, match="organisation number"):
        asyncio.run(valuatum.company_readiness("SE", 52623, "5569839805"))


def test_parameter_only_years_before_the_first_statement_are_dropped():
    payload = exporter.build_payload(_model(), [], report_language="en", country_code="SE", source_currency="SEK")
    assert payload["actuals"]["years"] == [2024, 2025]
    assert payload["unit"] == "tSEK" and payload["meta"]["business_id"] == "556983-9805"


def test_paid_swedish_checkout_binds_the_swedish_source(monkeypatch):
    _enabled(monkeypatch)
    seed.ensure_seeded()
    starts = []
    monkeypatch.setattr(main, "_start_bg", lambda *args, **kwargs: starts.append((args, kwargs)) or True)
    metadata = {"product": main.VALUATION_PRODUCT_TAG, "country": "SE", "reportLanguage": "en",
                "reportCurrency": "SEK", "companyCode": "556983-9805", "businessId": "556983-9805",
                "fid": "52623", "customerEmail": "paid-owner@example.com", "forecast": "true"}
    async def paid(*args, **kwargs):
        return {"payment_status": "paid", "metadata": metadata}
    async def search(query, country="FI"):
        assert query == "556983-9805" and country == "SE"
        return [{"fid": 52623, "company_name": "Husqvarna Stockholm Aktiebolag",
                 "company_code": "556983-9805", "country_code": "SE"}]
    readiness_calls = []
    async def readiness(*args, **kwargs):
        readiness_calls.append((args, kwargs))
        return {"eligible": True, "currency": "SEK", "reason": None}
    monkeypatch.setattr(main, "_stripe_get_checkout_session", paid)
    monkeypatch.setattr(valuatum, "search_company", search)
    monkeypatch.setattr(valuatum, "company_readiness", readiness)
    body = {"business_id": "556983-9805", "fid": 52623, "company_name": "Forged name ignored",
            "email": "forged@example.com", "country": "SE", "report_language": "en",
            "stripe_session_id": "cs_swedish_automatic_test"}
    client = TestClient(main.app)
    for key, value in [("business_id", "556570-0845"), ("fid", 999), ("country", "DK")]:
        assert client.post("/api/public/checkout-generate", json={**body, key: value}).status_code in (409, 503)
    first = client.post("/api/public/checkout-generate", json=body)
    assert first.status_code == 200, first.text
    assert client.post("/api/public/checkout-generate", json=body).json() == first.json()
    assert len(starts) == 1
    # Estimates were generated at the pre-checkout check; payment never regenerates them here.
    assert readiness_calls == [(("SE", 52623, "556983-9805"), {})]
    run = store.get_run(first.json()["run_id"])
    assert run["params"]["country_code"] == "SE" and run["params"]["source_currency"] == "SEK"
    assert run["params"]["report_language"] == "en" and run["params"]["delivery_email"] == "paid-owner@example.com"
    assert main._forecast_source(run) == "SE"


def test_swedish_forecast_import_writes_only_to_the_swedish_source(monkeypatch):
    _enabled(monkeypatch)
    urls = []
    def handler(request):
        urls.append(str(request.url))
        assert request.headers["authorization"] == "Bearer se-test-token"
        return httpx.Response(200, json={"jobId": 9, "status": "OK", "resultFid": 99})
    real_client = httpx.AsyncClient
    monkeypatch.setattr(forecast_import.httpx, "AsyncClient",
                        lambda **kwargs: real_client(transport=httpx.MockTransport(handler), **kwargs))
    assert asyncio.run(forecast_import.import_and_wait(52623,
        [{"varname": "ns", "year": 2026, "value": 30.0}], country="SE")) == 99
    assert urls == ["https://swedish-source.example/rest/estimates/import"]


def test_paid_swedish_run_exports_from_the_swedish_source(monkeypatch):
    _enabled(monkeypatch)
    calls = []
    async def generate(fid, country="FI"):
        calls.append(("generate", fid, country))
    def run(cmd, country="FI"):
        flag = lambda name: cmd[cmd.index(name) + 1]
        calls.append(("export", country, flag("--country-code"), flag("--source-currency"), flag("--company-code-override")))
        payload = exporter.build_payload(_model(), [], report_language="en", country_code="SE",
                                         source_currency="SEK", verify_cashflows=True)
        Path(flag("--output")).write_text(json.dumps(payload), encoding="utf-8")
        return 0, "", ""
    async def inline(function, *args, **kwargs):
        return function(*args, **kwargs)
    monkeypatch.setattr(valuatum.asyncio, "to_thread", inline)
    monkeypatch.setattr(estimate_trigger, "trigger_and_wait", generate)
    monkeypatch.setattr(valuatum, "_run", run)
    from fetchers.company_data import fetch_company_data
    data = asyncio.run(fetch_company_data("52623", {
        "company_name": "Husqvarna Stockholm Aktiebolag", "company_code": "556983-9805",
        "country_code": "SE", "report_language": "en", "source_currency": "SEK"}))
    assert calls == [("generate", 52623, "SE"), ("export", "SE", "SE", "SEK", "556983-9805")]
    assert data["unit"] == "tSEK" and data["actuals"]["years"] == [2024, 2025]


def test_estimate_generating_source_check_has_its_own_tight_limit(monkeypatch):
    _enabled(monkeypatch)
    async def readiness(country, fid, code, generate=False):
        assert generate is True
        return {"eligible": True}
    monkeypatch.setattr(valuatum, "company_readiness", readiness)
    client = TestClient(main.app)
    query = {"country": "SE", "fid": 52623, "code": "556983-9805"}
    statuses = [client.get("/api/public/company-readiness", params=query).status_code for _ in range(11)]
    assert statuses == [200] * 10 + [429]
