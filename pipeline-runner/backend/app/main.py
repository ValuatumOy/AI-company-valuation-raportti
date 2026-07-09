"""FastAPI app. The OpenRouter key lives here, never in the browser."""
import asyncio
import hmac
import json
import os
import re
from contextlib import asynccontextmanager

import httpx
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse
from sse_starlette.sse import EventSourceResponse

load_dotenv()

from . import email_delivery, openrouter, report, runner, seed, store, validators, valuatum  # noqa: E402
from .models import (  # noqa: E402
    AccessKeyIn, CheckoutGenerateIn, CompareIn, ExpertGenerateIn, FetchIn, OrderIn,
    OrderStatusIn, PipelineIn, RedeemRoundIn, ReorderIn, Round2In, RunIn, StageIn,
    ValidateIn, ValuatumExportIn,
)
from fetchers.company_data import fetch_company_data  # noqa: E402


@asynccontextmanager
async def _lifespan(app):
    seed.ensure_seeded()
    store.reset_stale_runs()  # clear orphan 'running' rows left by the last restart
    await openrouter.refresh_models()
    yield


app = FastAPI(title="Valuation Pipeline Runner", lifespan=_lifespan)

_origins = os.getenv("ALLOWED_ORIGINS", "*")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"] if _origins == "*" else [o.strip() for o in _origins.split(",")],
    allow_origin_regex=None if _origins == "*" else r"https://.*\.vercel\.app",
    allow_methods=["*"],
    allow_headers=["*"],
)

# Shared-token gate. If APP_TOKEN is unset (local dev) auth is disabled.
# When set, every /api/* call (except /api/health) needs:
#   Authorization: Bearer <APP_TOKEN>
_APP_TOKEN = os.getenv("APP_TOKEN", "")

# Bump on deploy to confirm which build is live (surfaced in /api/health).
BUILD = "2026-07-09-credit-refund-paid-toggle"

# Round-2 refinement writer. Preserve-and-patch is an editing task, not creative
# writing — Sonnet 5 ($2/$10) does it at 1/2.5 of Opus 4.8's price; round-1
# authorship stays Fable. Env-overridable for A/B.
ROUND2_WRITER_MODEL = (
    os.getenv("ROUND2_WRITER_MODEL") or "anthropic/claude-sonnet-5"
)

# Paid extra refinement rounds (round 3+, once ROUND2_MAX_PER_RUN's free
# rounds are used up). REST calls via httpx, not the stripe SDK — two
# endpoints (create session, retrieve session) don't justify a new
# dependency when httpx is already required.
STRIPE_SECRET_KEY = (os.getenv("STRIPE_SECRET_KEY") or "").strip()
EXTRA_ROUND_PRICE_CENTS = int(os.getenv("EXTRA_ROUND_PRICE_CENTS") or 500)
_STRIPE_API = "https://api.stripe.com/v1"


async def _stripe_create_checkout_session(*, success_url, cancel_url, metadata, amount_cents, name):
    data = {
        "mode": "payment",
        "success_url": success_url,
        "cancel_url": cancel_url,
        "line_items[0][quantity]": "1",
        "line_items[0][price_data][currency]": "eur",
        "line_items[0][price_data][unit_amount]": str(amount_cents),
        "line_items[0][price_data][product_data][name]": name,
        **{f"metadata[{k}]": v for k, v in metadata.items()},
    }
    async with httpx.AsyncClient(timeout=20) as client:
        resp = await client.post(
            f"{_STRIPE_API}/checkout/sessions", data=data, auth=(STRIPE_SECRET_KEY, "")
        )
    resp.raise_for_status()
    return resp.json()


async def _stripe_get_checkout_session(session_id: str):
    async with httpx.AsyncClient(timeout=20) as client:
        resp = await client.get(
            f"{_STRIPE_API}/checkout/sessions/{session_id}", auth=(STRIPE_SECRET_KEY, "")
        )
    resp.raise_for_status()
    return resp.json()


# Paths a capped expert key (`exp_`) may reach. DENY-BY-DEFAULT: everything not
# matched here (reseed, pipeline/stage edits, orders, key minting, deletes, cost
# admin) is admin-token-only. Ownership of a specific run is checked in the
# endpoint via _require_run_access — the allowlist only opens the route.
_EXPERT_GET = re.compile(
    r"^/api/(pipelines(/[^/]+)?"
    r"|companies"
    r"|company-search"
    r"|runs/[^/]+(/readiness|/report\.(html|pdf)|/stream)?"
    r"|expert/me)$"
)
_EXPERT_POST = re.compile(
    r"^/api/(expert/generate|runs/[^/]+/round2(/checkout|/redeem)?)$"
)


