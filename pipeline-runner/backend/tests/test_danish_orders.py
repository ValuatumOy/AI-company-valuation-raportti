import asyncio
import copy
import json

import httpx
import pytest
from starlette.testclient import TestClient

from app import main, seed, store, valuatum, forecast_interpret, email_delivery, forecast_import
from valuatum_kit import config, export_modeldata_json as exporter


def _enabled(monkeypatch):
    for key, value in {
        "VALUATUM_DK_API_BASE_URL": "https://danish-source.example/rest",
        "VALUATUM_DK_TOKEN": "dk-test-token",
        "VALUATUM_DK_ORDERS_ENABLED": "1",
        "VALUATUM_DK_FORECASTS_VERIFIED": "1",
        "VALUATUM_DK_SOURCE_UNIT": "millions",
        "INTERNATIONAL_CLIENT_SITE_URL": "https://company-valuation.example",
    }.items():
        monkeypatch.setenv(key, value)
    monkeypatch.setattr(main, "STRIPE_SECRET_KEY", "test-only")
    monkeypatch.setattr(main, "_APP_TOKEN", "admin-only")
    main._RATE_HITS.clear()


def _candidate():
    return {"fid": 349862, "company_name": "Novo Nordisk A/S", "company_code": "24256790K", "country_code": "DK"}


def _model(currency="DKK"):
    data = {"2025": {"ns": 100, "ebit": 10}}
    for year in range(2026, 2036):
        data[str(year)] = {
            "ns": 110, "ebit": 11, "wacc": .10, "operating_cash_flow": 10,
            "gross_cap_expenditure_nega": 2, "free_operating_cash_flow": 8,
            "free_cash_flow_to_firm": 8, "disc_fcff": 7, "cum_disc_fcff": 1000,
            "ib_debt_nega_prev_year": -50, "cash_prev_year": 20,
            "value_of_equity_fcff": 970, "noplat": 10, "cost_of_cap_abs": -8,
            "eva": 2, "disc_eva": 1.8, "cum_disc_eva": 100,
            "prol_cap_invested": 900, "value_of_equity_eva": 970,
        }
    return {"companyName": "Novo Nordisk A/S", "companyCode": "24256790K",
            "currency": currency, "currentYear": 2026, "dataMap": data}


def test_source_isolation_and_identifier_contract(monkeypatch):
    monkeypatch.setenv("VALUATUM_API_BASE_URL", "https://finnish.example/rest")
    monkeypatch.delenv("VALUATUM_DK_API_BASE_URL", raising=False)
    assert config.api_base_url() == "https://finnish.example/rest"
    with pytest.raises(ValueError):
        config.api_base_url("DK")
    monkeypatch.setenv("VALUATUM_DK_API_BASE_URL", "https://upcloudtest.valuatum.com/rest")
    with pytest.raises(ValueError):
        config.api_base_url("DK")
    assert valuatum._normalize_query("13590400", "DK") == ("code", "13590400")
    assert valuatum._normalize_query("DK13590400", "DK") == ("code", "13590400")
    assert valuatum._normalize_query("13590400K", "DK")[0] == "name"
    assert valuatum._normalize_query("16123988", "FI") == ("code", "1612398-8")


def test_unconfigured_danish_public_routes_do_not_charge(monkeypatch):
    monkeypatch.delenv("VALUATUM_DK_API_BASE_URL", raising=False)
    monkeypatch.setattr(main, "_APP_TOKEN", "admin-only")
    client = TestClient(main.app)
    assert client.get("/api/public/capabilities").json()["countries"]["DK"]["enabled"] is False
    assert client.get("/api/public/company-search?q=JYSK&country=DK").status_code == 503
    assert client.get("/api/public/company-search?q=JYSK&country=US").status_code == 422
    body = {"business_id": "13590400", "company_name": "JYSK A/S", "email": "owner@example.com",
            "country": "DK", "stripe_session_id": "cs_not_paid"}
    assert client.post("/api/public/checkout-generate", json=body).status_code == 503


