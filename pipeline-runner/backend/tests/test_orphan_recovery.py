"""Recovery of runs orphaned by a deploy, restart or crash.

Regression cover for 2026-07-31: a deploy landed while run 8dfd3918 was inside
its writer stage. The container died, the run row was blanket-flipped to
'error', the stage row stayed 'running', nothing was logged and no alert fired —
the customer saw only "missing report sections" and lost a credit.
"""
import asyncio
from datetime import datetime, timedelta, timezone

import pytest


@pytest.fixture(autouse=True)
def _only_this_tests_runs():
    """The sweep looks at every 'running' row in the database, so leftovers from
    a sibling test would be adopted too. Start each case from an empty table."""
    from app import db, seed

    seed.ensure_seeded()  # also creates the schema on a fresh temp database
    db.execute("DELETE FROM stage_results")
    db.execute("DELETE FROM runs")
    yield


def _run(coro):
    return asyncio.run(coro)


def _stale(seconds):
    return (datetime.now(timezone.utc) - timedelta(seconds=seconds)).isoformat()


def _seed(monkeypatch, *, access_key=None, params=None):
    """A run stopped mid-writer: stage 0/1 done, stage 2 frozen at 'running'."""
    from app import db, main, seed, store

    seed.ensure_seeded()
    monkeypatch.setattr(main, "_APP_TOKEN", "")
    pid = store.list_pipelines()[0]["id"]
    rid = store.create_run(
        pid, None, True, identifier="42",
        params={"company_name": "Orphan Oy", **(params or {})},
        access_key=access_key,
    )
    store.upsert_result(rid, {"order": 0, "name": "FAKTAT", "status": "ok",
                              "parsed_json": {"meta": {}}})
    store.upsert_result(rid, {"order": 1, "name": "Enrichment", "status": "ok",
                              "parsed_json": {}})
    store.upsert_result(rid, {"order": 2, "name": "Raportti", "status": "running"})
    db.execute("UPDATE runs SET heartbeat_at=? WHERE id=?", (_stale(3600), rid))
    return main, store, rid


def _capture_starts(monkeypatch, main):
    started = []

    def fake_start(rid, only=None, from_order=None, completion_status=None):
        started.append({"rid": rid, "only": only, "from_order": from_order,
                        "completion_status": completion_status})
        return True

    monkeypatch.setattr(main, "_start_bg", fake_start)
    return started


def test_orphaned_run_resumes_from_the_unfinished_stage(monkeypatch):
    main, store, rid = _seed(monkeypatch)
    started = _capture_starts(monkeypatch, main)
    monkeypatch.setattr(main.openrouter, "runs_paused", lambda: False)

    _run(main._recover_stale_runs())

    # Stage 2 is re-run; the completed stage 0/1 outputs are reused, not re-paid.
    assert started == [{"rid": rid, "only": None, "from_order": 2,
                        "completion_status": None}]
    assert store.get_run(rid)["status"] == "running"
    assert (store.get_run(rid)["params"] or {}).get("_restart_attempts") == 1


def test_live_run_is_never_adopted_from_another_container(monkeypatch):
    """A rolling deploy runs old and new side by side. A fresh heartbeat means
    the previous container is still generating — hands off."""
    from app import db

    main, store, rid = _seed(monkeypatch)
    db.execute("UPDATE runs SET heartbeat_at=? WHERE id=?", (_stale(5), rid))
    started = _capture_starts(monkeypatch, main)

    _run(main._recover_stale_runs())

    assert started == []
    assert store.get_run(rid)["status"] == "running"


def test_attempt_cap_stops_a_restart_loop_and_refunds(monkeypatch):
    """A run that kills the process would otherwise be resumed on every boot."""
    from app import main as main_mod, store as store_mod

    key = store_mod.create_access_key("orphan", 5)["key"]
    store_mod.consume_generation(key)
    main, store, rid = _seed(
        monkeypatch, access_key=key,
        params={"_restart_attempts": main_mod.RESUME_MAX_ATTEMPTS,
                "delivery_email": "a@b.fi"},
    )
    started = _capture_starts(monkeypatch, main)
    monkeypatch.setattr(main.openrouter, "runs_paused", lambda: False)
    alerted = []

    async def fake_alert(rid_arg, reason=None):
        alerted.append((rid_arg, reason))
        return {"sent": True}

    monkeypatch.setattr(main.email_delivery, "send_admin_run_failed", fake_alert)

    _run(main._recover_stale_runs())

    assert started == []
    run = store.get_run(rid)
    assert run["status"] == "error"
    # The frozen stage row is named, so the UI stops showing "missing sections".
    stage2 = [r for r in run["results"] if r["order"] == 2][0]
    assert stage2["status"] == "error"
    assert "uudelleenkäynnisty" in (stage2["error_message"] or "")
    # Finalization never ran, so the refund has to happen here.
    assert store.get_access_key(key)["generations_used"] == 0
    # The alert must carry WHY: the explanation lives on the stage row, which
    # nobody reading the email can see.
    assert len(alerted) == 1 and alerted[0][0] == rid
    assert "uudelleenkäynnisty" in alerted[0][1]
    assert "yritetty jo" in alerted[0][1]
    assert "krediitti palautettu" in alerted[0][1].lower()


