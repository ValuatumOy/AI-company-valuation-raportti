"""Tests for the forecast-edit refinement path (ACE #3048).

Covers three layers: the forecast_import ValuBuild client (mirrors
test_estimate_trigger), the export_stream gate bypass, and the round2 endpoint's
two-branch logic (import vs clarifications) including the paid redeem path.
"""
import asyncio
import json

import httpx
import pytest

from app import forecast_import, valuatum


def _run(coro):
    return asyncio.run(coro)


def _configure(monkeypatch):
    monkeypatch.setenv("VALUATUM_API_BASE_URL", "https://valu.test/rest/")
    monkeypatch.setenv("VALUATUM_TOKEN", "test-token")
    monkeypatch.setattr(forecast_import, "POLL_INTERVAL_SECONDS", 0.0)


def _mock_client(monkeypatch, handler):
    real_client = httpx.AsyncClient
    transport = httpx.MockTransport(handler)
    monkeypatch.setattr(
        forecast_import.httpx,
        "AsyncClient",
        lambda **kwargs: real_client(transport=transport, **kwargs),
    )


# ---- forecast_import client -------------------------------------------------

def test_import_posts_body_and_returns_result_fid(monkeypatch):
    _configure(monkeypatch)
    statuses = iter(["RUNNING", "OK"])
    calls = []

    def handler(request):
        calls.append((request.method, str(request.url)))
        assert request.headers["authorization"] == "Bearer test-token"
        if request.method == "POST":
            body = json.loads(request.content)
            assert body == {
                "baseFid": 42,
                "values": [{"varname": "ns", "year": 2026, "value": 60.0}],
            }
            return httpx.Response(202, json={"jobId": 7, "status": "PENDING", "resultFid": None})
        return httpx.Response(
            200, json={"jobId": 7, "status": next(statuses), "resultFid": 4243}
        )

    _mock_client(monkeypatch, handler)
    result_fid = _run(forecast_import.import_and_wait(
        42, [{"varname": "ns", "year": 2026, "value": 60.0}]
    ))

    assert result_fid == 4243
    assert calls == [
        ("POST", "https://valu.test/rest/estimates/import"),
        ("GET", "https://valu.test/rest/estimates/imports/7"),
        ("GET", "https://valu.test/rest/estimates/imports/7"),
    ]


def test_import_surfaces_job_error_reason(monkeypatch):
    _configure(monkeypatch)

    def handler(request):
        if request.method == "POST":
            return httpx.Response(202, json={"jobId": 8, "status": "PENDING"})
        return httpx.Response(
            200, json={"jobId": 8, "status": "ERROR", "errorMessage": "mallin tallennus kaatui"}
        )

    _mock_client(monkeypatch, handler)
    with pytest.raises(forecast_import.ForecastImportError, match="mallin tallennus"):
        _run(forecast_import.import_and_wait(42, [{"varname": "ns", "year": 2026, "value": 60.0}]))


def test_import_missing_result_fid_on_ok_is_error(monkeypatch):
    _configure(monkeypatch)
    _mock_client(
        monkeypatch,
        lambda request: httpx.Response(202, json={"jobId": 9, "status": "OK"}),
    )
    with pytest.raises(forecast_import.ForecastImportError, match="resultFid"):
        _run(forecast_import.import_and_wait(42, [{"varname": "ns", "year": 2026, "value": 60.0}]))


def test_import_times_out(monkeypatch):
    _configure(monkeypatch)
    monkeypatch.setattr(forecast_import, "TIMEOUT_SECONDS", 0.0)
    _mock_client(
        monkeypatch,
        lambda request: httpx.Response(202, json={"jobId": 10, "status": "RUNNING"}),
    )
    with pytest.raises(forecast_import.ForecastImportError, match="aikakatkaistiin"):
        _run(forecast_import.import_and_wait(42, [{"varname": "ns", "year": 2026, "value": 60.0}]))