def test_public_capabilities_exposes_the_configured_extra_review_price(monkeypatch):
    monkeypatch.setattr(main, "_APP_TOKEN", "admin-only")
    monkeypatch.setattr(main, "EXTRA_ROUND_PRICE_CENTS", 750)
    assert TestClient(main.app).get("/api/public/capabilities").json()["extra_review_price_cents"] == 750


@pytest.mark.parametrize("currency", ["DKK", "EUR"])
def test_readiness_uses_readonly_source_and_reconciles_signs(monkeypatch, currency):
    _enabled(monkeypatch)
    source = _model(currency)
    source["dataMap"]["20261"] = {"ns": 999999}
    source["dataMap"]["0"] = {"ns": 999999}
    for row in source["dataMap"].values():
        row["cr_cf_gross_capex_nega"] = 999  # Statement cashflow differs from the DCF capex source.
    original = copy.deepcopy(source)
    async def search(query, country="FI"):
        assert query == "24256790" and country == "DK"
        return [_candidate()]
    async def data(fids, poses, country="FI"):
        assert fids == [349862] and country == "DK"
        return {"349862": source}
    monkeypatch.setattr(valuatum, "search_company", search)
    monkeypatch.setattr(valuatum, "modeldata", data)
    readiness = asyncio.run(valuatum.danish_company_readiness(349862, "24256790"))
    assert readiness["eligible"], readiness
    assert readiness["currency"] == currency and readiness["unit"] == "t" + currency
    assert readiness["company_code"] == "24256790K"
    assert readiness["actual_year"] == 2025 and len(readiness["forecast_years"]) == 10
    assert source == original
    payload = exporter.build_payload(source, country_code="DK", report_language="en", verify_cashflows=True)
    assert payload["valuation_engine"]["dcf"]["gross_capex"] == [-2000] * 10
    assert payload["meta"]["business_id"] == "24256790"
    source["dataMap"]["2026"]["free_operating_cash_flow"] = 999
    with pytest.raises(ValueError, match="do not reconcile"):
        exporter.build_payload(source, country_code="DK", verify_cashflows=True)


def test_incomplete_forecasts_never_become_eligible(monkeypatch):
    _enabled(monkeypatch)
    source = _model()
    for values in source["dataMap"].values():
        values.pop("ebit", None)
    async def search(*args, **kwargs):
        return [_candidate()]
    async def data(*args, **kwargs):
        return {"349862": source}
    monkeypatch.setattr(valuatum, "search_company", search)
    monkeypatch.setattr(valuatum, "modeldata", data)
    readiness = asyncio.run(valuatum.danish_company_readiness(349862, "24256790"))
    assert readiness["eligible"] is False
    assert "forecast ebit is missing" in readiness["reason"]
    source["currency"] = "USD"
    assert asyncio.run(valuatum.danish_company_readiness(349862, "24256790"))["eligible"] is False