def _expert_path_allowed(method: str, path: str) -> bool:
    if method == "GET":
        return bool(_EXPERT_GET.match(path))
    if method == "POST":
        return bool(_EXPERT_POST.match(path))
    return False


@app.middleware("http")
async def auth_gate(request, call_next):
    request.state.access_key = None  # None = admin/unlimited
    if _APP_TOKEN and request.method != "OPTIONS":
        path = request.url.path
        # POST /api/orders is the public website order intake — no bearer.
        # It only writes a capped-length row; abuse guarded by rate limit + honeypot.
        if path == "/api/orders" and request.method == "POST":
            return await call_next(request)
        # POST /api/public/checkout-generate: same public/unauthenticated shape,
        # called by the client site right after a verified Stripe payment.
        if path == "/api/public/checkout-generate" and request.method == "POST":
            return await call_next(request)
        # GET /api/public/company-search: public homepage search, no invite key.
        if path == "/api/public/company-search" and request.method == "GET":
            return await call_next(request)
        if path.startswith("/api/") and path != "/api/health":
            from fastapi.responses import JSONResponse

            sent = request.headers.get("authorization", "")
            if hmac.compare_digest(sent, f"Bearer {_APP_TOKEN}"):
                pass  # full admin access
            elif sent.startswith("Bearer exp_"):
                key = sent[len("Bearer "):]
                row = store.get_access_key(key)
                if not row or not row.get("active"):
                    return JSONResponse({"detail": "unauthorized"}, status_code=401)
                if not _expert_path_allowed(request.method, path):
                    return JSONResponse({"detail": "forbidden"}, status_code=403)
                request.state.access_key = key
            else:
                return JSONResponse({"detail": "unauthorized"}, status_code=401)
    return await call_next(request)


def _check_not_paused():
    """503 before any credit is consumed or run created. openrouter.chat has the
    hard backstop for paths that slip past the endpoint guards (rerun, compare)."""
    if openrouter.runs_paused():
        raise HTTPException(
            503, "Raporttien generointi on väliaikaisesti keskeytetty ylläpidon toimesta."
        )


def _require_run_access(rid: str, request: Request):
    """A run served to an expert key must have been created by that same key.
    Admin (access_key None) sees everything. Returns the run or raises."""
    run = store.get_run(rid)
    if not run:
        raise HTTPException(404, "run not found")
    key = getattr(request.state, "access_key", None)
    if key and run.get("access_key") != key:
        raise HTTPException(403, "forbidden")
    return run


@app.get("/api/health")
def health():
    from . import db

    return {
        "ok": True,
        "auth": bool(_APP_TOKEN),
        "db": "postgres" if db.IS_PG else "sqlite",
        "build": BUILD,
    }


# ---- pipelines / stages -----------------------------------------------------

@app.get("/api/pipelines")
def get_pipelines():
    seed.ensure_current_defaults()
    return store.list_pipelines()


@app.post("/api/pipelines")
def post_pipeline(body: PipelineIn):
    return store.create_pipeline(body.name)


@app.post("/api/reseed")
def post_reseed():
    return seed.reseed_defaults(force=True)


@app.get("/api/pipelines/{pid}")
def get_pipeline(pid: str):
    seed.ensure_current_defaults()
    p = store.get_pipeline(pid)
    if not p:
        raise HTTPException(404, "pipeline not found")
    return p


@app.patch("/api/pipelines/{pid}")
def rename_pipeline(pid: str, body: PipelineIn):
    if not store.get_pipeline(pid):
        raise HTTPException(404, "pipeline not found")
    store.rename_pipeline(pid, body.name)
    return store.get_pipeline(pid)


@app.post("/api/pipelines/{pid}/stages")
def post_stage(pid: str, body: StageIn):
    if not store.get_pipeline(pid):
        raise HTTPException(404, "pipeline not found")
    return store.add_stage(pid, body.model_dump())


@app.put("/api/stages/{sid}")
def put_stage(sid: str, body: StageIn):
    s = store.update_stage(sid, body.model_dump())
    if not s:
        raise HTTPException(404, "stage not found")
    return s


@app.delete("/api/stages/{sid}")
def del_stage(sid: str):
    store.delete_stage(sid)
    return {"ok": True}


@app.post("/api/pipelines/{pid}/reorder")
def post_reorder(pid: str, body: ReorderIn):
    return store.reorder(pid, body.stage_ids)


# ---- models -----------------------------------------------------------------

@app.get("/api/models")
def get_models():
    return openrouter.models()


@app.post("/api/models/refresh")
async def refresh_models():
    return await openrouter.refresh_models()


# ---- stage 0 fetcher --------------------------------------------------------