def test_import_surfaces_connection_error(monkeypatch):
    _configure(monkeypatch)

    def handler(request):
        raise httpx.ConnectError("endpoint alhaalla", request=request)

    _mock_client(monkeypatch, handler)
    with pytest.raises(forecast_import.ForecastImportError, match="Yhteys ValuBuildin"):
        _run(forecast_import.import_and_wait(42, [{"varname": "ns", "year": 2026, "value": 60.0}]))


# ---- export_stream gate bypass ----------------------------------------------

def _minimal_modeldata():
    return {
        "meta": {"company_name": "Test Oy", "y_tunnus": "1234567-8"},
        "headcount": {}, "actuals": {}, "forecast": {}, "forecast_parameters": {},
        "valuation_engine": {}, "key_ratios": {}, "credit_risk": {},
        "peers": [], "client_reported_signals": [], "flags": [],
    }


async def _async_value(value):
    return value


def test_export_stream_skips_gate_for_edited_forecast(monkeypatch):
    async def inline(function, *args, **kwargs):
        return function(*args, **kwargs)

    monkeypatch.setattr(valuatum.asyncio, "to_thread", inline)
    monkeypatch.setenv("VALUATUM_TOKEN", "tok")
    monkeypatch.setenv("VALUATUM_API_BASE_URL", "https://valu.test/rest")
    monkeypatch.delenv("VALUATUM_MCP_URL", raising=False)

    def forbidden_trigger(fid):
        pytest.fail("estimate-generointia ei saa ajaa muokatulle fidille")

    def fake_run(cmd):
        output = cmd[cmd.index("--output") + 1]
        with open(output, "w", encoding="utf-8") as handle:
            json.dump(_minimal_modeldata(), handle)
        return 0, "", ""

    monkeypatch.setattr(valuatum.estimate_trigger, "trigger_and_wait", forbidden_trigger)
    monkeypatch.setattr(valuatum, "_run", fake_run)
    monkeypatch.setattr(valuatum, "lookup_company_metadata", lambda **kwargs: _async_value({}))

    events = _run(_collect(valuatum.export_stream(
        "Test Oy", 4243, skip_estimate_generation=True
    )))
    assert [e["step"] for e in events] == ["estimates", "fetch", "ready"]
    assert "Skipping" in events[0]["label"]


async def _collect(gen):
    return [event async for event in gen]


def test_company_data_forwards_skip_flag(monkeypatch):
    captured = {}

    async def fake_export_stream(**kwargs):
        captured.update(kwargs)
        yield {"step": "ready", "json": {"meta": {}}, "warnings": []}

    monkeypatch.setattr(valuatum, "export_stream", fake_export_stream)
    from fetchers import company_data
    _run(company_data.fetch_company_data("4243", {"skip_estimate_generation": True}))
    assert captured["skip_estimate_generation"] is True
    assert captured["fid"] == 4243


# ---- round2 endpoint: two-branch logic --------------------------------------

def _seed_client(monkeypatch):
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
    monkeypatch.setattr(main, "_APP_TOKEN", "")
    monkeypatch.setattr(main, "_start_bg", lambda *a, **k: True)  # never actually run
    return ASGIClient(), main, store


def _parent_with_forecast(store, pid, fid="42"):
    rid = store.create_run(pid, {"meta": {"company_name": "X"}}, True, fid)
    store.upsert_result(rid, {
        "order": 0, "name": "FAKTAT", "status": "ok",
        "parsed_json": {"forecast": {
            "years": [2025, 2026], "net_sales": [50000, 55000], "ebit": [10000, 11000],
        }},
    })
    return rid


