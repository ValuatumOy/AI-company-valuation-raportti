"""Round-1 forecast pause/edit/continue flow (model B)."""
import asyncio

import httpx
import pytest


def _run(coro):
    return asyncio.run(coro)


class ASGIClient:
    def __init__(self, app):
        self.app = app

    def post(self, path, **kwargs):
        async def send():
            transport = httpx.ASGITransport(app=self.app)
            async with httpx.AsyncClient(
                transport=transport, base_url="http://testserver"
            ) as client:
                return await client.post(path, **kwargs)

        return _run(send())


def _seed_run(monkeypatch, *, access_key=None, with_forecast=True):
    from app import main, seed, store

    seed.ensure_seeded()
    monkeypatch.setattr(main, "_APP_TOKEN", "")
    monkeypatch.setattr(main, "_check_not_paused", lambda: None)
    pid = store.list_pipelines()[0]["id"]
    rid = store.create_run(
        pid,
        {"meta": {"company_name": "Round 1 Oy"}},
        True,
        identifier="42",
        params={"company_name": "Round 1 Oy"},
        access_key=access_key,
    )
    if with_forecast:
        store.upsert_result(rid, {
            "order": 0,
            "name": "FAKTAT",
            "status": "ok",
            "parsed_json": {
                "forecast": {
                    "years": [2026, 2027],
                    "net_sales": [5000, 5500],
                    "ebit": [200, 300],
                }
            },
        })
    return ASGIClient(main.app), main, store, rid


def test_stage0_drive_finishes_awaiting_without_email(monkeypatch):
    c, main, store, rid = _seed_run(monkeypatch, with_forecast=False)
    del c

    async def fake_run_stages(run, stages, **kwargs):
        assert kwargs == {"only": 0, "from_order": None}
        store.upsert_result(rid, {
            "order": 0,
            "name": "FAKTAT",
            "status": "ok",
            "parsed_json": {"forecast": {"years": [2026], "net_sales": [5000], "ebit": [200]}},
        })
        store.set_run_status(rid, "ok")
        yield {"event": "done", "status": "ok"}

    async def forbidden_email(*args, **kwargs):
        pytest.fail("stage-0-only-ajo ei saa lähettää raporttisähköpostia")

    monkeypatch.setattr(main.runner, "run_stages", fake_run_stages)
    monkeypatch.setattr(main.email_delivery, "send_report_ready", forbidden_email)
    _run(main._drive_run(rid, only=0, completion_status="awaiting_forecast"))

    assert store.get_run(rid)["status"] == "awaiting_forecast"
    assert rid not in main._RUN_TASKS


def test_fetch_forecast_starts_only_stage0(monkeypatch):
    c, main, _, rid = _seed_run(monkeypatch, with_forecast=False)
    started = {}

    def fake_start(run_id, **kwargs):
        started.update(run_id=run_id, kwargs=kwargs)
        return True

    monkeypatch.setattr(main, "_start_bg", fake_start)
    response = c.post(f"/api/runs/{rid}/fetch-forecast")

    assert response.status_code == 200
    assert response.json()["status"] == "running"
    assert started == {
        "run_id": rid,
        "kwargs": {"only": 0, "completion_status": "awaiting_forecast"},
    }


def test_fetch_forecast_is_idempotent_when_already_awaiting(monkeypatch):
    c, main, store, rid = _seed_run(monkeypatch)
    store.set_run_status(rid, "awaiting_forecast")
    monkeypatch.setattr(
        main,
        "_start_bg",
        lambda *args, **kwargs: pytest.fail("valmista stage 0:aa ei haeta uudelleen"),
    )

    response = c.post(f"/api/runs/{rid}/fetch-forecast")
    assert response.status_code == 200
    assert response.json()["started"] is False
    assert response.json()["forecast"]["net_sales"] == [5000, 5500]


def test_generate_without_edits_continues_from_stage1_on_same_run(monkeypatch):
    c, main, store, rid = _seed_run(monkeypatch)
    store.set_run_status(rid, "awaiting_forecast")
    started = {}
    monkeypatch.setattr(
        main,
        "_start_bg",
        lambda run_id, **kwargs: started.update(run_id=run_id, kwargs=kwargs) or True,
    )
    monkeypatch.setattr(
        main.forecast_import,
        "import_and_wait",
        lambda *args, **kwargs: pytest.fail("hyväksyntä ilman muutoksia ei importoi"),
    )

    response = c.post(f"/api/runs/{rid}/generate-forecast", json={})

    assert response.status_code == 200
    assert response.json() == {"run_id": rid, "started": True, "forecast_edited": False}
    assert started == {"run_id": rid, "kwargs": {"from_order": 1}}
    run = store.get_run(rid)
    assert run["id"] == rid
    assert run["identifier"] == "42"
    assert run["status"] == "running"
    assert run["parent_run_id"] is None


