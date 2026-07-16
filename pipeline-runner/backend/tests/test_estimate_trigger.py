import asyncio
import json

import httpx
import pytest

from app import estimate_trigger, runner, valuatum
from fetchers import company_data
from valuatum_kit import config as valuatum_config, fetch_modeldata


def _run(coro):
    return asyncio.run(coro)


def _configure(monkeypatch):
    monkeypatch.setenv("VALUATUM_API_BASE_URL", "https://valu.test/rest/")
    monkeypatch.setenv("VALUATUM_TOKEN", "test-token")
    monkeypatch.setattr(estimate_trigger, "POLL_INTERVAL_SECONDS", 0.0)


def _mock_client(monkeypatch, handler):
    real_client = httpx.AsyncClient
    transport = httpx.MockTransport(handler)
    monkeypatch.setattr(
        estimate_trigger.httpx,
        "AsyncClient",
        lambda **kwargs: real_client(transport=transport, **kwargs),
    )


def test_trigger_waits_until_job_is_ok(monkeypatch):
    _configure(monkeypatch)
    statuses = iter(["RUNNING", "OK"])
    calls = []

    def handler(request):
        calls.append((request.method, str(request.url)))
        assert request.headers["authorization"] == "Bearer test-token"
        if request.method == "POST":
            return httpx.Response(202, json={"jobId": 17, "fid": 42, "status": "PENDING"})
        return httpx.Response(200, json={"jobId": 17, "fid": 42, "status": next(statuses)})

    _mock_client(monkeypatch, handler)
    _run(estimate_trigger.trigger_and_wait(42))

    assert calls == [
        ("POST", "https://valu.test/rest/estimates/generate/42"),
        ("GET", "https://valu.test/rest/estimates/jobs/17"),
        ("GET", "https://valu.test/rest/estimates/jobs/17"),
    ]


def test_trigger_surfaces_job_error_reason(monkeypatch):
    _configure(monkeypatch)

    def handler(request):
        if request.method == "POST":
            return httpx.Response(202, json={"jobId": 18, "status": "PENDING"})
        return httpx.Response(
            200,
            json={"jobId": 18, "status": "ERROR", "errorMessage": "mallin tallennus epäonnistui"},
        )

    _mock_client(monkeypatch, handler)
    with pytest.raises(estimate_trigger.EstimateGenerationError, match="mallin tallennus"):
        _run(estimate_trigger.trigger_and_wait(42))


def test_trigger_times_out(monkeypatch):
    _configure(monkeypatch)
    monkeypatch.setattr(estimate_trigger, "TIMEOUT_SECONDS", 0.0)
    _mock_client(
        monkeypatch,
        lambda request: httpx.Response(202, json={"jobId": 19, "status": "RUNNING"}),
    )

    with pytest.raises(estimate_trigger.EstimateGenerationError, match="aikakatkaistiin"):
        _run(estimate_trigger.trigger_and_wait(42))


def test_trigger_surfaces_connection_error(monkeypatch):
    _configure(monkeypatch)

    def handler(request):
        raise httpx.ConnectError("endpoint alhaalla", request=request)

    _mock_client(monkeypatch, handler)
    with pytest.raises(estimate_trigger.EstimateGenerationError, match="Yhteys ValuBuildin"):
        _run(estimate_trigger.trigger_and_wait(42))


def test_trigger_surfaces_disappeared_job(monkeypatch):
    _configure(monkeypatch)

    def handler(request):
        if request.method == "POST":
            return httpx.Response(202, json={"jobId": 20, "status": "PENDING"})
        return httpx.Response(404, json={"error": "not found"})

    _mock_client(monkeypatch, handler)
    with pytest.raises(estimate_trigger.EstimateGenerationError, match="katosi"):
        _run(estimate_trigger.trigger_and_wait(42))


def test_trigger_rejects_invalid_poll_payload(monkeypatch):
    _configure(monkeypatch)

    def handler(request):
        if request.method == "POST":
            return httpx.Response(202, json={"jobId": 21, "status": "PENDING"})
        return httpx.Response(200, json=[{"jobId": 21, "status": "OK"}])

    _mock_client(monkeypatch, handler)
    with pytest.raises(estimate_trigger.EstimateGenerationError, match="virheellisen job-vastauksen"):
        _run(estimate_trigger.trigger_and_wait(42))


def test_trigger_rejects_mismatched_poll_job_id(monkeypatch):
    _configure(monkeypatch)

    def handler(request):
        if request.method == "POST":
            return httpx.Response(202, json={"jobId": 22, "status": "PENDING"})
        return httpx.Response(200, json={"jobId": 23, "status": "OK"})

    _mock_client(monkeypatch, handler)
    with pytest.raises(estimate_trigger.EstimateGenerationError, match="väärän jobId:n"):
        _run(estimate_trigger.trigger_and_wait(42))


def test_trigger_uses_test_environment_when_url_is_unset(monkeypatch):
    monkeypatch.delenv("VALUATUM_API_BASE_URL", raising=False)
    monkeypatch.setenv("VALUATUM_TOKEN", "test-token")
    calls = []

    def handler(request):
        calls.append(str(request.url))
        return httpx.Response(200, json={"jobId": 24, "status": "OK"})

    _mock_client(monkeypatch, handler)
    _run(estimate_trigger.trigger_and_wait(42))

    assert calls == [
        "https://profindertest.valuatum.com/rest/estimates/generate/42"
    ]


def test_modeldata_uses_estimate_generation_environment(monkeypatch):
    monkeypatch.setenv(
        "VALUATUM_API_BASE_URL", "https://valu.test/rest/"
    )
    assert fetch_modeldata.modeldata_url() == "https://valu.test/rest/modeldata"