def test_forecast_import_round_imports_new_fid_and_runs_from_stage0(monkeypatch):
    _, main, store = _seed_client(monkeypatch)
    from app.models import ForecastEdit

    async def fake_import(base_fid, values):
        assert base_fid == 42
        assert values == [{"varname": "ns", "year": 2026, "value": 60.0}]
        return 4243

    monkeypatch.setattr(main.forecast_import, "import_and_wait", fake_import)
    started = {}
    monkeypatch.setattr(main, "_start_bg",
                        lambda rid, **k: started.update(rid=rid, kwargs=k) or True)

    pid = store.list_pipelines()[0]["id"]
    parent = _parent_with_forecast(store, pid)
    child_rid = _run(main._start_forecast_import_round(
        parent,
        store.get_run(parent),
        [ForecastEdit(varname="ns", year=2026, value=60.0)],
    ))
    child = store.get_run(child_rid)
    # Re-fid'd to the imported model, gate bypassed, re-run from stage 0.
    assert child["identifier"] == "4243"
    assert child["params"]["skip_estimate_generation"] is True
    assert started["kwargs"].get("from_order") == 0
    # Human-readable change summary reaches the writer context params.
    assert "Liikevaihto 2026" in child["params"]["forecast_changes"]
    assert "60 000" in child["params"]["forecast_changes"]  # 60 M€ -> tEUR
    # Stage 0 result is NOT seeded from the parent (it must refetch the new fid).
    assert not any(res.get("order") == 0 for res in (child.get("results") or []))
    # And the parent's input_data is dropped so the runner's manual-paste shortcut
    # can't reuse the OLD fid's FAKTAT — stage 0 fetches by the new identifier.
    assert child.get("input_data") is None


def test_forecast_import_round_carries_clarifications(monkeypatch):
    """The feedback panel lets a user answer clarifications AND edit forecasts in
    one submit — the forecast branch must not silently drop the answers (the
    round re-runs stage 1, which is where they get folded in)."""
    _, main, store = _seed_client(monkeypatch)
    from app.models import ClarificationAnswer, ForecastEdit

    async def fake_import(base_fid, values):
        return 4243

    monkeypatch.setattr(main.forecast_import, "import_and_wait", fake_import)

    pid = store.list_pipelines()[0]["id"]
    parent = _parent_with_forecast(store, pid)
    child_rid = _run(main._start_forecast_import_round(
        parent,
        store.get_run(parent),
        [ForecastEdit(varname="ns", year=2026, value=60.0)],
        clarifications=[
            ClarificationAnswer(
                id="q1", question="Omistus?", answer="100 % perustajilla"
            )
        ],
        clarifications_free_text="IPR on yhtiöllä itsellään.",
    ))
    child = store.get_run(child_rid)
    assert child["params"]["clarifications"] == [
        {"id": "q1", "question": "Omistus?", "answer": "100 % perustajilla"}
    ]
    assert child["params"]["clarifications_free_text"] == "IPR on yhtiöllä itsellään."


def test_round2_clarifications_branch_unchanged(monkeypatch):
    c, main, store = _seed_client(monkeypatch)

    def forbidden_import(*a, **k):
        pytest.fail("tekstihaara ei saa tuoda ennusteita")

    monkeypatch.setattr(main.forecast_import, "import_and_wait", forbidden_import)
    started = {}
    monkeypatch.setattr(main, "_start_bg",
                        lambda rid, **k: started.update(kwargs=k) or True)

    pid = store.list_pipelines()[0]["id"]
    parent = _parent_with_forecast(store, pid)
    r = c.post(f"/api/runs/{parent}/round2",
               json={"clarifications": [], "clarifications_free_text": "uusi tieto"})
    assert r.status_code == 200
    child = store.get_run(r.json()["run_id"])
    # Same fid as parent, no skip flag, re-run from stage 1 (enrichment).
    assert child["identifier"] == "42"
    assert "skip_estimate_generation" not in child["params"]
    assert started["kwargs"].get("from_order") == 1