def test_paid_danish_identity_idempotency_and_source_binding(monkeypatch):
    _enabled(monkeypatch)
    seed.ensure_seeded()
    starts = []
    monkeypatch.setattr(main, "_start_bg", lambda *args, **kwargs: starts.append((args, kwargs)) or True)
    metadata = {"product": main.VALUATION_PRODUCT_TAG, "country": "DK", "reportLanguage": "en",
                "reportCurrency": "DKK", "companyCode": "24256790K", "businessId": "24256790",
                "fid": "349862", "customerEmail": "paid-owner@example.com", "forecast": "true"}
    async def paid(*args, **kwargs):
        return {"payment_status": "paid", "metadata": metadata}
    async def search(query, country="FI"):
        assert query == "24256790" and country == "DK"
        return [_candidate()]
    async def readiness(*args, **kwargs):
        return {"eligible": True, "currency": "DKK", "reason": None}
    monkeypatch.setattr(main, "_stripe_get_checkout_session", paid)
    monkeypatch.setattr(valuatum, "search_company", search)
    monkeypatch.setattr(valuatum, "danish_company_readiness", readiness)
    body = {"business_id": "24256790", "fid": 349862, "company_name": "Forged name ignored",
            "email": "forged@example.com", "country": "DK", "report_language": "en",
            "stripe_session_id": "cs_danish_automatic_test"}
    client = TestClient(main.app)
    for key, value in [("business_id", "13590400"), ("fid", 999), ("country", "FI"), ("report_language", "fi")]:
        assert client.post("/api/public/checkout-generate", json={**body, key: value}).status_code == 409
    first = client.post("/api/public/checkout-generate", json=body)
    assert first.status_code == 200, first.text
    assert client.post("/api/public/checkout-generate", json=body).json() == first.json()
    assert len(starts) == 1 and starts[0][1]["only"] == 0
    run = store.get_run(first.json()["run_id"])
    assert run["params"]["country_code"] == "DK" and run["params"]["source_currency"] == "DKK"
    assert run["params"]["delivery_email"] == "paid-owner@example.com"
    assert run["params"]["company_name"] == "Novo Nordisk A/S"
    assert run["params"]["company_code"] == "24256790K"
    assert email_delivery._report_link(run["id"], run).startswith("https://company-valuation.example/report?")
    store.set_run_status(run["id"], "error")
    assert client.post("/api/public/checkout-generate", json=body).json() == first.json()
    assert len(starts) == 1  # Duplicate webhook/page reload never starts another paid Danish run.


def test_forecast_prompt_keeps_source_currency_and_million_scale():
    prompt = forecast_interpret._prompt("Increase revenue by 10%", {"years": [2026], "net_sales": [5300]},
                                        currency="DKK", language="en")
    assert "MILLIONS OF DKK" in prompt and '"current_value_millions": 5.3' in prompt
    assert "EURO" not in prompt and "Do not convert currencies" in prompt


def test_source_cache_is_partitioned_even_when_fids_and_queries_collide(monkeypatch):
    _enabled(monkeypatch)
    monkeypatch.setenv("VALUATUM_API_BASE_URL", "https://finnish-source.example/rest")
    monkeypatch.setenv("VALUATUM_TOKEN", "fi-test-token")
    calls = []
    def handler(request):
        calls.append(request.url.host)
        is_dk = request.url.host == "danish-source.example"
        assert request.headers["authorization"] == "Bearer " + ("dk-test-token" if is_dk else "fi-test-token")
        return httpx.Response(200, json=[{"companyCode": "13590400", "companyName": "DK" if is_dk else "FI"}])
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    monkeypatch.setattr(valuatum, "_CLIENT", client)
    monkeypatch.setattr(valuatum, "_ROWS_CACHE", {})
    async def run():
        dk = await valuatum._company_rows("code", "13590400", "DK")
        fi = await valuatum._company_rows("code", "13590400", "FI")
        assert (await valuatum._company_rows("code", "13590400", "DK")) == dk
        assert dk != fi
        await client.aclose()
    asyncio.run(run())
    assert calls == ["danish-source.example", "finnish-source.example"]


def test_danish_forecast_import_never_uses_finnish_source_or_converts_currency(monkeypatch):
    _enabled(monkeypatch)
    monkeypatch.setenv("VALUATUM_API_BASE_URL", "https://finnish-source.example/rest")
    requests = []
    def handler(request):
        requests.append(str(request.url))
        assert request.headers["authorization"] == "Bearer dk-test-token"
        assert json.loads(request.content) == {"baseFid": 349862,
            "values": [{"varname": "ns", "year": 2026, "value": 309064.5}]}
        return httpx.Response(200, json={"jobId": 77, "status": "OK", "resultFid": 777})
    real_client = httpx.AsyncClient
    monkeypatch.setattr(forecast_import.httpx, "AsyncClient",
                        lambda **kwargs: real_client(transport=httpx.MockTransport(handler), **kwargs))
    assert asyncio.run(forecast_import.import_and_wait(349862,
        [{"varname": "ns", "year": 2026, "value": 309064.5}], country="DK")) == 777
    assert requests == ["https://danish-source.example/rest/estimates/import"]