@app.post("/api/fetch-company")
async def fetch_company(body: FetchIn):
    try:
        data = await fetch_company_data(body.identifier, body.params)
        return {"ok": True, "input_data": data}
    except NotImplementedError as e:
        return {"ok": False, "not_implemented": True, "message": str(e)}
    except Exception as e:
        return {"ok": False, "message": str(e)}


@app.get("/api/valuatum/config")
def valuatum_config():
    return {
        "token": bool(os.getenv("VALUATUM_TOKEN")),
        "profinder": bool(os.getenv("VALU_MCP_PROFINDER_URL")),
        "kit": valuatum.EXPORT.exists(),
    }


@app.post("/api/valuatum/company-json")
async def valuatum_company_json(body: ValuatumExportIn):
    async def gen():
        async for ev in valuatum.export_stream(
            company_name=body.company_name,
            fid=body.fid,
            actuals=body.actuals,
            estimates=body.estimates,
            company_code_override=body.company_code_override,
        ):
            # On success, remember the company (name + FID + fetched data) so the
            # user never has to look up the FID or refetch to run again.
            if ev.get("step") == "ready":
                try:
                    meta = (ev.get("json") or {}).get("meta") or {}
                    code = (
                        body.company_code_override
                        or (meta.get("y_tunnus") or "").replace("-", "").strip()
                        or None
                    )
                    store.upsert_company(
                        fid=body.fid,
                        company_name=body.company_name,
                        company_code=code,
                        actuals=body.actuals,
                        estimates=body.estimates,
                        input_data=ev.get("json"),
                    )
                except Exception:
                    pass
            yield {"data": json.dumps(ev, ensure_ascii=False)}

    # ping=20 keeps the SSE connection alive while the subprocess runs (up to 180 s)
    return EventSourceResponse(gen(), ping=20)


# ---- saved companies (remembered name + FID, instant re-run) ----------------

@app.get("/api/companies")
def get_companies():
    return store.list_companies()


@app.get("/api/companies/{fid}")
def get_company_one(fid: int):
    c = store.get_company(fid)
    if not c:
        raise HTTPException(404, "company not found")
    return c


@app.delete("/api/companies/{fid}")
def del_company(fid: int):
    store.delete_company(fid)
    return {"ok": True}


@app.get("/api/company-search")
async def company_search(q: str):
    """Resolve a company name or y-tunnus to Valuatum FID(s) so self-serve
    generation isn't limited to the operator's pre-fetched company list (the
    long-standing FID blocker)."""
    q = (q or "").strip()
    if len(q) < 2:
        return []
    try:
        return await valuatum.search_company(q)
    except httpx.HTTPStatusError as e:
        raise HTTPException(e.response.status_code, e.response.text[:500])
    except RuntimeError as e:
        raise HTTPException(500, str(e))


_SEARCH_HITS: dict[str, list[float]] = {}
_SEARCH_LIMIT, _SEARCH_WINDOW_S = 60, 60.0  # search-as-you-type needs a per-minute cap, not per-hour


def _search_rate_ok(ip: str) -> bool:
    import time

    now = time.monotonic()
    hits = [t for t in _SEARCH_HITS.get(ip, []) if now - t < _SEARCH_WINDOW_S]
    if len(hits) >= _SEARCH_LIMIT:
        _SEARCH_HITS[ip] = hits
        return False
    hits.append(now)
    _SEARCH_HITS[ip] = hits
    return True


@app.get("/api/public/company-search")
async def company_search_public(q: str, request: Request):
    """Public, unauthenticated version of /api/company-search for the client
    site's homepage search (that page has no invite key to send). One row per
    company, not per followed model — same disambiguation heuristic as the
    checkout-generate endpoint, since the marketing site just needs "does this
    company exist in Valuatum", not the model picker /testi has."""
    q = (q or "").strip()
    if len(q) < 2:
        return []
    ip = (request.headers.get("x-forwarded-for") or "").split(",")[0].strip() \
        or (request.client.host if request.client else "?")
    # Own limiter, not _order_rate_ok's 5/hour — that's sized for order
    # submission, not autocomplete, and a search box blows through 5/hour in
    # one typing session (bug found live: "Athlos" then "Athlos Oy" locked
    # the visitor out of search for an hour).
    if not _search_rate_ok(ip):
        raise HTTPException(429, "liian monta hakua — yritä hetken kuluttua uudelleen")
    try:
        candidates = await valuatum.search_company(q)
    except httpx.HTTPStatusError as e:
        raise HTTPException(e.response.status_code, e.response.text[:500])
    except RuntimeError as e:
        raise HTTPException(500, str(e))
    by_company: dict[str, list[dict]] = {}
    order: list[str] = []
    for c in candidates:
        code = c.get("company_code") or str(c.get("fid"))
        if code not in by_company:
            order.append(code)
        by_company.setdefault(code, []).append(c)
    out = []
    for code in order:
        best = _pick_checkout_candidate(by_company[code])
        if best:
            out.append(best)
    return out


