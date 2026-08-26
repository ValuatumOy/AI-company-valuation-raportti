"""The customer's own words survive the round, whether or not they act on them.

Regression for NoCFO (2026-08-25/26): the CEO described a forecast change, got a
good AI proposal, never clicked "use these changes", and refined with free text
instead. The forecast description was never stored (the preview endpoint was
stateless) and the free text was stored but never displayed anywhere, so the
round looked identical to one that had applied nothing.
"""
import asyncio

import httpx


def _run(coro):
    return asyncio.run(coro)


def _client():
    from app import main

    class ASGIClient:
        def _call(self, method, path, **kwargs):
            async def send():
                transport = httpx.ASGITransport(app=main.app)
                async with httpx.AsyncClient(
                    transport=transport, base_url="http://testserver"
                ) as client:
                    return await client.request(method, path, **kwargs)

            return _run(send())

        def get(self, path, **kwargs):
            return self._call("GET", path, **kwargs)

        def post(self, path, **kwargs):
            return self._call("POST", path, **kwargs)

    return ASGIClient()


def _forecast():
    return {"years": [2025, 2026], "net_sales": [5000, 5300], "ebit": [400, 450]}


def _seed_run(monkeypatch):
    from app import main, seed, store

    seed.ensure_seeded()
    monkeypatch.setattr(main, "_check_not_paused", lambda: None)
    monkeypatch.setattr(main, "_APP_TOKEN", "")
    main._RATE_HITS.clear()
    pid = store.list_pipelines()[0]["id"]
    rid = store.create_run(
        pid,
        {"meta": {"company_name": "Kommentti Oy"}},
        True,
        identifier="42",
        params={"user_input": "Tilauksen lisätiedot"},
    )
    store.upsert_result(rid, {
        "order": 0, "name": "FAKTAT", "status": "ok",
        "parsed_json": {"forecast": _forecast()},
    })
    return main, store, rid


def test_unaccepted_forecast_preview_is_still_recorded(monkeypatch):
    main, store, rid = _seed_run(monkeypatch)

    async def fake_interpret(text, forecast):
        return {
            "edits": [{"varname": "ns", "year": 2026, "value": 5.8}],
            "summary": "Liikevaihto 5,8 M€ vuonna 2026.",
            "notes": [],
        }

    monkeypatch.setattr(main.forecast_interpret, "interpret", fake_interpret)
    c = _client()

    assert c.post(
        f"/api/runs/{rid}/forecast-preview",
        json={"text": "Nosta liikevaihto 5,8 miljoonaan"},
    ).status_code == 200

    # No accept, no round started — the ask is on the run anyway.
    previews = store.get_run(rid)["params"]["forecast_previews"]
    assert len(previews) == 1
    assert previews[0]["text"] == "Nosta liikevaihto 5,8 miljoonaan"
    assert previews[0]["rows"] == [
        {"varname": "ns", "year": 2026, "old": 5300, "value": 5800.0}
    ]
    assert previews[0]["at"]


def test_comments_collect_every_round_of_the_family(monkeypatch):
    main, store, rid = _seed_run(monkeypatch)
    store.append_forecast_preview(rid, {"text": "Laske 2027 puoleen.", "summary": "", "rows": []})
    child = store.clone_run(rid, params={
        "clarifications": [{"id": "q1", "question": "Kysymys?", "answer": "Vastaus."}],
        "clarifications_free_text": "Holvin sijoitus vastasi n. 20 % omistusta.",
    })
    c = _client()

    body = c.get(f"/api/runs/{child}/comments").json()

    assert [r["run_id"] for r in body["rounds"]] == [rid, child]
    assert body["rounds"][0]["user_input"] == "Tilauksen lisätiedot"
    assert body["rounds"][0]["empty"] is False
    assert body["rounds"][0]["forecast_previews"][0]["text"] == "Laske 2027 puoleen."
    round2 = body["rounds"][1]
    assert round2["clarifications_free_text"].startswith("Holvin sijoitus")
    assert round2["clarifications"][0]["answer"] == "Vastaus."
    assert round2["forecast_changes"] == ""
    # Cloned params carry the parent's order note and previews; show each once.
    assert round2["user_input"] == ""
    assert round2["forecast_previews"] == []