def test_generate_with_edits_rebinds_same_run_and_restarts_stage0(monkeypatch):
    c, main, store, rid = _seed_run(monkeypatch)
    store.set_run_status(rid, "awaiting_forecast")

    async def fake_import(base_fid, values):
        assert base_fid == 42
        assert values == [{"varname": "ns", "year": 2027, "value": 6.2}]
        assert store.get_run(rid)["status"] == "importing_forecast"
        return 4243

    started = {}
    monkeypatch.setattr(main.forecast_import, "import_and_wait", fake_import)
    monkeypatch.setattr(
        main,
        "_start_bg",
        lambda run_id, **kwargs: started.update(run_id=run_id, kwargs=kwargs) or True,
    )

    response = c.post(f"/api/runs/{rid}/generate-forecast", json={
        "forecast_edits": [{"varname": "ns", "year": 2027, "value": 6.2}],
    })

    assert response.status_code == 200
    assert response.json()["run_id"] == rid
    assert response.json()["identifier"] == "4243"
    assert started == {"run_id": rid, "kwargs": {"from_order": 0}}
    run = store.get_run(rid)
    assert run["identifier"] == "4243"
    assert run["input_data"] is None
    assert run["parent_run_id"] is None
    assert run["params"]["skip_estimate_generation"] is True
    assert run["params"]["forecast_edits"] == [
        {"varname": "ns", "year": 2027, "value": 6.2}
    ]
    assert "Liikevaihto 2027" in run["params"]["forecast_changes"]
    assert "6 200" in run["params"]["forecast_changes"]


def test_full_grid_imports_every_cell_but_summarises_only_the_changed_one(monkeypatch):
    """The client submits the whole forecast grid (ValuBuild's import drops years
    after a gap), so every cell must reach ValuBuild — while the writer's
    forecast_changes block still lists only what the user actually moved."""
    c, main, store, rid = _seed_run(monkeypatch)  # baseline ns 5000/5500, ebit 200/300
    store.set_run_status(rid, "awaiting_forecast")

    sent = {}

    async def fake_import(base_fid, values):
        sent["values"] = values
        return 4243

    monkeypatch.setattr(main.forecast_import, "import_and_wait", fake_import)
    monkeypatch.setattr(main, "_start_bg", lambda run_id, **kwargs: True)

    grid = [
        {"varname": "ns", "year": 2026, "value": 5.0},    # unchanged
        {"varname": "ebit", "year": 2026, "value": 0.2},  # unchanged
        {"varname": "ns", "year": 2027, "value": 6.2},    # changed
        {"varname": "ebit", "year": 2027, "value": 0.3},  # unchanged
    ]
    response = c.post(f"/api/runs/{rid}/generate-forecast", json={"forecast_edits": grid})

    assert response.status_code == 200
    # Nothing is dropped on the way to ValuBuild: the import stays contiguous.
    assert sent["values"] == grid
    params = store.get_run(rid)["params"]
    assert params["forecast_edits"] == grid
    # ...but the baseline cells never reach the writer as "changes".
    assert params["forecast_changes"] == "- Liikevaihto 2027: 5 500 → 6 200 tEUR"


def test_generate_import_failure_restores_awaiting_state(monkeypatch):
    c, main, store, rid = _seed_run(monkeypatch)
    store.set_run_status(rid, "awaiting_forecast")

    async def failing_import(base_fid, values):
        raise main.forecast_import.ForecastImportError("ValuBuild kaatui")

    monkeypatch.setattr(main.forecast_import, "import_and_wait", failing_import)
    monkeypatch.setattr(
        main,
        "_start_bg",
        lambda *args, **kwargs: pytest.fail("import-virheessä kirjoitus ei käynnisty"),
    )

    response = c.post(f"/api/runs/{rid}/generate-forecast", json={
        "forecast_edits": [{"varname": "ebit", "year": 2027, "value": 0.4}],
    })
    assert response.status_code == 502
    assert store.get_run(rid)["status"] == "awaiting_forecast"
    assert store.get_run(rid)["identifier"] == "42"


def test_stale_import_returns_to_retryable_awaiting_state(monkeypatch):
    _, _, store, rid = _seed_run(monkeypatch)
    store.set_run_status(rid, "importing_forecast")

    store.reset_stale_runs()

    assert store.get_run(rid)["status"] == "awaiting_forecast"


def test_generate_rejects_second_or_post_report_attempt(monkeypatch):
    c, main, _, rid = _seed_run(monkeypatch)
    monkeypatch.setattr(
        main.forecast_import,
        "import_and_wait",
        lambda *args, **kwargs: pytest.fail("väärässä tilassa ei importoida"),
    )

    response = c.post(f"/api/runs/{rid}/generate-forecast", json={})
    assert response.status_code == 409


def test_expert_forecast_mode_consumes_one_credit_and_starts_stage0(monkeypatch):
    from app import main, seed, store

    seed.ensure_seeded()
    monkeypatch.setattr(main, "_APP_TOKEN", "admin-token")
    monkeypatch.setattr(main, "_check_not_paused", lambda: None)
    key = store.create_access_key("round1 forecast", generations_limit=2)["key"]
    started = {}
    monkeypatch.setattr(
        main,
        "_start_bg",
        lambda run_id, **kwargs: started.update(run_id=run_id, kwargs=kwargs) or True,
    )
    client = ASGIClient(main.app)

    response = client.post(
        "/api/expert/generate",
        headers={"Authorization": f"Bearer {key}"},
        json={"fid": 42, "company_name": "Round 1 Oy", "mode": "forecast"},
    )

    assert response.status_code == 200
    rid = response.json()["run_id"]
    assert response.json()["mode"] == "forecast"
    assert store.get_access_key(key)["generations_used"] == 1
    assert store.get_run(rid)["access_key"] == key
    assert started == {
        "run_id": rid,
        "kwargs": {"only": 0, "completion_status": "awaiting_forecast"},
    }