# ---- website orders (public intake; operator fulfils in this UI) -------------
# ponytail: in-memory per-IP rate limit — enough for one process; move to the DB
# if the backend ever runs more than one instance.
_ORDER_HITS: dict[str, list[float]] = {}
_ORDER_LIMIT, _ORDER_WINDOW_S = 5, 3600.0


def _order_rate_ok(ip: str) -> bool:
    import time

    now = time.monotonic()
    hits = [t for t in _ORDER_HITS.get(ip, []) if now - t < _ORDER_WINDOW_S]
    if len(hits) >= _ORDER_LIMIT:
        _ORDER_HITS[ip] = hits
        return False
    hits.append(now)
    _ORDER_HITS[ip] = hits
    return True


@app.post("/api/orders")
def post_order(body: OrderIn, request: Request):
    if body.website.strip():  # honeypot filled → bot; pretend success
        return {"ok": True}
    ip = (request.headers.get("x-forwarded-for") or "").split(",")[0].strip() \
        or (request.client.host if request.client else "?")
    if not _order_rate_ok(ip):
        raise HTTPException(429, "liian monta tilausta — yritä myöhemmin uudelleen")
    oid = store.create_order(
        body.company.strip(), body.email.strip(),
        body.user_input.strip() or None,
    )
    return {"ok": True, "order_id": oid}


@app.get("/api/orders")
def get_orders():
    return store.list_orders()


@app.patch("/api/orders/{oid}")
def patch_order(oid: str, body: OrderStatusIn):
    row = store.set_order_status(oid, body.status)
    if not row:
        raise HTTPException(404, "order not found")
    return dict(row)


@app.get("/api/sample-input-data")
def sample_input_data():
    path = os.path.join(os.path.dirname(__file__), "..", "fetchers",
                        "sample_input_data.json")
    with open(path, encoding="utf-8") as f:
        return json.load(f)


# ---- validators -------------------------------------------------------------

@app.post("/api/validate")
def post_validate(body: ValidateIn):
    return validators.run_validator(body.validator_code, body.output, body.context)


# ---- runs -------------------------------------------------------------------

@app.post("/api/runs")
def post_run(body: RunIn, request: Request):
    p = store.get_pipeline(body.pipeline_id)
    if not p:
        raise HTTPException(404, "pipeline not found")
    _check_not_paused()
    # A new round-1 report is where an expert's quota is spent. Claim it before
    # creating the run (a bad pipeline_id 404s above, before any unit is spent).
    key = getattr(request.state, "access_key", None)
    if key and not store.consume_generation(key):
        raise HTTPException(403, "Generointikiintiö on käytetty loppuun.")
    rid = store.create_run(
        body.pipeline_id, body.input_data, body.stop_on_failure,
        identifier=body.identifier, params=body.params, access_key=key,
    )
    return {"run_id": rid}


def _run_with_params(rid):
    # identifier/params are persisted on the run row (store.get_run), so a
    # background run survives a restart and compare/rerun need no in-memory state.
    return store.get_run(rid)


async def _stream(rid, only=None, from_order=None):
    run = _run_with_params(rid)
    if not run:
        raise HTTPException(404, "run not found")
    p = store.get_pipeline(run["pipeline_id"])
    store.set_run_status(rid, "running")
    async for event in runner.run_stages(
        run, p["stages"], only=only, from_order=from_order
    ):
        yield {"data": json.dumps(event, ensure_ascii=False)}


@app.get("/api/runs/{rid}/stream")
async def stream_run(rid: str, request: Request):
    _require_run_access(rid, request)
    return EventSourceResponse(_stream(rid))


# ---- background runner ------------------------------------------------------
# Drive a run to completion server-side, decoupled from any client connection.
# run_stages persists each stage result as it finishes, so the client just polls
# GET /api/runs/{rid}. This means a run survives the tab closing / backgrounding
# (e.g. iOS Safari) and the progress UI never depends on fetch-stream buffering.
_RUN_TASKS: dict[str, asyncio.Task] = {}


async def _drive_run(rid: str, only=None, from_order=None):
    try:
        run = _run_with_params(rid)
        if not run:
            return
        p = store.get_pipeline(run["pipeline_id"])
        store.set_run_status(rid, "running")
        async for _ in runner.run_stages(
            run, p["stages"], only=only, from_order=from_order
        ):
            pass
    except Exception:
        try:
            store.set_run_status(rid, "error")
        except Exception:
            pass
    finally:
        try:
            final_run = store.get_run(rid)
            if final_run and final_run.get("status") == "ok":
                await email_delivery.send_report_ready(rid)
        except Exception as e:
            print(f"report email delivery failed for {rid}: {e}", flush=True)
        _RUN_TASKS.pop(rid, None)