def test_comments_are_admin_only(monkeypatch):
    main, store, rid = _seed_run(monkeypatch)
    monkeypatch.setattr(main, "_APP_TOKEN", "admin-token")
    store.create_access_key("expert", generations_limit=3)
    key = store.list_access_keys()[0]["key"]
    c = _client()

    assert c.get(
        f"/api/runs/{rid}/comments", headers={"Authorization": f"Bearer {key}"}
    ).status_code == 403
    assert c.get(
        f"/api/runs/{rid}/comments", headers={"Authorization": "Bearer admin-token"}
    ).status_code == 200


VALUBUILD_TOLERANCE_ERROR = (
    "Ennusteiden tuonti epäonnistui (job 1): Forecast values were not applied "
    "within tolerance: [ns:2031 submitted 3.5 but the model settled at 2.7892391, "
    "ns:2032 submitted 4.5 but the model settled at 2.9099418042938217, "
    "ebit:2030 submitted 0.25 but the model settled at -2.21422485]"
)


def test_refused_forecast_import_is_recorded_emailed_and_explained(monkeypatch):
    """NoCFO, 2026-08-26. ValuBuild refused the import because the model
    recalculates the late forecast years from its own growth and margin drivers.
    The round never starts, so nothing else in the system records the attempt."""
    from app import forecast_import

    main, store, rid = _seed_run(monkeypatch)
    store.set_run_status(rid, "awaiting_forecast")

    async def refuse(base_fid, values):
        raise forecast_import.ForecastImportError(VALUBUILD_TOLERANCE_ERROR)

    sent = {}

    async def fake_customer_mail(run_id, cells, reason=""):
        sent["rid"] = run_id
        sent["cells"] = cells
        return {"sent": True}

    monkeypatch.setattr(main.forecast_import, "import_and_wait", refuse)
    monkeypatch.setattr(main.email_delivery, "send_forecast_import_failed",
                        fake_customer_mail)
    c = _client()

    response = c.post(f"/api/runs/{rid}/generate-forecast", json={
        "forecast_edits": [
            {"varname": "ns", "year": 2026, "value": 5.3},
            {"varname": "ebit", "year": 2026, "value": 0.25},
        ],
    })

    assert response.status_code == 502
    detail = response.json()["detail"]
    # Finnish, names the years, says nothing was charged — not ValuBuild's dump.
    assert "Liikevaihto 2031–2032" in detail
    assert "EBIT 2030" in detail
    assert "veloitettu" in detail
    assert "submitted" not in detail

    # The customer was told.
    assert sent["rid"] == rid
    assert {(x["varname"], x["year"]) for x in sent["cells"]} == {
        ("ns", 2031), ("ns", 2032), ("ebit", 2030)
    }

    # The attempt survives on the run, and the run is back where it was.
    run = store.get_run(rid)
    assert run["status"] == "awaiting_forecast"
    failure = run["params"]["forecast_import_failures"][0]
    assert failure["edits"][0] == {"varname": "ns", "year": 2026, "value": 5.3}
    assert failure["reason"] == VALUBUILD_TOLERANCE_ERROR
    assert len(failure["rejected_cells"]) == 3

    # And it is visible where a human will actually look.
    body = c.get(f"/api/runs/{rid}/comments").json()
    assert body["rounds"][0]["forecast_import_failures"][0]["reason"] == (
        VALUBUILD_TOLERANCE_ERROR
    )
    assert body["rounds"][0]["empty"] is False


def test_non_tolerance_import_error_falls_back_to_the_raw_reason(monkeypatch):
    from app import forecast_import

    main, store, rid = _seed_run(monkeypatch)
    store.set_run_status(rid, "awaiting_forecast")

    async def refuse(base_fid, values):
        raise forecast_import.ForecastImportError("ValuBuild ei vastannut (HTTP 503).")

    async def fake_customer_mail(run_id, cells, reason=""):
        return {"sent": True}

    monkeypatch.setattr(main.forecast_import, "import_and_wait", refuse)
    monkeypatch.setattr(main.email_delivery, "send_forecast_import_failed",
                        fake_customer_mail)

    response = _client().post(f"/api/runs/{rid}/generate-forecast", json={
        "forecast_edits": [{"varname": "ns", "year": 2026, "value": 5.3}],
    })

    assert response.status_code == 502
    assert response.json()["detail"] == "ValuBuild ei vastannut (HTTP 503)."
