"""Tests for free-text forecast interpretation and its non-importing preview API."""
import asyncio
import json

import httpx
import pytest

from app import forecast_interpret
from app.models import ForecastEdit


def _run(coro):
    return asyncio.run(coro)


def _forecast():
    return {
        "years": [2025, 2026],
        "net_sales": [5000, 5300],
        "ebit": [400, 450],
    }


def test_interpret_uses_millions_contract_and_env_model(monkeypatch):
    captured = {}

    async def fake_chat(**kwargs):
        captured.update(kwargs)
        return {
            "text": """```json
                {"edits":[{"varname":"ns","year":2026,"value":5.8}],
                 "summary":"Liikevaihto nostettiin.","notes":[]}
                ```"""
        }

    monkeypatch.setenv("FORECAST_INTERPRET_MODEL", "test/forecast-model")
    monkeypatch.setattr(forecast_interpret.openrouter, "chat", fake_chat)
    result = _run(forecast_interpret.interpret("Nosta 2026 liikevaihto 5,8 M€:oon", _forecast()))

    assert captured["model"] == "test/forecast-model"
    assert captured["temperature"] == 0.0
    assert captured["expects_json"] is True
    assert '"current_value_meur": 5.3' in captured["prompt"]
    assert "5,3 miljoonaa euroa" in captured["prompt"]
    assert result == {
        "edits": [{"varname": "ns", "year": 2026, "value": 5.8}],
        "summary": "Liikevaihto nostettiin.",
        "notes": [],
    }


@pytest.mark.parametrize("response", [
    "ei jsonia",
    json.dumps({"summary": "edits puuttuu"}),
    json.dumps({
        "edits": [
            {"varname": "ns", "year": 2026, "value": 5.8},
            {"varname": "ns", "year": 2026, "value": 6.0},
        ]
    }),
])
def test_interpret_rejects_malformed_or_duplicate_response(monkeypatch, response):
    async def fake_chat(**kwargs):
        return {"text": response}

    monkeypatch.setattr(forecast_interpret.openrouter, "chat", fake_chat)
    with pytest.raises(forecast_interpret.ForecastInterpretError):
        _run(forecast_interpret.interpret("Muuta ennustetta", _forecast()))


def test_magnitude_guard_compares_millions_to_stage0_teur():
    safe = [ForecastEdit(varname="ns", year=2026, value=5.8)]
    wrong_unit = [ForecastEdit(varname="ns", year=2026, value=5800)]

    assert forecast_interpret.magnitude_notes(_forecast(), safe) == []
    notes = forecast_interpret.magnitude_notes(_forecast(), wrong_unit)
    assert len(notes) == 1
    assert "miljoonat/tEUR" in notes[0]


def _seed_preview_client(monkeypatch, *, access_key=None, with_forecast=True):
    from app import main, seed, store

    class ASGIClient:
        def post(self, path, **kwargs):
            async def send():
                transport = httpx.ASGITransport(app=main.app)
                async with httpx.AsyncClient(
                    transport=transport, base_url="http://testserver"
                ) as client:
                    return await client.post(path, **kwargs)

            return _run(send())

    seed.ensure_seeded()
    monkeypatch.setattr(main, "_check_not_paused", lambda: None)
    main._RATE_HITS.clear()
    pid = store.list_pipelines()[0]["id"]
    rid = store.create_run(
        pid,
        {"meta": {"company_name": "Preview Oy"}},
        True,
        identifier="42",
        access_key=access_key,
    )
    if with_forecast:
        store.upsert_result(rid, {
            "order": 0,
            "name": "FAKTAT",
            "status": "ok",
            "parsed_json": {"forecast": _forecast()},
        })
    return ASGIClient(), main, store, rid


def test_preview_returns_edits_and_teur_rows_without_import(monkeypatch):
    c, main, store, rid = _seed_preview_client(monkeypatch)
    monkeypatch.setattr(main, "_APP_TOKEN", "")

    async def fake_interpret(text, forecast):
        assert text == "Nosta liikevaihto 5,8 miljoonaan vuonna 2026"
        assert forecast == _forecast()
        return {
            "edits": [{"varname": "ns", "year": 2026, "value": 5.8}],
            "summary": "Vuoden 2026 liikevaihto nostetaan 5,8 miljoonaan euroon.",
            "notes": ["Käyttäjän antama absoluuttinen tavoite."],
        }

    monkeypatch.setattr(main.forecast_interpret, "interpret", fake_interpret)
    monkeypatch.setattr(
        main.forecast_import,
        "import_and_wait",
        lambda *args, **kwargs: pytest.fail("preview ei saa käynnistää importtia"),
    )

    before_count = store.refinement_count(rid)
    response = c.post(
        f"/api/runs/{rid}/forecast-preview",
        json={"text": "Nosta liikevaihto 5,8 miljoonaan vuonna 2026"},
    )

    assert response.status_code == 200
    assert response.json() == {
        "edits": [{"varname": "ns", "year": 2026, "value": 5.8}],
        "summary": "Vuoden 2026 liikevaihto nostetaan 5,8 miljoonaan euroon.",
        "rows": [{"varname": "ns", "year": 2026, "old": 5300, "value": 5800.0}],
        "notes": ["Käyttäjän antama absoluuttinen tavoite."],
    }
    assert store.refinement_count(rid) == before_count
    assert store.get_run(rid)["identifier"] == "42"