def _start_bg(rid: str, only=None, from_order=None) -> bool:
    task = _RUN_TASKS.get(rid)
    if task and not task.done():
        return False
    _RUN_TASKS[rid] = asyncio.create_task(
        _drive_run(rid, only=only, from_order=from_order)
    )
    return True


@app.post("/api/runs/{rid}/start")
async def start_run(rid: str, request: Request,
                    from_order: int | None = None, only: int | None = None):
    _check_not_paused()
    _require_run_access(rid, request)
    # Experts may only (re)start their own run as a whole; scoped stage reruns
    # are an admin operation.
    if getattr(request.state, "access_key", None) and (
        from_order not in (None, 1) or only is not None
    ):
        raise HTTPException(403, "forbidden")
    return {"ok": True, "started": _start_bg(rid, only=only, from_order=from_order)}


@app.post("/api/runs/{rid}/round2")
async def round2_run(rid: str, body: Round2In, request: Request):
    """Round 2: clone the parent run (reuse its stage-0 FAKTAT), fold the user's
    clarifications into params, and re-run from enrichment so the corrected facts
    reshape the locked business thesis and the scenarios."""
    _check_not_paused()
    parent = _require_run_access(rid, request)
    # Round-2 is credit-free, so cap refinements per report — otherwise one key
    # can spam unlimited Opus-priced full-report rewrites ($6-runs incident).
    # Depth-based, not a per-node child count: refining round 2's own result
    # again would otherwise reset the count (a fresh run always has 0
    # children of its own) and the cap would never actually bite.
    max_r2 = int(os.getenv("ROUND2_MAX_PER_RUN") or 2)
    if store.lineage_depth(rid) >= max_r2:
        raise HTTPException(
            429, f"Tarkennuskierrosten enimmäismäärä ({max_r2}) on jo käytetty "
                 "tälle raportille."
        )
    new_rid = _start_refinement_round(
        rid, parent, body.clarifications, body.clarifications_free_text,
        show_old_numbers=body.show_old_numbers,
    )
    return {"run_id": new_rid, "parent_run_id": rid}


def _start_refinement_round(rid, parent, clarifications, clarifications_free_text,
                            show_old_numbers=False) -> str:
    # Maximal-preserve: hand the round the prior enrichment + assembled report
    # so it refines (keep the good, apply the fix) instead of regenerating blind.
    prev_enrichment = next(
        (r.get("parsed_json") for r in (parent.get("results") or [])
         if r.get("order") == 1),
        None,
    )
    new_rid = store.clone_run(rid, params={
        "clarifications": [
            c.model_dump() if hasattr(c, "model_dump") else c for c in clarifications
        ],
        "clarifications_free_text": clarifications_free_text,
        "previous_enrichment": prev_enrichment,
        "previous_report": store.final_report_json(rid),
        "show_old_numbers": show_old_numbers,
        # Careful preserve-and-patch is an editing task, not creative writing —
        # use Opus for the round-2 writer while round 1 stays Fable.
        "round2_writer_model": ROUND2_WRITER_MODEL,
    })
    # NOTE: every refinement round MUST re-run stage 1 (from_order=1): the
    # enrichment stage is where clarifications get folded in (1_enrichment.txt
    # KIERROS 2 -KURI) — the writer prompt has no {{clarifications}} of its
    # own, it consumes the corrected enrichment. That stage is
    # maximal-preserve + targeted search, so it's cheap (~$0.15), not a full
    # re-research.
    _start_bg(new_rid, from_order=1)
    return new_rid


@app.post("/api/runs/{rid}/round2/checkout")
async def round2_checkout(rid: str, body: Round2In, request: Request):
    """Round 3+ isn't free — create a Stripe Checkout Session for one paid
    extra refinement. The clarification answers are staged server-side
    (Stripe metadata is far too small to hold them); metadata only carries a
    lookup token, redeemed by round2_redeem after payment succeeds."""
    _require_run_access(rid, request)
    if not STRIPE_SECRET_KEY:
        raise HTTPException(503, "Lisäkierrosten maksut eivät ole vielä käytössä.")
    key = getattr(request.state, "access_key", None)
    token = store.create_pending_round(
        rid, key, [c.model_dump() for c in body.clarifications], body.clarifications_free_text
    )
    site = (os.getenv("CLIENT_SITE_URL") or "").rstrip("/")
    key_q = f"&key={key}" if key else ""
    success_url = (
        f"{site}/testi?rid={rid}{key_q}&paid_round_token={token}"
        f"&show_old_numbers={1 if body.show_old_numbers else 0}"
        "&session_id={CHECKOUT_SESSION_ID}"
    )
    cancel_url = f"{site}/testi?rid={rid}{key_q}"
    session = await _stripe_create_checkout_session(
        success_url=success_url, cancel_url=cancel_url,
        metadata={"token": token, "rid": rid},
        amount_cents=EXTRA_ROUND_PRICE_CENTS,
        name="Arvonmäärityksen lisätarkennuskierros",
    )
    return {"checkout_url": session.get("url")}


