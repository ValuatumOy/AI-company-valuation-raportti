import asyncio
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app import main, seed, store
from app.models import ForecastEdit, GenerateForecastIn, Round2In


def _parent(monkeypatch, *, awaiting=False):
    seed.ensure_seeded()
    monkeypatch.setattr(main, "_check_not_paused", lambda: None)
    monkeypatch.setattr(main, "_require_run_access", lambda rid, request: store.get_run(rid))
    monkeypatch.setattr(main, "_start_bg", lambda *args, **kwargs: True)
    pid = store.list_pipelines()[0]["id"]
    rid = store.create_run(pid, {"meta": {"company_name": "Synthetic manual company"}}, True, identifier="42")
    store.upsert_result(rid, {"order": 0, "name": "FAKTAT", "status": "ok",
        "parsed_json": {"forecast": {"years": [2026], "net_sales": [1000], "ebit": [100]}}})
    store.set_run_status(rid, "awaiting_forecast" if awaiting else "ok")
    return rid, SimpleNamespace(state=SimpleNamespace(access_key=None))


@pytest.mark.parametrize("edits", [None, [ForecastEdit(varname="ns", year=2026, value=2)]])
def test_concurrent_forecast_submits_claim_only_one_run_after_preflight(monkeypatch, edits):
    rid, request = _parent(monkeypatch, awaiting=True)
    imports, starts = [], []
    monkeypatch.setattr(main, "_start_bg", lambda *args, **kwargs: starts.append((args, kwargs)) or True)

    async def scenario():
        gate = asyncio.Event()
        checks = 0

        async def source(run):
            nonlocal checks
            checks += 1
            if checks == 2:
                gate.set()
            await gate.wait()

        async def imported(*args, **kwargs):
            imports.append((args, kwargs))
            await asyncio.sleep(0)
            return 99

        monkeypatch.setattr(main, "_require_reusable_source", source)
        monkeypatch.setattr(main.forecast_import, "import_and_wait", imported)
        body = GenerateForecastIn(forecast_edits=edits)
        return await asyncio.gather(
            main.generate_forecast(rid, body, request),
            main.generate_forecast(rid, body, request),
            return_exceptions=True,
        )

    results = asyncio.run(scenario())
    assert sum(isinstance(result, dict) for result in results) == 1
    failures = [result for result in results if isinstance(result, HTTPException)]
    assert len(failures) == 1 and failures[0].status_code == 409
    assert len(starts) == 1
    assert len(imports) == (1 if edits else 0)


def test_import_failure_releases_claim_without_rebinding_snapshot(monkeypatch):
    rid, request = _parent(monkeypatch, awaiting=True)
    before = store.get_run(rid)

    async def source(run):
        await asyncio.sleep(0)

    async def imported(*args, **kwargs):
        raise RuntimeError("temporary source failure")

    monkeypatch.setattr(main, "_require_reusable_source", source)
    monkeypatch.setattr(main.forecast_import, "import_and_wait", imported)
    with pytest.raises(RuntimeError, match="temporary source failure"):
        asyncio.run(main.generate_forecast(rid, GenerateForecastIn(forecast_edits=[
            ForecastEdit(varname="ns", year=2026, value=2)]), request))
    after = store.get_run(rid)
    assert after["status"] == "awaiting_forecast"
    assert after["identifier"] == before["identifier"]
    assert after["results"] == before["results"]


def test_cancelled_import_releases_claim(monkeypatch):
    rid, request = _parent(monkeypatch, awaiting=True)

    async def scenario():
        importing = asyncio.Event()

        async def source(run):
            return None

        async def imported(*args, **kwargs):
            importing.set()
            await asyncio.Future()

        monkeypatch.setattr(main, "_require_reusable_source", source)
        monkeypatch.setattr(main.forecast_import, "import_and_wait", imported)
        task = asyncio.create_task(main.generate_forecast(rid, GenerateForecastIn(forecast_edits=[
            ForecastEdit(varname="ns", year=2026, value=2)]), request))
        await importing.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(scenario())
    assert store.get_run(rid)["status"] == "awaiting_forecast"


@pytest.mark.parametrize("results", [[], [{"order": 0, "status": "error", "parsed_json": {}}],
                                      [{"order": 0, "status": "validation_failed", "parsed_json": {}}],
                                      [{"order": 0, "status": "ok", "parsed_json": {}}]])
def test_finnish_fid_requires_successful_reusable_snapshot(results):
    run = {"identifier": "42", "params": {}, "results": results}
    with pytest.raises(HTTPException) as exc:
        asyncio.run(main._require_reusable_source(run))
    assert exc.value.status_code == 422


@pytest.mark.parametrize("run", [{"identifier": None, "params": {}, "results": []},
                                 {"identifier": "42", "params": {"country_code": "DK"}, "results": []}])
def test_missing_snapshot_guard_preserves_manual_and_danish_flow(run):
    assert asyncio.run(main._require_reusable_source(run)) is None


def test_finnish_fid_cannot_evade_source_check_by_dropping_source_metadata():
    run = {"identifier": "42", "params": {}, "input_data": None,
           "results": [{"order": 0, "status": "ok", "parsed_json": {
               "actuals": {"years": [2025]}, "forecast": {"years": [2026]}}}]}
    with pytest.raises(HTTPException) as exc:
        asyncio.run(main._require_reusable_source(run))
    assert exc.value.status_code == 422
    run["input_data"] = {"meta": {"company_name": "Explicit manual company"}}
    assert asyncio.run(main._require_reusable_source(run)) is None


@pytest.mark.parametrize("edits", [None, [ForecastEdit(varname="ns", year=2026, value=2)]])
def test_one_process_reserves_family_until_refinement_clone_exists(monkeypatch, edits):
    rid, request = _parent(monkeypatch)
    monkeypatch.setenv("ROUND2_MAX_PER_RUN", "1")
    imports = []

    async def scenario():
        entered, release = asyncio.Event(), asyncio.Event()

        async def source(run):
            if not edits:
                entered.set()
                await release.wait()

        async def imported(*args, **kwargs):
            imports.append((args, kwargs))
            entered.set()
            await release.wait()
            return 99

        monkeypatch.setattr(main, "_require_reusable_source", source)
        monkeypatch.setattr(main.forecast_import, "import_and_wait", imported)
        body = Round2In(forecast_edits=edits)
        first = asyncio.create_task(main.round2_run(rid, body, request))
        await entered.wait()
        with pytest.raises(HTTPException) as exc:
            await main.round2_run(rid, body, request)
        assert exc.value.status_code == 409
        release.set()
        result = await first
        with pytest.raises(HTTPException) as exc:
            await main.round2_run(rid, body, request)
        assert exc.value.status_code == 429
        return result

    result = asyncio.run(scenario())
    assert result["parent_run_id"] == rid
    assert store.refinement_count(rid) == 1
    assert len(imports) == (1 if edits else 0)
    assert rid not in main._REFINEMENT_IN_PROGRESS


def test_refinement_cap_is_checked_after_awaited_source_validation(monkeypatch):
    rid, request = _parent(monkeypatch)
    monkeypatch.setenv("ROUND2_MAX_PER_RUN", "1")

    async def source(run):
        await asyncio.sleep(0)
        store.clone_run(rid)

    monkeypatch.setattr(main, "_require_reusable_source", source)
    with pytest.raises(HTTPException) as exc:
        asyncio.run(main.round2_run(rid, Round2In(), request))
    assert exc.value.status_code == 429
    assert store.refinement_count(rid) == 1
    assert rid not in main._REFINEMENT_IN_PROGRESS