def test_modeldata_defaults_to_test_environment(monkeypatch):
    monkeypatch.delenv("VALUATUM_API_BASE_URL", raising=False)
    assert fetch_modeldata.modeldata_url() == (
        valuatum_config.DEFAULT_VALUATUM_API_BASE_URL + "/modeldata"
    )


def _minimal_modeldata():
    return {
        "meta": {"company_name": "Test Oy", "y_tunnus": "1234567-8"},
        "headcount": {},
        "actuals": {},
        "forecast": {},
        "forecast_parameters": {},
        "valuation_engine": {},
        "key_ratios": {},
        "credit_risk": {},
        "peers": [],
        "client_reported_signals": [],
        "flags": [],
    }


async def _collect_export_events():
    return [event async for event in valuatum.export_stream("Test Oy", 42)]


def _inline_export_threads(monkeypatch):
    async def inline(function, *args, **kwargs):
        return function(*args, **kwargs)

    monkeypatch.setattr(valuatum.asyncio, "to_thread", inline)


def test_export_stream_generates_before_modeldata(monkeypatch):
    _inline_export_threads(monkeypatch)
    monkeypatch.setenv("VALUATUM_TOKEN", "tok")
    monkeypatch.setenv("VALUATUM_API_BASE_URL", "https://valu.test/rest")
    monkeypatch.delenv("VALUATUM_MCP_URL", raising=False)
    order = []

    async def fake_trigger(fid):
        order.append(("trigger", fid))

    def fake_run(cmd):
        order.append(("modeldata", int(cmd[cmd.index("--fid") + 1])))
        output = cmd[cmd.index("--output") + 1]
        with open(output, "w", encoding="utf-8") as handle:
            json.dump(_minimal_modeldata(), handle)
        return 0, "", ""

    monkeypatch.setattr(valuatum.estimate_trigger, "trigger_and_wait", fake_trigger)
    monkeypatch.setattr(valuatum, "_run", fake_run)
    monkeypatch.setattr(valuatum, "lookup_company_metadata", lambda **kwargs: _async_value({}))

    events = _run(_collect_export_events())

    assert [event["step"] for event in events] == ["estimates", "fetch", "ready"]
    assert order == [("trigger", 42), ("modeldata", 42)]


async def _async_value(value):
    return value


def test_export_stream_hard_fails_before_modeldata(monkeypatch):
    _inline_export_threads(monkeypatch)
    monkeypatch.setenv("VALUATUM_TOKEN", "tok")
    monkeypatch.setenv("VALUATUM_API_BASE_URL", "https://valu.test/rest")

    async def fail_trigger(fid):
        raise estimate_trigger.EstimateGenerationError("ValuBuild testivirhe")

    monkeypatch.setattr(valuatum.estimate_trigger, "trigger_and_wait", fail_trigger)
    monkeypatch.setattr(
        valuatum,
        "_run",
        lambda cmd: pytest.fail("modeldataa ei saa hakea generointivirheen jälkeen"),
    )

    events = _run(_collect_export_events())

    assert [event["step"] for event in events] == ["estimates", "error"]
    assert "ValuBuild testivirhe" in events[-1]["message"]


def test_export_stream_generates_with_default_api_when_url_is_unset(monkeypatch):
    _inline_export_threads(monkeypatch)
    monkeypatch.setenv("VALUATUM_TOKEN", "tok")
    monkeypatch.delenv("VALUATUM_API_BASE_URL", raising=False)
    monkeypatch.delenv("VALUATUM_MCP_URL", raising=False)
    order = []

    async def fake_trigger(fid):
        order.append(("trigger", fid))

    def fake_run(cmd):
        order.append(("modeldata", int(cmd[cmd.index("--fid") + 1])))
        output = cmd[cmd.index("--output") + 1]
        with open(output, "w", encoding="utf-8") as handle:
            json.dump(_minimal_modeldata(), handle)
        return 0, "", ""

    monkeypatch.setattr(valuatum.estimate_trigger, "trigger_and_wait", fake_trigger)
    monkeypatch.setattr(valuatum, "_run", fake_run)
    monkeypatch.setattr(valuatum, "lookup_company_metadata", lambda **kwargs: _async_value({}))

    events = _run(_collect_export_events())

    assert [event["step"] for event in events] == ["estimates", "fetch", "ready"]
    assert order == [("trigger", 42), ("modeldata", 42)]


def test_fetch_company_data_preserves_ready_warnings(monkeypatch):
    async def fake_export_stream(**kwargs):
        yield {
            "step": "ready",
            "json": {"meta": {"company_name": "Test Oy"}},
            "warnings": ["backfill jäi vajaaksi"],
        }

    monkeypatch.setattr(valuatum, "export_stream", fake_export_stream)
    data = _run(company_data.fetch_company_data("42", {"company_name": "Test Oy"}))
    assert data["fetch_warnings"] == ["backfill jäi vajaaksi"]


def test_stage_zero_generation_failure_does_not_call_llm(monkeypatch):
    async def fail_fetch(identifier, params):
        raise estimate_trigger.EstimateGenerationError("generointi epäonnistui")

    async def forbidden_chat(**kwargs):
        pytest.fail("LLM:ää ei saa kutsua Stage 0 -virheen jälkeen")

    monkeypatch.setattr(runner, "fetch_company_data", fail_fetch)
    monkeypatch.setattr(runner.openrouter, "chat", forbidden_chat)
    stage = {
        "id": "stage-0",
        "order": 0,
        "name": "FAKTAT",
        "model": runner.DATA_FETCHER_MODEL,
        "prompt_template": "",
    }

    result = _run(
        runner._execute_stage(stage, {}, None, "42", {}, rid="test-run")
    )

    assert result["status"] == "error"
    assert "generointi epäonnistui" in result["error_message"]
