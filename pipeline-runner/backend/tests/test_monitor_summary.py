"""store.monitor_summary — the report monitor's per-report figures.

One sale buys a root run plus its refinement rounds, so the family is the unit:
cost and paid rounds sum over it, timing and status come from the root.
"""
import json

import pytest


@pytest.fixture(autouse=True)
def _empty_tables():
    from app import db

    db.init_db()
    for t in ("stage_results", "orders", "runs"):
        db.execute(f"DELETE FROM {t}")
    yield


FROM, TO = "2026-08-01T00:00:00+00:00", "2026-09-01T00:00:00+00:00"


def _run(rid, created, status, parent=None, company="Acme Oy"):
    from app import db

    db.execute(
        "INSERT INTO runs(id,pipeline_id,input_data,status,stop_on_failure,"
        "total_cost_usd,created_at,identifier,params,parent_run_id) "
        "VALUES(?,?,?,?,?,?,?,?,?,?)",
        (rid, "p", None, status, 1, 0.0, created, "123",
         json.dumps({"company_name": company, "delivery_email": "b@x.fi"}), parent),
    )


def _stage(rid, cost, started, finished):
    from app import store

    store.upsert_result(rid, {"order": 0, "name": "s", "status": "ok",
                              "cost_usd": cost, "started_at": started,
                              "finished_at": finished})


def test_family_sums_cost_and_sales_but_keeps_the_root_s_timing():
    from app import store

    _run("A", "2026-08-20T10:00:00+00:00", "ok")
    _run("A2", "2026-08-20T11:00:00+00:00", "ok", parent="A")
    _run("A3", "2026-08-20T12:00:00+00:00", "ok", parent="A2")
    _stage("A", 1.00, "2026-08-20T10:00:05+00:00", "2026-08-20T10:03:05+00:00")
    _stage("A2", 0.50, "2026-08-20T11:00:00+00:00", "2026-08-20T11:02:00+00:00")
    _stage("A3", 0.25, "2026-08-20T12:00:00+00:00", "2026-08-20T12:04:00+00:00")
    store.create_paid_order("Acme Oy", "b@x.fi", "", "cs_1", 123, "k", "A", 7900, "eur")
    store.create_paid_order("Acme Oy", "b@x.fi", "", "cs_2", 123, "k", "A3", 500, "eur")

    (r,) = store.monitor_summary(FROM, TO)
    assert r["runId"] == "A"          # rounds never surface as reports of their own
    assert r["rounds"] == 2
    assert r["costUsd"] == 1.75
    assert r["order"] == {"email": "b@x.fi", "company": "Acme Oy",
                          "amountTotalCents": 8400, "currency": "eur", "sales": 2}
    # Not 12:04 — that would measure the customer thinking between rounds.
    assert r["finishedAt"] == "2026-08-20T10:03:05+00:00"


def test_unsold_and_unpriced_runs_survive_the_join():
    from app import db, store

    _run("B", "2026-08-21T09:00:00+00:00", "error", company="Beta Oy")
    _run("C", "2026-08-22T09:00:00+00:00", "awaiting_forecast", company="Gamma Oy")
    db.execute(
        "INSERT INTO orders(id,company,email,status,created_at,run_id) "
        "VALUES(?,?,?,?,?,?)",
        ("o", "Gamma Oy", "c@x.fi", "in_progress", "2026-08-22T09:00:00+00:00", "C"),
    )

    by_id = {r["runId"]: r for r in store.monitor_summary(FROM, TO)}
    assert by_id["B"]["order"] is None            # internal/expert generation
    assert by_id["C"]["order"]["amountTotalCents"] is None  # order predates the column


def test_range_is_bounded_by_the_root_s_creation():
    from app import store

    _run("D", "2026-07-01T09:00:00+00:00", "ok")
    assert store.monitor_summary(FROM, TO) == []


def _client(monkeypatch, monitor_token=""):
    from starlette.testclient import TestClient

    from app import main

    monkeypatch.setattr(main, "_APP_TOKEN", "secret")
    monkeypatch.setattr(main, "_MONITOR_TOKEN", monitor_token)
    return TestClient(main.app)


QUERY = "/api/monitor/summary?from=2026-08-01T00:00:00Z&to=2026-09-01T00:00:00Z"


def test_the_endpoint_is_admin_only(monkeypatch):
    from app import store

    c = _client(monkeypatch)
    key = store.create_access_key("a customer", 1)["key"]
    assert c.get(QUERY).status_code == 401
    # A customer's report key must not read every buyer on the platform.
    assert c.get(QUERY, headers={"authorization": f"Bearer {key}"}).status_code == 403
    assert c.get(QUERY, headers={"authorization": "Bearer secret"}).status_code == 200


def test_the_bounds_are_restated_in_the_shape_created_at_is_stored_in(monkeypatch):
    c = _client(monkeypatch)
    body = c.get(QUERY, headers={"authorization": "Bearer secret"}).json()
    # "Z" would not sort against the "+00:00" _now() writes.
    assert body["from"] == "2026-08-01T00:00:00+00:00"
    assert c.get("/api/monitor/summary?from=nope&to=x",
                 headers={"authorization": "Bearer secret"}).status_code == 400


def test_the_monitor_token_opens_this_endpoint_and_nothing_else(monkeypatch):
    c = _client(monkeypatch, monitor_token="mon")
    h = {"authorization": "Bearer mon"}
    assert c.get(QUERY, headers=h).status_code == 200
    # The whole point of a second token: no runs, no orders, no report bodies.
    for path in ("/api/runs", "/api/orders", "/api/costs", "/api/access-keys",
                 "/api/pipelines"):
        assert c.get(path, headers=h).status_code == 401, path
    assert c.post("/api/reseed", headers=h).status_code == 401


def test_without_a_monitor_token_only_the_admin_one_opens_the_endpoint(monkeypatch):
    c = _client(monkeypatch)
    assert c.get(QUERY, headers={"authorization": "Bearer mon"}).status_code == 401
    assert c.get(QUERY, headers={"authorization": "Bearer secret"}).status_code == 200