def test_edited_danish_run_retains_country_currency_and_exact_source_scope(monkeypatch):
    _enabled(monkeypatch)
    seed.ensure_seeded()
    monkeypatch.setattr(main, "_start_bg", lambda *args, **kwargs: True)
    rid = store.create_run(store.list_pipelines()[0]["id"], None, True, identifier="349862",
        params={"country_code": "DK", "source_currency": "DKK", "report_language": "en",
                "company_name": "Novo Nordisk A/S", "company_code": "24256790K", "forecast_mode": True})
    store.upsert_result(rid, {"order": 0, "name": "FAKTAT", "status": "ok",
        "parsed_json": {"forecast": {"years": [2026], "net_sales": [309064000], "ebit": [100000000]}}})
    store.set_run_status(rid, "awaiting_forecast")
    calls = []
    async def imported(fid, edits, **kwargs):
        calls.append((fid, edits, kwargs))
        return 777
    monkeypatch.setattr(forecast_import, "import_and_wait", imported)
    response = TestClient(main.app).post(f"/api/runs/{rid}/generate-forecast",
        headers={"Authorization": "Bearer admin-only"},
        json={"forecast_edits": [{"varname": "ns", "year": 2026, "value": 309064.5}]})
    assert response.status_code == 200, response.text
    assert calls == [(349862, [{"varname": "ns", "year": 2026, "value": 309064.5}], {"country": "DK"})]
    run = store.get_run(rid)
    assert run["identifier"] == "777"
    assert run["params"]["country_code"] == "DK" and run["params"]["source_currency"] == "DKK"
    assert run["params"]["company_code"] == "24256790K" and run["params"]["skip_estimate_generation"]
    assert "tDKK" in run["params"]["forecast_changes"] and "tEUR" not in run["params"]["forecast_changes"]


def test_danish_delivery_and_forecast_emails_are_english_and_use_international_links(monkeypatch, tmp_path):
    _enabled(monkeypatch)
    monkeypatch.setenv("REPORT_EMAIL_ENABLED", "1")
    monkeypatch.setenv("AWS_REGION", "eu-west-1")
    monkeypatch.setenv("REPORT_EMAIL_FROM", "reports@example.com")
    run = {"access_key": "exp_test_only", "params": {"country_code": "DK", "source_currency": "DKK",
           "report_language": "en", "company_name": "Novo Nordisk A/S", "delivery_email": "owner@example.com"}}
    monkeypatch.setattr(store, "get_run", lambda rid: run)
    monkeypatch.setattr(store, "final_report_json", lambda rid: {"meta": {"company_name": "Novo Nordisk A/S"}})
    pdf = tmp_path / "report.pdf"
    pdf.write_bytes(b"%PDF-1.4 test-only")
    monkeypatch.setattr(email_delivery.report, "generate_pdf", lambda *args: str(pdf))
    messages = []
    async def dispatch(message, **kwargs):
        messages.append(message)
        return {"sent": True}
    monkeypatch.setattr(email_delivery, "_dispatch", dispatch)
    asyncio.run(email_delivery.send_report_ready("run_test"))
    asyncio.run(email_delivery.send_forecast_ready("run_test"))
    asyncio.run(email_delivery.send_forecast_import_failed("run_test", [{"varname": "ns", "year": 2030}]))
    assert [message["Subject"] for message in messages] == [
        "Valuation report ready: Novo Nordisk A/S", "Review your valuation forecasts: Novo Nordisk A/S",
        "Forecast changes could not be applied: Novo Nordisk A/S"]
    for message in messages:
        text = message.get_body(preferencelist=("plain",)).get_content()
        assert "https://company-valuation.example/report?key=exp_test_only&rid=run_test" in text
        assert "arvonmääritys" not in text and "/raportti" not in text