def test_preview_adds_magnitude_warning(monkeypatch):
    c, main, _, rid = _seed_preview_client(monkeypatch)
    monkeypatch.setattr(main, "_APP_TOKEN", "")

    async def fake_interpret(text, forecast):
        return {
            "edits": [{"varname": "ns", "year": 2026, "value": 5800}],
            "summary": "",
            "notes": [],
        }

    monkeypatch.setattr(main.forecast_interpret, "interpret", fake_interpret)
    response = c.post(f"/api/runs/{rid}/forecast-preview", json={"text": "Muuta"})

    assert response.status_code == 200
    assert response.json()["rows"][0]["value"] == 5_800_000
    assert "miljoonat/tEUR" in response.json()["notes"][0]


def test_preview_rejects_missing_forecast_before_llm(monkeypatch):
    c, main, _, rid = _seed_preview_client(monkeypatch, with_forecast=False)
    monkeypatch.setattr(main, "_APP_TOKEN", "")
    monkeypatch.setattr(
        main.forecast_interpret,
        "interpret",
        lambda *args, **kwargs: pytest.fail("LLM:ää ei saa kutsua ilman ennustetta"),
    )

    response = c.post(f"/api/runs/{rid}/forecast-preview", json={"text": "Muuta"})
    assert response.status_code == 400
    assert "stage 0" in response.text


@pytest.mark.parametrize("edit", [
    {"varname": "revenue", "year": 2026, "value": 5.8},
    {"varname": "ns", "year": 2099, "value": 5.8},
])
def test_preview_rejects_ai_edits_outside_forecast_contract(monkeypatch, edit):
    c, main, _, rid = _seed_preview_client(monkeypatch)
    monkeypatch.setattr(main, "_APP_TOKEN", "")

    async def fake_interpret(text, forecast):
        return {"edits": [edit], "summary": "", "notes": []}

    monkeypatch.setattr(main.forecast_interpret, "interpret", fake_interpret)
    response = c.post(f"/api/runs/{rid}/forecast-preview", json={"text": "Muuta"})
    assert response.status_code == 400


def test_expert_can_preview_own_run_without_consuming_quota(monkeypatch):
    from app import store

    owner = store.create_access_key("preview owner", generations_limit=3)["key"]
    assert store.consume_generation(owner) is True
    c, main, store, rid = _seed_preview_client(monkeypatch, access_key=owner)
    monkeypatch.setattr(main, "_APP_TOKEN", "admin-token")

    async def fake_interpret(text, forecast):
        return {
            "edits": [{"varname": "ebit", "year": 2026, "value": 0.5}],
            "summary": "EBIT päivitetään.",
            "notes": [],
        }

    monkeypatch.setattr(main.forecast_interpret, "interpret", fake_interpret)
    used_before = store.get_access_key(owner)["generations_used"]
    response = c.post(
        f"/api/runs/{rid}/forecast-preview",
        headers={"Authorization": f"Bearer {owner}"},
        json={"text": "EBIT 0,5 miljoonaan"},
    )

    assert response.status_code == 200
    assert store.get_access_key(owner)["generations_used"] == used_before

    other = store.create_access_key("other preview user", generations_limit=3)["key"]
    forbidden = c.post(
        f"/api/runs/{rid}/forecast-preview",
        headers={"Authorization": f"Bearer {other}"},
        json={"text": "EBIT 0,5 miljoonaan"},
    )
    assert forbidden.status_code == 403


def test_preview_rate_limit_stops_before_llm(monkeypatch):
    c, main, _, rid = _seed_preview_client(monkeypatch)
    monkeypatch.setattr(main, "_APP_TOKEN", "")
    monkeypatch.setattr(main, "_rate_ok", lambda bucket, ip: False)
    monkeypatch.setattr(
        main.forecast_interpret,
        "interpret",
        lambda *args, **kwargs: pytest.fail("rate limitin takana ei kutsuta LLM:ää"),
    )

    response = c.post(f"/api/runs/{rid}/forecast-preview", json={"text": "Muuta"})
    assert response.status_code == 429