@app.post("/api/runs/{rid}/round2/redeem")
async def round2_redeem(rid: str, body: RedeemRoundIn, request: Request):
    """After Stripe confirms payment, actually run the paid extra round."""
    _require_run_access(rid, request)
    if not STRIPE_SECRET_KEY:
        raise HTTPException(503, "Lisäkierrosten maksut eivät ole vielä käytössä.")
    _check_not_paused()
    pending = store.get_pending_round(body.token)
    if not pending or pending["run_id"] != rid:
        raise HTTPException(404, "Tarkennuskierrosta ei löytynyt.")
    if pending["consumed"]:
        raise HTTPException(409, "Tämä tarkennuskierros on jo käytetty.")
    try:
        session = await _stripe_get_checkout_session(body.stripe_session_id)
    except httpx.HTTPStatusError as e:
        raise HTTPException(e.response.status_code, e.response.text[:500])
    if (session.get("payment_status") != "paid"
            or (session.get("metadata") or {}).get("token") != body.token):
        raise HTTPException(402, "Maksua ei voitu vahvistaa.")
    store.consume_pending_round(body.token)
    parent = store.get_run(rid)
    new_rid = _start_refinement_round(
        rid, parent, pending["clarifications"], pending["clarifications_free_text"],
        show_old_numbers=body.show_old_numbers,
    )
    return {"run_id": new_rid, "parent_run_id": rid}


@app.post("/api/runs/{rid}/stages/{order}/rerun")
async def rerun_stage(rid: str, order: int):
    return EventSourceResponse(_stream(rid, only=order))


@app.post("/api/runs/{rid}/stages/{order}/rerun-from")
async def rerun_from(rid: str, order: int):
    return EventSourceResponse(_stream(rid, from_order=order))


@app.get("/api/costs")
def get_costs():
    return store.costs_summary()


@app.get("/api/report-capabilities")
def report_capabilities():
    return {
        "generator": report.generator_available(),
        "pdf": report.find_chrome() is not None,
    }


def _require_ready(rid: str, force: int):
    """Block delivering a report from an unhealthy run unless explicitly forced.
    This is the single most important safety check — it stops a run whose number
    validators FAILED from being handed to a paying client."""
    r = store.report_readiness(rid)
    if not r["ready"] and not force:
        raise HTTPException(409, {"detail": "raportti ei läpäissyt tarkistuksia",
                                  "issues": r["issues"]})


@app.get("/api/runs/{rid}/readiness")
def run_readiness(rid: str, request: Request):
    _require_run_access(rid, request)
    return store.report_readiness(rid)


@app.get("/api/runs/{rid}/report-source")
def report_source(rid: str):
    j = store.final_report_json(rid)
    if j is None:
        raise HTTPException(400, "ei valmista loppuvaiheen JSONia tälle ajolle")
    return j


@app.post("/api/preview-report")
def preview_report(body: dict):
    """Render an arbitrary report JSON to HTML — no run, no LLM, no cost. Powers
    fast design iteration and the in-app report preview."""
    from . import render
    try:
        return HTMLResponse(render.render_html(body))
    except Exception as e:
        raise HTTPException(400, str(e))


@app.get("/api/runs/{rid}/report.html")
def report_html(rid: str, request: Request, force: int = 0):
    _require_run_access(rid, request)
    j = store.final_report_json(rid)
    if j is None:
        raise HTTPException(400, "ei valmista loppuvaiheen JSONia tälle ajolle")
    _require_ready(rid, force)
    try:
        path = report.generate_html(rid, j)
    except Exception as e:
        raise HTTPException(500, str(e))
    return HTMLResponse(open(path, encoding="utf-8").read())


@app.get("/api/runs/{rid}/report.pdf")
def report_pdf(rid: str, request: Request, force: int = 0):
    _require_run_access(rid, request)
    j = store.final_report_json(rid)
    if j is None:
        raise HTTPException(400, "ei valmista loppuvaiheen JSONia tälle ajolle")
    _require_ready(rid, force)
    try:
        path = report.generate_pdf(rid, j)
    except Exception as e:
        raise HTTPException(503, str(e))
    return FileResponse(path, media_type="application/pdf",
                        filename=f"raportti-{rid[:8]}.pdf")


