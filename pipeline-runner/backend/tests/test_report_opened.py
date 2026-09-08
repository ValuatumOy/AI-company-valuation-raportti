"""Whether the customer has opened their report, without grepping the logs.

The delivery email attaches the PDF, so this timestamp answers "did they open
the link" — which is what decides whether they have seen the refinement round
at all. On 2026-09-08 the only way to answer that was to read the Railway log
before it rolled over, and by then it had.
"""
import types


def _seed_run(monkeypatch):
    from app import seed, store

    seed.ensure_seeded()
    pid = store.list_pipelines()[0]["id"]
    rid = store.create_run(
        pid, None, True, identifier="1",
        params={"company_name": "Avattu Oy", "delivery_email": "ostaja@example.com"},
    )
    return store, rid


def test_first_open_stays_put_while_the_latest_and_the_count_move(monkeypatch):
    store, rid = _seed_run(monkeypatch)
    assert (store.get_run(rid)["params"] or {}).get("report_opened_at") is None

    store.mark_report_opened(rid)
    first = store.get_run(rid)["params"]
    assert first["report_opened_at"]
    assert first["report_opened_count"] == 1

    store.mark_report_opened(rid)
    second = store.get_run(rid)["params"]
    assert second["report_opened_at"] == first["report_opened_at"]
    assert second["report_opened_count"] == 2
    assert second["report_opened_last"] >= second["report_opened_at"]


def test_the_run_list_carries_the_open_state(monkeypatch):
    store, rid = _seed_run(monkeypatch)
    store.mark_report_opened(rid)
    row = next(r for r in store.list_runs() if r["id"] == rid)
    assert row["report_opened_at"]
    assert row["report_opened_count"] == 1
    assert row["delivery_email"] == "ostaja@example.com"


def test_an_operator_reading_the_report_is_not_the_customer(monkeypatch):
    """Admin calls carry the bearer token and leave access_key None."""
    from app import main

    store, rid = _seed_run(monkeypatch)

    admin = types.SimpleNamespace(state=types.SimpleNamespace(access_key=None))
    main._note_customer_opened(rid, admin)
    assert (store.get_run(rid)["params"] or {}).get("report_opened_at") is None

    buyer = types.SimpleNamespace(state=types.SimpleNamespace(access_key="exp_x"))
    main._note_customer_opened(rid, buyer)
    assert (store.get_run(rid)["params"] or {}).get("report_opened_at")


def test_bookkeeping_never_breaks_the_delivery(monkeypatch):
    from app import main

    store, rid = _seed_run(monkeypatch)

    def boom(_rid):
        raise RuntimeError("db down")

    monkeypatch.setattr(main.store, "mark_report_opened", boom)
    buyer = types.SimpleNamespace(state=types.SimpleNamespace(access_key="exp_x"))
    main._note_customer_opened(rid, buyer)  # must not raise