def test_forecast_import_failure_starts_no_round(monkeypatch):
    _, main, store = _seed_client(monkeypatch)
    from fastapi import HTTPException
    from app.models import ForecastEdit

    async def failing_import(base_fid, values):
        raise main.forecast_import.ForecastImportError("ValuBuild kaatui")

    monkeypatch.setattr(main.forecast_import, "import_and_wait", failing_import)
    monkeypatch.setattr(main, "_start_bg",
                        lambda *a, **k: pytest.fail("kierrosta ei saa käynnistää import-virheessä"))

    pid = store.list_pipelines()[0]["id"]
    parent = _parent_with_forecast(store, pid)
    before = store.lineage_depth(parent)
    with pytest.raises(HTTPException) as exc:
        _run(main._start_forecast_import_round(
            parent,
            store.get_run(parent),
            [ForecastEdit(varname="ns", year=2026, value=60.0)],
        ))
    assert exc.value.status_code == 502
    assert "ValuBuild kaatui" in str(exc.value.detail)
    # No child run was created → no quota/lineage consumed.
    assert store.lineage_depth(parent) == before


def test_round2_forecast_edit_rejects_bad_varname(monkeypatch):
    c, main, store = _seed_client(monkeypatch)
    monkeypatch.setattr(main.forecast_import, "import_and_wait",
                        lambda *a, **k: pytest.fail("importtia ei saa kutsua validoinnin kaatuessa"))
    pid = store.list_pipelines()[0]["id"]
    parent = _parent_with_forecast(store, pid)
    r = c.post(f"/api/runs/{parent}/round2", json={
        "forecast_edits": [{"varname": "revenue", "year": 2026, "value": 60.0}],
    })
    assert r.status_code == 400


def test_free_round2_forecast_edit_requires_checkout_before_cap(monkeypatch):
    c, main, store = _seed_client(monkeypatch)
    monkeypatch.setenv("ROUND2_MAX_PER_RUN", "0")
    monkeypatch.setattr(main.forecast_import, "import_and_wait",
                        lambda *a, **k: pytest.fail("katon ylittyessä importtia ei saa tehdä"))
    pid = store.list_pipelines()[0]["id"]
    parent = _parent_with_forecast(store, pid)
    r = c.post(f"/api/runs/{parent}/round2", json={
        "forecast_edits": [{"varname": "ns", "year": 2026, "value": 60.0}],
    })
    assert r.status_code == 402
    assert "checkout" in r.text


def test_paid_redeem_runs_forecast_import_branch(monkeypatch):
    from app import db
    c, main, store = _seed_client(monkeypatch)
    monkeypatch.setattr(main, "STRIPE_SECRET_KEY", "sk_test_x")
    monkeypatch.setenv("CLIENT_SITE_URL", "https://valuatum-arvonmaaritys.vercel.app")

    async def fake_import(base_fid, values):
        assert base_fid == 42
        assert values == [{"varname": "ebit", "year": 2026, "value": 12.0}]
        return 4243

    monkeypatch.setattr(main.forecast_import, "import_and_wait", fake_import)
    started = {}
    monkeypatch.setattr(main, "_start_bg",
                        lambda rid, **k: started.update(rid=rid, kwargs=k) or True)

    pid = store.list_pipelines()[0]["id"]
    parent = _parent_with_forecast(store, pid)

    async def fake_create(**kw):
        return {"id": "cs_test_fc", "url": "https://checkout.stripe.com/pay/cs_test_fc"}

    monkeypatch.setattr(main, "_stripe_create_checkout_session", fake_create)
    r = c.post(f"/api/runs/{parent}/round2/checkout", json={
        "forecast_edits": [{"varname": "ebit", "year": 2026, "value": 12.0}],
        "clarifications_free_text": "Uusi sopimus allekirjoitettu.",
    })
    assert r.status_code == 200
    pending = db.query_one("SELECT * FROM pending_rounds WHERE run_id=?", (parent,))
    token = pending["token"]
    # The staged edits survived to the DB for redemption after payment.
    assert json.loads(pending["forecast_edits"]) == [
        {"varname": "ebit", "year": 2026, "value": 12.0}
    ]

    async def fake_get_paid(session_id):
        return {"payment_status": "paid", "metadata": {"token": token}}

    monkeypatch.setattr(main, "_stripe_get_checkout_session", fake_get_paid)
    r2 = c.post(f"/api/runs/{parent}/round2/redeem",
                json={"token": token, "stripe_session_id": "cs_test_fc"})
    assert r2.status_code == 200
    child = store.get_run(r2.json()["run_id"])
    assert child["identifier"] == "4243"
    assert child["params"]["skip_estimate_generation"] is True
    assert started["kwargs"].get("from_order") == 0
    # Staged clarification text rides along with the paid forecast edits.
    assert child["params"]["clarifications_free_text"] == "Uusi sopimus allekirjoitettu."


