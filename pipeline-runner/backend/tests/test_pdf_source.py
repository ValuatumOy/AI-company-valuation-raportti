"""PDF-service source and Norwegian locale: read-only source, NOK end to end."""
import asyncio

import pytest

from app import estimate_trigger, forecast_import, report_context, report_i18n
from valuatum_kit import config, export_modeldata_json as export


def test_pdf_source_needs_its_own_production_base_and_token(monkeypatch):
    monkeypatch.delenv("VALUATUM_PDF_API_BASE_URL", raising=False)
    monkeypatch.setenv("VALUATUM_TOKEN", "finnish-token")
    with pytest.raises(ValueError, match="PDF service production source is not configured"):
        config.api_token("PDF")
    monkeypatch.setenv("VALUATUM_PDF_API_BASE_URL", "https://pdftest.example.com/rest")
    with pytest.raises(ValueError, match="not configured"):
        config.api_base_url("PDF")
    monkeypatch.setenv("VALUATUM_PDF_API_BASE_URL", "https://pdf.example.com/rest/")
    assert config.api_base_url("PDF") == "https://pdf.example.com/rest"
    with pytest.raises(ValueError, match="token is not configured"):
        config.api_token("PDF")
    monkeypatch.setenv("VALUATUM_PDF_TOKEN", "pdf-token")
    assert config.api_token("PDF") == "pdf-token"


def test_pdf_models_never_get_estimates_or_imports(monkeypatch):
    monkeypatch.setenv("VALUATUM_PDF_API_BASE_URL", "https://pdf.example.com/rest")
    monkeypatch.setenv("VALUATUM_PDF_TOKEN", "pdf-token")
    with pytest.raises(estimate_trigger.EstimateGenerationError, match="read-only"):
        asyncio.run(estimate_trigger.trigger_and_wait(1, country="PDF"))
    with pytest.raises(forecast_import.ForecastImportError, match="read-only"):
        asyncio.run(forecast_import.import_and_wait(1, [{"varName": "ns", "year": 2026, "value": 1}],
                                                    country="PDF"))


def test_norwegian_report_context_keeps_nok():
    ctx = report_context.resolve_context(None, {"country_code": "NO", "source_currency": "NOK",
                                                 "report_language": "en"})
    assert ctx == {"report_language": "en", "country_code": "NO", "currency": "NOK", "unit": "tNOK"}
    with pytest.raises(ValueError, match="Foreign reports require verified source_currency"):
        report_context.resolve_context(None, {"country_code": "NO", "report_language": "en"})


def test_nok_labels_are_checked_against_the_report_currency():
    report = {"unit": "tNOK", "meta": {"country_code": "NO", "currency": "NOK", "unit": "tNOK",
                                       "report_language": "en"},
              "summary": "Net sales were 180 052 tNOK."}
    assert report_i18n.locale_from(report).unit == "tNOK"
    report["summary"] = "Net sales were 180 052 tEUR."
    with pytest.raises(ValueError):
        report_i18n.locale_from(report)


def test_norwegian_model_exports_in_nok_with_source_verification():
    model = {"followedModelId": 1, "companyName": "2TAL AS", "companyCode": "955497643",
             "currency": "NOK", "currentYear": 2025,
             "dataMap": {"2024": {"ns": 1.0}, "2025": {}, "Y+1": {"ns": 9}}}
    payload = export.build_payload(model, country_code="NO", report_language="en", source_currency="NOK")
    assert payload["unit"] == "tNOK"
    assert payload["meta"]["business_id"] == "955497643"
    assert payload["actuals"]["years"] == [2024]
    with pytest.raises(ValueError, match="Complete source cashflow reconciliation is missing"):
        export.build_payload(model, country_code="NO", report_language="en", source_currency="NOK",
                             verify_cashflows=True)


def test_norwegian_run_refuses_forecast_edits_through_the_endpoint(monkeypatch):
    from fastapi.testclient import TestClient
    from app import main, seed, store
    monkeypatch.setattr(main, "_APP_TOKEN", "admin-only")
    seed.ensure_seeded()
    rid = store.create_run(store.list_pipelines()[0]["id"], None, True, identifier="123456",
        params={"country_code": "NO", "source_currency": "NOK", "report_language": "en",
                "company_name": "2TAL AS", "forecast_mode": True})
    store.upsert_result(rid, {"order": 0, "name": "FAKTAT", "status": "ok",
        "parsed_json": {"forecast": {"years": [2026], "net_sales": [180000000], "ebit": [8000000]}}})
    store.set_run_status(rid, "awaiting_forecast")
    calls = []
    async def imported(*args, **kwargs):
        calls.append((args, kwargs))
        return 777
    monkeypatch.setattr(forecast_import, "import_and_wait", imported)
    client = TestClient(main.app)
    edit = {"forecast_edits": [{"varname": "ns", "year": 2026, "value": 190.0}]}
    response = client.post(f"/api/runs/{rid}/generate-forecast", headers={"Authorization": "Bearer admin-only"}, json=edit)
    assert response.status_code == 409 and "not available for this report's source" in response.text
    assert store.get_run(rid)["status"] == "awaiting_forecast"
    response = client.post(f"/api/runs/{rid}/round2", headers={"Authorization": "Bearer admin-only"}, json=edit)
    assert response.status_code == 409 and "not available for this report's source" in response.text
    assert calls == []