@app.get("/api/runs")
def get_runs():
    return store.list_runs()


@app.get("/api/runs/{rid}")
def get_run(rid: str, request: Request):
    return _require_run_access(rid, request)


# ---- expert access keys -----------------------------------------------------
# Mint/list are admin-only (the auth middleware never lets an `exp_` key reach
# /api/access-keys — it's off the expert allowlist). /api/expert/me lets an
# expert read their own remaining quota for the client-site gate.

@app.post("/api/access-keys")
def post_access_key(body: AccessKeyIn):
    return store.create_access_key(
        body.label, body.generations_limit, body.expires_at)


@app.get("/api/access-keys")
def list_access_keys():
    return store.list_access_keys()


@app.get("/api/expert/me")
def expert_me(request: Request):
    key = getattr(request.state, "access_key", None)
    if not key:  # admin token, not an expert context
        raise HTTPException(400, "not an expert key")
    row = store.get_access_key(key)
    if not row:
        raise HTTPException(401, "unauthorized")
    limit = row["generations_limit"]
    unlimited = limit is None or limit <= 0
    return {
        "label": row["label"],
        "generations_used": row["generations_used"],
        "generations_limit": limit,
        "unlimited": unlimited,
        "remaining": None if unlimited else max(0, limit - row["generations_used"]),
    }


def _default_pipeline_id(pipeline_id: str | None) -> str:
    # Self-serve defaults to the single-writer "koeajo" pipeline (FAKTAT +
    # enrichment + one writer), not the 6-stage default. Callers can still
    # pass an explicit pipeline_id (the admin/operator UI does).
    if pipeline_id:
        return pipeline_id
    pls = store.list_pipelines() or []
    sw = next((p for p in pls if p.get("name") == seed.SINGLE_WRITER_PIPELINE_NAME), None)
    return (sw or (pls[0] if pls else {})).get("id")


def _create_generation_run(
    *, fid: int, company_name: str, company_code=None, industry_text=None,
    industry_code=None, industry_id=None, industry_tree=None,
    delivery_email=None, user_input="", pipeline_id=None, access_key=None,
) -> str:
    """Shared by the invite-key expert flow and the public paid checkout flow:
    build the stage-0 params and kick off a background run."""
    pid = _default_pipeline_id(pipeline_id)
    if not pid or not store.get_pipeline(pid):
        raise HTTPException(404, "pipeline not found")
    _check_not_paused()
    params = {"company_name": company_name}
    if company_code:
        params["company_code"] = company_code
    if industry_text:
        params["industry_text"] = industry_text
    if industry_code:
        params["industry_code"] = industry_code
    if industry_id is not None:
        params["industry_id"] = industry_id
    if industry_tree is not None:
        params["industry_tree"] = industry_tree
    if delivery_email:
        params["delivery_email"] = delivery_email.strip()
    if user_input.strip():
        params["user_input"] = user_input.strip()
    rid = store.create_run(
        pid, None, True, identifier=str(fid), params=params, access_key=access_key,
    )
    _start_bg(rid)
    return rid


@app.post("/api/expert/generate")
async def expert_generate(body: ExpertGenerateIn, request: Request):
    """Self-serve generation: create a run that fetches the company's Valuatum
    data in stage 0 (by FID) and runs the pipeline. Consumes one generation for
    expert keys; admin (access_key None) is unlimited. Round-2 refinement of the
    resulting report is free (see round2_run)."""
    _default_pipeline_id(body.pipeline_id) or HTTPException(404, "pipeline not found")
    _check_not_paused()
    key = getattr(request.state, "access_key", None)
    if key and not store.consume_generation(key):
        raise HTTPException(403, "Generointikiintiö on käytetty loppuun.")
    rid = _create_generation_run(
        fid=body.fid, company_name=body.company_name, company_code=body.company_code,
        industry_text=body.industry_text, industry_code=body.industry_code,
        industry_id=body.industry_id, industry_tree=body.industry_tree,
        delivery_email=body.delivery_email, user_input=body.user_input,
        pipeline_id=body.pipeline_id, access_key=key,
    )
    return {"run_id": rid}


def _pick_checkout_candidate(candidates: list[dict]) -> dict | None:
    """Automated FID pick for the unattended checkout flow (no human in the
    loop to use the /testi disambiguation picker). Prefer the "Profinder"
    auto-model, then a parent company_code (no "K" suffix — see HANDOFF for
    the K-suffix group-company convention), else the first result.
    ponytail: heuristic, not guaranteed correct for every company — if this
    ever picks the wrong sibling model, surface a manual picker on the
    checkout success page instead of guessing harder here."""
    if not candidates:
        return None
    for c in candidates:
        if c.get("analyst_name") == "Profinder":
            return c
    for c in candidates:
        if not str(c.get("company_code") or "").upper().endswith("K"):
            return c
    return candidates[0]