def test_paid_redeem_import_failure_keeps_token_redeemable(monkeypatch):
    """A paid token must survive an import failure: the forecast branch calls
    ValuBuild after the payment check, and burning the token there would mean
    money taken, no round, no retry."""
    from app import db
    c, main, store = _seed_client(monkeypatch)
    monkeypatch.setattr(main, "STRIPE_SECRET_KEY", "sk_test_x")
    monkeypatch.setenv("CLIENT_SITE_URL", "https://valuatum-arvonmaaritys.vercel.app")
    monkeypatch.setattr(main, "_start_bg", lambda *a, **k: True)

    pid = store.list_pipelines()[0]["id"]
    parent = _parent_with_forecast(store, pid)

    async def fake_create(**kw):
        return {"id": "cs_test_fail", "url": "https://checkout.stripe.com/pay/cs_test_fail"}

    monkeypatch.setattr(main, "_stripe_create_checkout_session", fake_create)
    r = c.post(f"/api/runs/{parent}/round2/checkout", json={
        "forecast_edits": [{"varname": "ns", "year": 2026, "value": 60.0}],
    })
    assert r.status_code == 200
    token = db.query_one("SELECT * FROM pending_rounds WHERE run_id=?", (parent,))["token"]

    async def fake_get_paid(session_id):
        return {"payment_status": "paid", "metadata": {"token": token}}

    monkeypatch.setattr(main, "_stripe_get_checkout_session", fake_get_paid)

    async def failing_import(base_fid, values):
        raise main.forecast_import.ForecastImportError("ValuBuild kaatui")

    monkeypatch.setattr(main.forecast_import, "import_and_wait", failing_import)
    r_fail = c.post(f"/api/runs/{parent}/round2/redeem",
                    json={"token": token, "stripe_session_id": "cs_test_fail"})
    assert r_fail.status_code == 502
    pending = db.query_one("SELECT * FROM pending_rounds WHERE token=?", (token,))
    assert pending["consumed"] == 0  # claim rolled back → still redeemable

    async def working_import(base_fid, values):
        return 4243

    monkeypatch.setattr(main.forecast_import, "import_and_wait", working_import)
    r_retry = c.post(f"/api/runs/{parent}/round2/redeem",
                     json={"token": token, "stripe_session_id": "cs_test_fail"})
    assert r_retry.status_code == 200
    assert store.get_run(r_retry.json()["run_id"])["identifier"] == "4243"

    # Now the token is spent for good: a further redeem is a 409.
    r_again = c.post(f"/api/runs/{parent}/round2/redeem",
                     json={"token": token, "stripe_session_id": "cs_test_fail"})
    assert r_again.status_code == 409


def test_checkout_rejects_bad_forecast_edit_before_payment(monkeypatch):
    c, main, store = _seed_client(monkeypatch)
    monkeypatch.setattr(main, "STRIPE_SECRET_KEY", "sk_test_x")
    monkeypatch.setattr(main, "_stripe_create_checkout_session",
                        lambda **k: pytest.fail("Stripe-sessiota ei saa luoda virheelliselle syötteelle"))
    pid = store.list_pipelines()[0]["id"]
    parent = _parent_with_forecast(store, pid)
    r = c.post(f"/api/runs/{parent}/round2/checkout", json={
        "forecast_edits": [{"varname": "ns", "year": 2026, "value": 0}],  # ns must be > 0
    })
    assert r.status_code == 400