def test_finalization_only_orphan_completes_without_paid_work(monkeypatch):
    """Killed after the last stage but before the status/delivery step: resuming
    past the end costs nothing and lets the normal delivery path run."""
    main, store, rid = _seed(monkeypatch)
    pipeline = store.get_pipeline(store.get_run(rid)["pipeline_id"])
    last = max(s["order"] for s in pipeline["stages"])
    for stage in pipeline["stages"]:
        store.upsert_result(rid, {"order": stage["order"], "name": stage["name"],
                                  "status": "ok", "parsed_json": {"sections": []}})
    started = _capture_starts(monkeypatch, main)

    _run(main._recover_stale_runs())

    assert len(started) == 1
    assert started[0]["from_order"] == last + 1
    # No paid work left → the attempt counter is untouched.
    assert (store.get_run(rid)["params"] or {}).get("_restart_attempts") is None


def test_forecast_mode_orphan_parks_at_awaiting_forecast_again(monkeypatch):
    """A forecast-mode run killed inside stage 0 must return to the review
    pause, not run straight through to a finished report."""
    from app import db, main as main_mod, seed, store

    seed.ensure_seeded()
    monkeypatch.setattr(main_mod, "_APP_TOKEN", "")
    pid = store.list_pipelines()[0]["id"]
    rid = store.create_run(pid, None, True, identifier="42",
                           params={"forecast_mode": True})
    store.upsert_result(rid, {"order": 0, "name": "FAKTAT", "status": "running"})
    db.execute("UPDATE runs SET heartbeat_at=? WHERE id=?", (_stale(3600), rid))
    started = _capture_starts(monkeypatch, main_mod)
    monkeypatch.setattr(main_mod.openrouter, "runs_paused", lambda: False)

    _run(main_mod._recover_stale_runs())

    assert started == [{"rid": rid, "only": 0, "from_order": None,
                        "completion_status": "awaiting_forecast"}]


def test_heartbeat_is_stamped_while_a_stage_runs(monkeypatch):
    """The writer is one 20–35 minute call, so the stamp cannot wait for a stage
    boundary — the ticker has to run alongside it."""
    from app import runner, store

    main, store_mod, rid = _seed(monkeypatch)
    del main, store_mod
    before = store.get_run(rid)["heartbeat_at"]

    async def drive():
        ticker = asyncio.create_task(runner._heartbeat_loop(rid, interval=0.01))
        await asyncio.sleep(0.05)  # stands in for a long-running stage
        ticker.cancel()

    _run(drive())

    assert store.get_run(rid)["heartbeat_at"] > before


def test_expert_run_without_delivery_email_still_alerts(monkeypatch):
    """The /raportti email field is optional, so gating alerts on it silenced the
    most common real failure. An access key means somebody is waiting."""
    from app import email_delivery, store

    key = store.create_access_key("expert", 5)["key"]
    main, _, rid = _seed(monkeypatch, access_key=key)
    del main
    sent = []

    async def fake_alert(subject, intro, rows, **kwargs):
        sent.append(subject)
        return {"sent": True}

    monkeypatch.setattr(email_delivery, "send_admin_alert", fake_alert)

    result = _run(email_delivery.send_admin_run_failed(rid))

    assert result == {"sent": True}
    assert sent and "Orphan Oy" in sent[0]


def test_admin_run_still_stays_out_of_the_shared_inbox(monkeypatch):
    from app import email_delivery

    main, _, rid = _seed(monkeypatch)  # no access key, no delivery_email
    del main

    async def forbidden(*args, **kwargs):
        pytest.fail("admin-ajo ei saa hälyttää jaettuun postilaatikkoon")

    monkeypatch.setattr(email_delivery, "send_admin_alert", forbidden)

    assert _run(email_delivery.send_admin_run_failed(rid))["reason"] == "no-recipient"


def test_driver_exception_is_logged_with_a_traceback(monkeypatch, capsys):
    """The one failure mode with no stage row to explain it used to vanish
    entirely: run flipped to 'error', nothing in the log, no alert."""
    main, store, rid = _seed(monkeypatch)

    async def exploding_run_stages(run, stages, **kwargs):
        raise RuntimeError("boom from the pipeline")
        yield  # pragma: no cover - makes this an async generator

    async def noop_alert(*args, **kwargs):
        return {"sent": True}

    monkeypatch.setattr(main.runner, "run_stages", exploding_run_stages)
    monkeypatch.setattr(main.email_delivery, "send_admin_run_failed", noop_alert)

    _run(main._drive_run(rid))

    out = capsys.readouterr().out
    assert "died in the background driver" in out
    assert "boom from the pipeline" in out
    assert store.get_run(rid)["status"] == "error"


@pytest.mark.parametrize("paused", [True, False])
def test_paused_backend_does_not_resume_but_still_settles_the_run(monkeypatch, paused):
    main, store, rid = _seed(monkeypatch)
    started = _capture_starts(monkeypatch, main)
    monkeypatch.setattr(main.openrouter, "runs_paused", lambda: paused)

    async def fake_alert(_rid):
        return {"sent": True}

    monkeypatch.setattr(main.email_delivery, "send_admin_run_failed", fake_alert)

    _run(main._recover_stale_runs())

    if paused:
        assert started == []
        assert store.get_run(rid)["status"] == "error"  # never left running
    else:
        assert len(started) == 1