@app.post("/api/public/checkout-generate")
async def public_checkout_generate(body: CheckoutGenerateIn, request: Request):
    """Public, unauthenticated: called by the client site's Stripe success page
    right after payment is verified server-side. Resolves the paid company to a
    Valuatum FID, mints a single-use access key, and starts generation — closing
    the "operator fulfils manually" gap for the self-serve paid flow. Same
    honeypot + IP rate limit as /api/orders; idempotent on stripe_session_id so
    a page reload after payment doesn't double-generate or double-mint a key."""
    if body.website.strip():  # honeypot filled → bot; pretend success
        return {"ok": True}
    ip = (request.headers.get("x-forwarded-for") or "").split(",")[0].strip() \
        or (request.client.host if request.client else "?")
    if not _order_rate_ok(ip):
        raise HTTPException(429, "liian monta tilausta — yritä myöhemmin uudelleen")
    existing = store.get_order_by_session(body.stripe_session_id)
    if existing:
        return {"run_id": existing.get("run_id"), "key": existing.get("access_key")}
    _check_not_paused()
    try:
        candidates = await valuatum.search_company(body.business_id)
    except httpx.HTTPStatusError as e:
        raise HTTPException(e.response.status_code, e.response.text[:500])
    except RuntimeError as e:
        raise HTTPException(500, str(e))
    candidate = _pick_checkout_candidate(candidates)
    if not candidate:
        raise HTTPException(
            404, f"Yritystä ({body.business_id}) ei löytynyt Valuatumista."
        )
    key_row = store.create_access_key(
        f"Tilaus: {body.company_name} ({body.stripe_session_id[:12]})",
        generations_limit=1,
    )
    key = key_row["key"]
    # Consume the one paid-for generation now — this call IS that generation,
    # it doesn't go through /api/expert/generate's own consume_generation.
    store.consume_generation(key)
    rid = _create_generation_run(
        fid=candidate["fid"], company_name=candidate.get("company_name") or body.company_name,
        company_code=candidate.get("company_code"), industry_text=candidate.get("industry_text"),
        industry_code=candidate.get("industry_code"), industry_id=candidate.get("industry_id"),
        industry_tree=candidate.get("industry_tree"), delivery_email=body.email,
        user_input=body.user_input, access_key=key,
    )
    store.create_paid_order(
        body.company_name, body.email, body.user_input, body.stripe_session_id,
        candidate["fid"], key, rid,
    )
    return {"run_id": rid, "key": key}


@app.delete("/api/runs/{rid}")
def delete_run(rid: str):
    # Don't delete a run whose background task is still executing.
    task = _RUN_TASKS.get(rid)
    if task and not task.done():
        raise HTTPException(409, "run is still executing")
    store.delete_run(rid)
    return {"ok": True}


# ---- compare models on a single stage --------------------------------------

@app.post("/api/runs/{rid}/stages/{order}/compare")
async def compare_models(rid: str, order: int, body: CompareIn):
    """Run the same stage with several models, return outputs side by side.
    Does not persist — purely for the user's A/B comparison."""
    run = _run_with_params(rid)
    if not run:
        raise HTTPException(404, "run not found")
    p = store.get_pipeline(run["pipeline_id"])
    stage = next((s for s in p["stages"] if s["order"] == order), None)
    if not stage:
        raise HTTPException(404, "stage not found")

    # rebuild context from stored prior results
    ctx = {}
    if run.get("input_data") is not None:
        ctx["input_data"] = run["input_data"]
    for r in run["results"]:
        if r["order"] < order and r.get("status") in ("ok", "validation_failed"):
            s2 = next((x for x in p["stages"] if x["order"] == r["order"]), None)
            if s2:
                runner._contribute(ctx, s2, runner._output_value(r))

    out = []
    for model in body.models:
        variant = {**stage, "model": model}
        res = await runner._execute_stage(
            variant, ctx, run.get("input_data"), run.get("identifier"),
            run.get("params", {}),
        )
        out.append({
            "model": model,
            "status": res["status"],
            "raw_response": res.get("raw_response"),
            "parsed_json": res.get("parsed_json"),
            "validator_report": res.get("validator_report"),
            "tokens_completion": res.get("tokens_completion"),
            "cost_usd": res.get("cost_usd"),
            "finish_reason": res.get("finish_reason"),
            "error_message": res.get("error_message"),
        })
    return {"results": out}
