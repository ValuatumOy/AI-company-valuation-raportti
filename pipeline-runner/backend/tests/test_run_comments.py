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
