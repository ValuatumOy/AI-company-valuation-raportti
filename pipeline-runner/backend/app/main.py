"""FastAPI app. The OpenRouter key lives here, never in the browser."""
import asyncio
import hmac
import json
import math
import os
import re
import traceback
from contextlib import asynccontextmanager

import httpx
from dotenv import load_dotenv
from fastapi import BackgroundTasks, FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse
from pydantic import ValidationError
from sse_starlette.sse import EventSourceResponse

load_dotenv()

from . import (  # noqa: E402
    email_delivery, forecast_import, forecast_interpret, openrouter, report, runner, seed, store,
    validators, valuatum,
)
from .models import (  # noqa: E402
    AccessKeyIn, AccessKeyLimitIn, CheckoutGenerateIn, CompareIn, ExpertGenerateIn, FetchIn,
    ForecastEdit, ForecastPreviewIn, GenerateForecastIn, OrderIn, OrderStatusIn, PipelineIn,
    RedeemRoundIn, ReorderIn, Round2In, RunIn, StageIn, ValidateIn, ValuatumExportIn,
)
from fetchers.company_data import fetch_company_data  # noqa: E402


@asynccontextmanager
async def _lifespan(app):
    seed.ensure_seeded()
    store.reset_stale_runs()  # an interrupted forecast import becomes retryable
    await openrouter.refresh_models()
    await _recover_stale_runs()  # resume what the last process left mid-flight
    yield
    await valuatum.aclose_client()


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
BUILD = "2026-08-05-checkout-product-check"

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

# Which paid Stripe sessions may start a run here. Stripe delivers an event to
# EVERY webhook endpoint registered on the account, and the sibling products on
# luottoriskit.fi put the very same field names (businessId/fid/companyName)
# in their checkout metadata — so a 10 € credit-report purchase looked exactly
# like an arvonmääritys purchase to the client site's webhook, which called
# this endpoint, which only asked "is it paid?" and spent ~$3 generating the
# wrong product for that buyer (2026-08-05). A session must now prove it bought
# THIS product: either the client site's marker, or a known price id.
VALUATION_PRODUCT_TAG = "arvonmaaritys_ai_raportti"
# Mirrors STRIPE_AI_REPORT_PRICE_ID on the client site (same default), so the
# check holds even for sessions created before the marker shipped. Comma-
# separated; extend it when the price is rotated or for a test-mode price.
VALUATION_PRICE_IDS = {
    p.strip()
    for p in (
        os.getenv("STRIPE_VALUATION_PRICE_IDS") or "price_1Ty8Pt2FVkKDgcuUD2fzJ8Fk"
    ).split(",")
    if p.strip()
}


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


async def _stripe_get_checkout_session(session_id: str, *, with_line_items: bool = False):
    params = {"expand[]": "line_items"} if with_line_items else None
    async with httpx.AsyncClient(timeout=20) as client:
        resp = await client.get(
            f"{_STRIPE_API}/checkout/sessions/{session_id}",
            params=params,
            auth=(STRIPE_SECRET_KEY, ""),
        )
    resp.raise_for_status()
    return resp.json()


def _is_valuation_session(session: dict) -> bool:
    """Did this paid session buy the arvonmääritys report, or another product
    sold from the same Stripe account? See VALUATION_PRODUCT_TAG."""
    if (session.get("metadata") or {}).get("product") == VALUATION_PRODUCT_TAG:
        return True
    for item in (session.get("line_items") or {}).get("data") or []:
        price = item.get("price") or {}
        if price.get("id") in VALUATION_PRICE_IDS:
            return True
    return False


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
    r"^/api/(expert/generate|runs/[^/]+/"
    r"(round2(/checkout|/redeem)?|forecast-preview|fetch-forecast|generate-forecast))$"
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

# The list is served light (no prompt bodies, no validator sources): with them
# it was 474 kB and ~6.8 s, and a connection that drops mid-flight never
# finishes it — which is how the admin UI came to hang on boot. The picker
# never renders a prompt; the stage editor gets the real bodies from
# GET /api/pipelines/{pid}.
@app.get("/api/pipelines")
def get_pipelines():
    seed.ensure_current_defaults()
    # `retired` marks a pipeline nothing may run through (the 6-stage one kept
    # for old runs) so the admin UI can hide it instead of offering it as a
    # choice that 400s on start.
    return [{**p, "retired": not str(p.get("name") or "").startswith(
        seed.SINGLE_WRITER_PIPELINE_PREFIX)}
        for p in store.list_pipelines(light=True)]


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
    if len(q) < valuatum.MIN_QUERY_LENGTH:
        return []
    try:
        return await valuatum.search_company(q)
    except httpx.HTTPStatusError as e:
        raise HTTPException(e.response.status_code, e.response.text[:500])
    except RuntimeError as e:
        raise HTTPException(500, str(e))


# ponytail: in-memory per-(bucket, ip) rate limits — enough for one process; move
# to the DB if the backend ever runs more than one instance.
#
# Each caller gets its OWN bucket. They used to share one dict per limiter, which
# conflated two very different callers: /api/orders is hit from the visitor's
# BROWSER (real per-user IPs), while /api/public/checkout-generate is hit
# SERVER-SIDE by the client site's /kassa/valmis Server Component — so every
# paying customer arrives on the same Vercel egress IP. Sharing the 5/hour order
# limit made it a *global* 5-reports-per-hour ceiling: the 6th buyer in an hour
# got a 429 that the client site silently swallowed.
_RATE_HITS: dict[tuple[str, str], list[float]] = {}

# (limit, window_seconds) per bucket.
_RATE_RULES = {
    # search-as-you-type, and ALSO server-to-server: the client site proxies it
    # through its own /api/search route, so every visitor of the marketing site
    # shares one Vercel egress IP here too. 60/min was a site-wide ceiling that a
    # handful of simultaneous visitors could exhaust — and the client swallows the
    # 429 into an empty result list (CombinedDataSource catches and falls back to
    # the bundled sample catalogue), so the visitor just doesn't find their
    # company. No LLM cost sits behind this lookup, so keep it generous.
    "search": (600, 60.0),
    # browser-submitted order intake: real per-visitor IPs
    "order": (5, 3600.0),
    # server-to-server, one shared Vercel IP for every customer. Idempotent on
    # stripe_session_id, and VALU_DAILY_USD_CAP is the real money backstop — this
    # is only a runaway-loop guard, so it must be far above any real hour's sales.
    "checkout": (40, 3600.0),
    # Cheap LLM interpretation call. This does not consume a report credit or
    # import anything, but a runaway preview loop should still be bounded.
    "forecast_preview": (20, 60.0),
}


def _rate_ok(bucket: str, ip: str) -> bool:
    import time

    limit, window = _RATE_RULES[bucket]
    now = time.monotonic()
    key = (bucket, ip)
    hits = [t for t in _RATE_HITS.get(key, []) if now - t < window]
    if len(hits) >= limit:
        _RATE_HITS[key] = hits
        return False
    hits.append(now)
    _RATE_HITS[key] = hits
    return True


def _client_ip(request: Request) -> str:
    return (request.headers.get("x-forwarded-for") or "").split(",")[0].strip() \
        or (request.client.host if request.client else "?")


@app.get("/api/public/company-search")
async def company_search_public(q: str, request: Request):
    """Public, unauthenticated version of /api/company-search for the client
    site's homepage search (that page has no invite key to send). One row per
    company, not per followed model — same disambiguation heuristic as the
    checkout-generate endpoint, since the marketing site just needs "does this
    company exist in Valuatum", not the model picker /raportti has."""
    q = (q or "").strip()
    if len(q) < valuatum.MIN_QUERY_LENGTH:
        return []
    # Own bucket, not the 5/hour order limit — that's sized for order submission,
    # not autocomplete, and a search box blows through 5/hour in one typing
    # session (bug found live: "Athlos" then "Athlos Oy" locked the visitor out
    # of search for an hour).
    if not _rate_ok("search", _client_ip(request)):
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


# ---- internal alerts ---------------------------------------------------------

async def _admin_notify(coro, ref: str, label: str) -> None:
    """Fire an internal alert without ever letting it disturb the customer path:
    a broken shared inbox must not change what the buyer receives."""
    try:
        result = await coro
        if isinstance(result, dict) and not result.get("sent", True):
            print(f"admin {label} for {ref} not sent: {result}", flush=True)
    except Exception as e:
        print(f"admin {label} for {ref} failed: {e}", flush=True)


async def _order_intake_alert(oid: str, company: str, email: str, user_input) -> None:
    await _admin_notify(
        email_delivery.send_admin_order_intake(oid, company, email, user_input),
        oid, "order intake alert",
    )


# ---- deploy / crash recovery -------------------------------------------------
# A report takes 20–45 minutes, almost all of it inside one writer call, so any
# restart in that window used to destroy the run: the container died mid-stage,
# the run row was blanket-flipped to 'error', the stage row stayed 'running',
# nothing was logged, no alert fired and the customer's credit was eaten
# (2026-07-31, run 8dfd3918 — killed by our own deploy). Deploying should not
# require knowing whether a run is in flight, so orphans are now resumed.

RESUME_STALE_SECONDS = float(os.getenv("VALU_RESUME_STALE_SECONDS") or 180)
RESUME_MAX_ATTEMPTS = int(os.getenv("VALU_RESUME_MAX_ATTEMPTS") or 2)
RESUME_MAX_AGE_HOURS = float(os.getenv("VALU_RESUME_MAX_AGE_HOURS") or 6)


def _resume_point(run, pipeline):
    """Order of the first stage that still owes work, or one past the last stage
    when every stage finished (the process died during finalization — resuming
    there costs nothing and lets the normal delivery path run)."""
    done = {r["order"]: r.get("status") for r in (run.get("results") or [])}
    orders = []
    for stage in sorted(pipeline["stages"], key=lambda s: s["order"]):
        orders.append(stage["order"])
        if not stage.get("enabled"):
            continue
        if done.get(stage["order"]) not in ("ok", "validation_failed", "skipped"):
            return stage["order"], False
    return (max(orders) + 1 if orders else 0), True


def _give_up_on_run(rid: str, reason: str) -> None:
    """Terminal state a human can read: stage rows named, credit back, alert out.

    States only what was actually observed — the run's process stopped answering.
    A deploy is the common cause but not the only one (a crash, an OOM kill, or a
    host that went away look identical from here), so the message must not claim
    to know which."""
    message = ("Ajo jäi kesken: sen suorittanut prosessi ei enää vastannut "
               f"(deploy, kaatuminen tai palvelimen vaihtuminen). {reason}").strip()
    info = store.fail_stale_run(rid, message)
    refunded = bool(info and info["credit_refunded"])
    print(f"orphaned run {rid} NOT resumed: {reason} "
          f"(credit_refunded={refunded})", flush=True)
    detail = message + (" Generointikrediitti palautettu." if refunded else "")
    asyncio.create_task(_admin_notify(
        email_delivery.send_admin_run_failed(rid, reason=detail),
        rid, "orphaned-run alert",
    ))


def _recover_one(row: dict) -> None:
    rid = row["id"]
    run = store.get_run(rid)
    pipeline = store.get_pipeline(run["pipeline_id"]) if run else None
    if not run or not pipeline:
        _give_up_on_run(rid, "Ajon pipelinea ei löytynyt.")
        return

    resume_from, finished = _resume_point(run, pipeline)
    if not finished:
        # Guards apply only when real (paid) work is left. Pure finalization is
        # free, so it is always allowed to complete.
        if openrouter.runs_paused():
            _give_up_on_run(rid, "Generointi on keskeytetty (RUNS_PAUSED).")
            return
        attempts = int((run.get("params") or {}).get("_restart_attempts") or 0)
        if attempts >= RESUME_MAX_ATTEMPTS:
            _give_up_on_run(rid, f"Automaattinen jatko yritetty jo {attempts}×.")
            return
        cap_msg = runner._spend_cap_exceeded(rid)
        if cap_msg:
            _give_up_on_run(rid, cap_msg)
            return
        store.bump_restart_attempts(rid)

    # A forecast-mode run killed before stage 0 finished must park at
    # awaiting_forecast again, not run straight through to a report.
    params = run.get("params") or {}
    forecast_park = (
        bool(params.get("forecast_mode"))
        and resume_from == 0
        and not any(r.get("order", 0) >= 1 for r in (run.get("results") or []))
    )
    # Claim it before starting: a sibling container booting a second later then
    # sees a fresh heartbeat and leaves this run alone.
    store.touch_heartbeat(rid)
    if forecast_park:
        started = _start_bg(rid, only=0, completion_status="awaiting_forecast")
    else:
        started = _start_bg(rid, from_order=resume_from)
    if not started:
        _give_up_on_run(rid, "Taustatehtävää ei voitu käynnistää.")
        return
    print(f"orphaned run {rid} resumed from stage {resume_from} "
          f"(finalize_only={finished})", flush=True)


async def _recover_stale_runs() -> None:
    """Startup sweep: adopt runs whose heartbeat went quiet under a dead process.

    Staleness, not status, is the test — during a rolling deploy the previous
    container may still be generating, and its run must not be touched."""
    try:
        orphans = store.stale_running_runs(RESUME_STALE_SECONDS, RESUME_MAX_AGE_HOURS)
    except Exception as e:
        print(f"orphan sweep failed: {e}", flush=True)
        return
    if not orphans:
        return
    print(f"orphan sweep: {len(orphans)} run(s) left running by a dead process",
          flush=True)
    for row in orphans:
        try:
            _recover_one(row)
        except Exception as e:
            print(f"orphan recovery failed for {row.get('id')}: {e}", flush=True)


# ---- website orders (public intake; operator fulfils in this UI) -------------

@app.post("/api/orders")
def post_order(body: OrderIn, request: Request, background: BackgroundTasks):
    if body.website.strip():  # honeypot filled → bot; pretend success
        return {"ok": True}
    if not _rate_ok("order", _client_ip(request)):
        raise HTTPException(429, "liian monta tilausta — yritä myöhemmin uudelleen")
    company = body.company.strip()
    email = body.email.strip()
    user_input = body.user_input.strip() or None
    oid = store.create_order(company, email, user_input)
    # Backgrounded: intake must answer fast and must not 500 the customer's order
    # because SES is having a bad day. Past the honeypot/rate limit, so bots
    # never reach the inbox.
    background.add_task(_order_intake_alert, oid, company, email, user_input)
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
    _assert_generation_pipeline(body.pipeline_id)
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


async def _stream_progress(rid):
    """READ-ONLY progress stream over the persisted run state. This used to call
    runner.run_stages() directly, which meant a plain GET re-executed the whole
    paid pipeline (bypassing the _RUN_TASKS dedup and credit consumption) — a
    finished report could be re-run for free just by opening its stream URL.
    Execution now happens only via POST /start's background task."""
    terminal = {"ok", "error", "validation_failed", "awaiting_forecast"}
    last = None
    while True:
        run = store.get_run(rid)
        if not run:
            yield {"data": json.dumps({"step": "error", "message": "run not found"})}
            return
        snapshot = {
            "step": "progress",
            "status": run.get("status"),
            "stages": [{"order": r.get("order"), "name": r.get("name"),
                        "status": r.get("status")} for r in run.get("results") or []],
        }
        s = json.dumps(snapshot, ensure_ascii=False, sort_keys=True)
        if s != last:
            last = s
            yield {"data": s}
        if run.get("status") in terminal:
            return
        await asyncio.sleep(2)


@app.get("/api/runs/{rid}/stream")
async def stream_run(rid: str, request: Request):
    _require_run_access(rid, request)
    return EventSourceResponse(_stream_progress(rid))


# ---- background runner ------------------------------------------------------
# Drive a run to completion server-side, decoupled from any client connection.
# run_stages persists each stage result as it finishes, so the client just polls
# GET /api/runs/{rid}. This means a run survives the tab closing / backgrounding
# (e.g. iOS Safari) and the progress UI never depends on fetch-stream buffering.
_RUN_TASKS: dict[str, asyncio.Task] = {}


async def _drive_run(rid: str, only=None, from_order=None, completion_status=None):
    task_removed = False
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
        # Never swallow this silently: an exception here is the one failure mode
        # that leaves no stage row to explain itself, and it used to vanish
        # completely (run marked 'error', no traceback, no alert).
        print(f"run {rid} died in the background driver:\n"
              f"{traceback.format_exc()}", flush=True)
        try:
            store.set_run_status(rid, "error")
        except Exception:
            pass
    finally:
        try:
            final_run = store.get_run(rid)
            if (completion_status and final_run
                    and final_run.get("status") == "ok"):
                # Make continuation safe as soon as the UI sees the awaiting
                # state: remove the completed stage-0 task before publishing it.
                _RUN_TASKS.pop(rid, None)
                task_removed = True
                store.set_run_status(rid, completion_status)
                final_run = store.get_run(rid)
                # Paid forecast-mode run parked for review: email the buyer the
                # link so an opted-in customer who closed the success page still
                # gets their report. No-op without a delivery_email / access key.
                if completion_status == "awaiting_forecast":
                    try:
                        result = await email_delivery.send_forecast_ready(rid)
                        if isinstance(result, dict) and not result.get("sent", True):
                            print(f"forecast email for {rid} not sent: {result}", flush=True)
                    except Exception as e:
                        print(f"forecast email delivery failed for {rid}: {e}", flush=True)
            if final_run and final_run.get("status") == "ok":
                readiness = store.report_readiness(rid)
                if readiness["ready"]:
                    result = await email_delivery.send_report_ready(rid)
                    if isinstance(result, dict) and not result.get("sent", True):
                        print(f"report email for {rid} not sent: {result}", flush=True)
                        # "disabled" (kill switch) and "no-recipient" (admin run)
                        # are normal — alerting on them would mean an alert per
                        # run, and with the kill switch on it couldn't send anyway.
                        if result.get("reason") not in ("disabled", "no-recipient"):
                            await _admin_notify(
                                email_delivery.send_admin_delivery_failed(rid, result),
                                rid, "delivery failure",
                            )
                    else:
                        await _admin_notify(
                            email_delivery.send_admin_delivery_notice(rid),
                            rid, "delivery notice",
                        )
                else:
                    # Delivering a report that failed its hard checks to a paying
                    # client is worse than a late email — hold it and log why.
                    print(f"report email for {rid} HELD, readiness issues: "
                          f"{readiness['issues']}", flush=True)
                    await _admin_notify(
                        email_delivery.send_admin_report_held(rid, readiness["issues"]),
                        rid, "held-report alert",
                    )
            elif final_run and final_run.get("status") == "error":
                # Money taken, run dead, buyer waiting on an email that will never
                # arrive. send_admin_run_failed no-ops for runs with no customer.
                await _admin_notify(
                    email_delivery.send_admin_run_failed(rid), rid, "run-failure alert",
                )
        except Exception as e:
            print(f"report email delivery failed for {rid}: {e}", flush=True)
            # A throw here (not a provider error — _dispatch turns those into
            # sent=False) means the customer got nothing and the branches above
            # never ran. _admin_notify swallows a second failure.
            await _admin_notify(
                email_delivery.send_admin_delivery_failed(rid, {"reason": str(e)}),
                rid, "delivery failure",
            )
        if not task_removed:
            _RUN_TASKS.pop(rid, None)


def _start_bg(rid: str, only=None, from_order=None, completion_status=None) -> bool:
    # Last line of defence for the retired 6-stage pipeline: every execution
    # path (start, round2 — which clones the PARENT's pipeline_id — forecast
    # import, scoped rerun) funnels through here, so one check covers them all,
    # including restarts of the 16 historical runs that still point at it.
    run = store.get_run(rid)
    _assert_generation_pipeline((run or {}).get("pipeline_id"))
    task = _RUN_TASKS.get(rid)
    if task and not task.done():
        return False
    _RUN_TASKS[rid] = asyncio.create_task(
        _drive_run(
            rid, only=only, from_order=from_order,
            completion_status=completion_status,
        )
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
    reshape the locked business thesis and the scenarios. Forecast edits belong
    to the same included round; they first import a new ValuBuild model and then
    re-run from stage 0 against its fresh data."""
    _check_not_paused()
    parent = _require_run_access(rid, request)
    # Round-2 is credit-free, so cap refinements per report — otherwise one key
    # can spam unlimited Opus-priced full-report rewrites ($6-runs incident).
    # Family-wide count, not path depth or per-node children: the emailed
    # link targets the ROOT run (path depth 0 forever), and a fresh run has
    # 0 children of its own — either narrower measure lets rounds restart
    # uncounted from an older run in the same report family.
    max_r2 = int(os.getenv("ROUND2_MAX_PER_RUN") or 2)
    # Unlimited expert keys (generations_limit <= 0) skip the cap: they're
    # ours/CEO's, iterate-until-good is the point, and the per-run + daily
    # USD caps still bound the damage. Keyless/admin calls stay capped.
    caller_key = getattr(request.state, "access_key", None)
    key_row = store.get_access_key(caller_key) if caller_key else None
    uncapped = key_row is not None and (key_row["generations_limit"] or 0) <= 0
    # The cap gates BOTH branches before any import runs: a forecast edit must not
    # trigger a (paid, uncancellable) ValuBuild import if the round isn't free/paid.
    if not uncapped and store.refinement_count(rid) >= max_r2:
        raise HTTPException(
            429, f"Tarkennuskierrosten enimmäismäärä ({max_r2}) on jo käytetty "
                 "tälle raportille."
        )
    if body.forecast_edits:
        new_rid = await _start_forecast_import_round(
            rid, parent, body.forecast_edits,
            clarifications=body.clarifications,
            clarifications_free_text=body.clarifications_free_text,
            show_old_numbers=body.show_old_numbers,
            scenario_probabilities=body.scenario_probabilities,
        )
    else:
        new_rid = _start_refinement_round(
            rid, parent, body.clarifications, body.clarifications_free_text,
            show_old_numbers=body.show_old_numbers,
            scenario_probabilities=body.scenario_probabilities,
        )
    return {"run_id": new_rid, "parent_run_id": rid}


def _start_refinement_round(rid, parent, clarifications, clarifications_free_text,
                            show_old_numbers=False, scenario_probabilities=None,
                            forecast_edits=None, forecast_changes=None, new_fid=None) -> str:
    # Maximal-preserve: hand the round the prior enrichment + assembled report
    # so it refines (keep the good, apply the fix) instead of regenerating blind.
    prev_enrichment = next(
        (r.get("parsed_json") for r in (parent.get("results") or [])
         if r.get("order") == 1),
        None,
    )
    params = {
        "clarifications": [
            c.model_dump() if hasattr(c, "model_dump") else c for c in clarifications
        ],
        "clarifications_free_text": clarifications_free_text,
        "previous_enrichment": prev_enrichment,
        "previous_report": store.final_report_json(rid),
        "show_old_numbers": show_old_numbers,
        "scenario_probabilities": (
            scenario_probabilities.model_dump()
            if hasattr(scenario_probabilities, "model_dump")
            else scenario_probabilities
        ),
        # Careful preserve-and-patch is an editing task, not creative writing —
        # use Opus for the round-2 writer whatever round 1's writer happens to be.
        "round2_writer_model": ROUND2_WRITER_MODEL,
    }
    if new_fid is not None:
        # Forecast-edit round (ACE #3048): re-fid to the freshly imported model
        # and re-run from stage 0 (which refetches the edited fid's modeldata) with
        # the estimate-generation gate bypassed so the user's values survive.
        params["forecast_edits"] = forecast_edits
        params["forecast_changes"] = forecast_changes
        params["skip_estimate_generation"] = True
        new_rid = store.clone_run(rid, params=params, identifier=str(new_fid))
        _start_bg(new_rid, from_order=0)
        return new_rid
    new_rid = store.clone_run(rid, params=params)
    # NOTE: every clarifications-only refinement round MUST re-run stage 1
    # (from_order=1): the enrichment stage is where clarifications get folded in
    # (1_enrichment.txt KIERROS 2 -KURI) — the writer prompt has no
    # {{clarifications}} of its own, it consumes the corrected enrichment. That
    # stage is maximal-preserve + targeted search, so it's cheap (~$0.15), not a
    # full re-research.
    _start_bg(new_rid, from_order=1)
    return new_rid


# Variables a user may edit in v1 — mirrors the ValuBuild server-side allowlist
# (EstimateController.ALLOWED_VARNAMES). Kept in sync manually for now.
FORECAST_ALLOWED_VARNAMES = {"ns", "ebit"}
_FORECAST_LABELS = {"ns": "Liikevaihto", "ebit": "EBIT"}


def _validate_forecast_edits(edits):
    """Light server-side guard before the (paid, uncancellable) import. ValuBuild
    validates again authoritatively; this just rejects obvious garbage early."""
    if not edits:
        raise HTTPException(400, "forecast_edits ei saa olla tyhjä.")
    seen = set()
    for e in edits:
        varname = (e.varname or "").strip()
        if varname not in FORECAST_ALLOWED_VARNAMES:
            raise HTTPException(400, f"Tuntematon muuttuja: {e.varname!r}")
        if e.year < 2000 or e.year > 2100:
            raise HTTPException(400, f"Virheellinen ennustevuosi: {e.year}")
        key = (varname, e.year)
        if key in seen:
            raise HTTPException(400, f"Sama ennustesolu annettiin kahdesti: {varname} {e.year}")
        seen.add(key)
        if not math.isfinite(e.value):
            raise HTTPException(400, "Ennustearvon on oltava äärellinen luku.")
        if varname == "ns" and e.value <= 0:
            raise HTTPException(400, "Liikevaihdon (ns) on oltava positiivinen.")


def _forecast_change_summary(parent, edits) -> str:
    """Human-readable 'old → new' list for the writer context. Old values come
    from the parent run's stage-0 forecast block (tEUR); the edits are in millions
    (modeldata unit), so ×1000 to compare in the report's tEUR."""
    forecast = _extract_stage0_forecast(parent) or {}
    years = forecast.get("years") or []
    old_by_var = {"ns": forecast.get("net_sales") or [], "ebit": forecast.get("ebit") or []}

    def fmt(v):
        if not isinstance(v, (int, float)):
            return "?"
        return f"{v:,.0f}".replace(",", " ")

    lines = []
    for e in edits:
        varname = (e.varname or "").strip()
        label = _FORECAST_LABELS.get(varname, varname)
        new_teur = e.value * 1000
        old_teur = None
        if e.year in years:
            arr = old_by_var.get(varname) or []
            idx = years.index(e.year)
            if idx < len(arr):
                old_teur = arr[idx]
        lines.append(f"- {label} {e.year}: {fmt(old_teur)} → {fmt(new_teur)} tEUR")
    return "\n".join(lines) if lines else "(Ennusteita ei muutettu.)"


def _extract_stage0_forecast(run) -> dict | None:
    """Return a run's stage-0 forecast block without copying or unit changes."""
    stage0 = next(
        (result.get("parsed_json") for result in (run.get("results") or [])
         if result.get("order") == 0),
        None,
    )
    forecast = (stage0 or {}).get("forecast") if isinstance(stage0, dict) else None
    return forecast if isinstance(forecast, dict) else None


def _forecast_value_teur(forecast: dict, varname: str, year: int):
    years = forecast.get("years") or []
    series_name = {"ns": "net_sales", "ebit": "ebit"}.get(varname)
    values = forecast.get(series_name) or [] if series_name else []
    if year not in years:
        return None
    index = years.index(year)
    if index >= len(values):
        return None
    value = values[index]
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return value if math.isfinite(value) else None


def _forecast_preview_rows(forecast: dict, edits) -> list[dict]:
    """Validate edit cells against stage 0 and build tEUR comparison rows."""
    rows = []
    for edit in edits:
        old_teur = _forecast_value_teur(forecast, edit.varname, edit.year)
        if old_teur is None:
            raise HTTPException(
                400,
                f"Ennusteessa ei ole arvoa muuttujalle {edit.varname!r} vuodelle {edit.year}.",
            )
        rows.append({
            "varname": edit.varname,
            "year": edit.year,
            "old": old_teur,
            "value": edit.value * 1000,
        })
    return rows


@app.post("/api/runs/{rid}/forecast-preview")
async def forecast_preview(rid: str, body: ForecastPreviewIn, request: Request):
    """Interpret free text into a safe, non-importing forecast proposal.

    ``edits[].value`` follows the ValuBuild contract (millions). The comparison
    ``rows`` use tEUR, matching the stage-0 forecast shown by the UI.
    """
    _check_not_paused()
    run = _require_run_access(rid, request)
    text = body.text.strip()
    if not text:
        raise HTTPException(400, "Ennusteiden muokkauspyyntö ei saa olla tyhjä.")
    forecast = _extract_stage0_forecast(run)
    if not forecast:
        raise HTTPException(400, "Runilta puuttuu stage 0:n ennustedata.")
    if not _rate_ok("forecast_preview", _client_ip(request)):
        raise HTTPException(429, "Liian monta ennusteiden esikatselupyyntöä. Yritä hetken kuluttua.")

    try:
        proposal = await forecast_interpret.interpret(text, forecast)
    except forecast_interpret.ForecastInterpretError as exc:
        raise HTTPException(502, str(exc))
    try:
        edits = [ForecastEdit.model_validate(edit) for edit in proposal["edits"]]
    except (KeyError, TypeError, ValidationError) as exc:
        raise HTTPException(502, "AI-tulkinta palautti virheellisiä ennustemuutoksia.") from exc

    _validate_forecast_edits(edits)
    rows = _forecast_preview_rows(forecast, edits)

    notes = list(proposal.get("notes") or [])
    notes.extend(forecast_interpret.magnitude_notes(forecast, edits))
    notes = list(dict.fromkeys(notes))
    return {
        "edits": [edit.model_dump() for edit in edits],
        "summary": proposal.get("summary") or "",
        "rows": rows,
        "notes": notes,
    }


@app.post("/api/runs/{rid}/fetch-forecast")
async def fetch_forecast(rid: str, request: Request):
    """Run only stage 0 and stop in the round-1 forecast editing state."""
    _check_not_paused()
    run = _require_run_access(rid, request)
    if any(result.get("order", 0) >= 1 for result in (run.get("results") or [])):
        raise HTTPException(
            409,
            "Raportin kirjoitusvaiheet on jo aloitettu; käytä maksullista tarkennuskierrosta.",
        )
    forecast = _extract_stage0_forecast(run)
    if run.get("status") == "awaiting_forecast" and forecast:
        return {
            "run_id": rid,
            "started": False,
            "status": "awaiting_forecast",
            "forecast": forecast,
        }
    if run.get("status") == "importing_forecast":
        raise HTTPException(409, "Ennustemuutosten tuonti on jo käynnissä.")
    started = _start_bg(rid, only=0, completion_status="awaiting_forecast")
    return {
        "run_id": rid,
        "started": started,
        "status": "running" if started else run.get("status"),
        "forecast": None,
    }


@app.post("/api/runs/{rid}/generate-forecast")
async def generate_forecast(rid: str, body: GenerateForecastIn, request: Request):
    """Continue an awaiting round-1 run, optionally after a forecast import."""
    _check_not_paused()
    run = _require_run_access(rid, request)
    if run.get("status") != "awaiting_forecast":
        raise HTTPException(409, "Run ei odota ennusteiden hyväksyntää.")
    forecast = _extract_stage0_forecast(run)
    if not forecast:
        raise HTTPException(400, "Runilta puuttuu stage 0:n ennustedata.")
    task = _RUN_TASKS.get(rid)
    if task and not task.done():
        raise HTTPException(409, "Runin taustatehtävä on vielä käynnissä.")

    edits = body.forecast_edits or []
    if not edits:
        store.set_run_status(rid, "running")
        started = _start_bg(rid, from_order=1)
        if not started:
            store.set_run_status(rid, "awaiting_forecast")
            raise HTTPException(409, "Raportin generointi on jo käynnissä.")
        return {"run_id": rid, "started": True, "forecast_edited": False}

    _validate_forecast_edits(edits)
    _forecast_preview_rows(forecast, edits)
    try:
        base_fid = int(str(run.get("identifier")).strip())
    except (TypeError, ValueError):
        raise HTTPException(
            400, "Ennusteiden muokkaus vaatii FID-pohjaisen runin (ei liitettyä FAKTAT-dataa)."
        )
    payload = [
        {"varname": (edit.varname or "").strip(), "year": edit.year, "value": edit.value}
        for edit in edits
    ]

    # Claim the awaiting run synchronously before the first await. A concurrent
    # duplicate request then sees importing_forecast and cannot launch a second
    # paid, uncancellable ValuBuild import.
    store.set_run_status(rid, "importing_forecast")
    try:
        new_fid = await forecast_import.import_and_wait(base_fid, payload)
    except forecast_import.ForecastImportError as exc:
        store.set_run_status(rid, "awaiting_forecast")
        raise HTTPException(502, str(exc))
    except asyncio.CancelledError:
        store.set_run_status(rid, "awaiting_forecast")
        raise
    except Exception:
        store.set_run_status(rid, "awaiting_forecast")
        raise

    params = dict(run.get("params") or {})
    params.update({
        "forecast_edits": payload,
        "forecast_changes": _forecast_change_summary(run, edits),
        "skip_estimate_generation": True,
    })
    store.rebind_run_forecast(rid, new_fid, params)
    started = _start_bg(rid, from_order=0)
    if not started:
        store.set_run_status(rid, "error")
        raise HTTPException(409, "Raportin generointia ei voitu käynnistää.")
    return {
        "run_id": rid,
        "started": True,
        "forecast_edited": True,
        "identifier": str(new_fid),
    }


async def _start_forecast_import_round(rid, parent, edits, clarifications=None,
                                       clarifications_free_text="",
                                       show_old_numbers=False,
                                       scenario_probabilities=None) -> str:
    """Import the user's forecast edits into a new ValuBuild fid, then start a
    refinement round re-fid'd to it (stage 0 onward, gate bypassed). Any
    clarification answers submitted alongside the edits ride along — the round
    re-runs stage 1 anyway, which is where they get folded in."""
    _validate_forecast_edits(edits)
    try:
        parent_fid = int(str(parent.get("identifier")).strip())
    except (TypeError, ValueError):
        raise HTTPException(
            400, "Ennusteiden muokkaus vaatii FID-pohjaisen runin (ei liitettyä FAKTAT-dataa)."
        )
    payload = [
        {"varname": (e.varname or "").strip(), "year": e.year, "value": e.value}
        for e in edits
    ]
    try:
        new_fid = await forecast_import.import_and_wait(parent_fid, payload)
    except forecast_import.ForecastImportError as exc:
        # Import failed → no round starts, no quota/lineage consumed.
        raise HTTPException(502, str(exc))
    summary = _forecast_change_summary(parent, edits)
    return _start_refinement_round(
        rid, parent, clarifications or [], clarifications_free_text or "",
        show_old_numbers=show_old_numbers,
        scenario_probabilities=scenario_probabilities,
        forecast_edits=payload, forecast_changes=summary, new_fid=new_fid,
    )


@app.post("/api/runs/{rid}/round2/checkout")
async def round2_checkout(rid: str, body: Round2In, request: Request):
    """Round 3+ isn't free — create a Stripe Checkout Session for one paid
    extra refinement. The clarification answers are staged server-side
    (Stripe metadata is far too small to hold them); metadata only carries a
    lookup token, redeemed by round2_redeem after payment succeeds."""
    _require_run_access(rid, request)
    if not STRIPE_SECRET_KEY:
        raise HTTPException(503, "Lisäkierrosten maksut eivät ole vielä käytössä.")
    # Validate forecast edits before taking payment — a bad edit must fail here,
    # not after the user has paid for a round that can't run.
    if body.forecast_edits:
        _validate_forecast_edits(body.forecast_edits)
    key = getattr(request.state, "access_key", None)
    token = store.create_pending_round(
        rid, key, [c.model_dump() for c in body.clarifications], body.clarifications_free_text,
        scenario_probabilities=(
            body.scenario_probabilities.model_dump()
            if body.scenario_probabilities else None
        ),
        forecast_edits=(
            [e.model_dump() for e in body.forecast_edits] if body.forecast_edits else None
        ),
    )
    site = (os.getenv("CLIENT_SITE_URL") or "").rstrip("/")
    key_q = f"&key={key}" if key else ""
    success_url = (
        f"{site}{email_delivery.REPORT_PATH}?rid={rid}{key_q}&paid_round_token={token}"
        f"&show_old_numbers={1 if body.show_old_numbers else 0}"
        "&session_id={CHECKOUT_SESSION_ID}"
    )
    cancel_url = f"{site}{email_delivery.REPORT_PATH}?rid={rid}{key_q}"
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
    # Claim the token atomically (guards a concurrent double-redeem), but roll
    # the claim back if the round fails to start: the forecast branch calls
    # ValuBuild AFTER the payment check and can fail or time out there, and a
    # burned token would mean money taken with no round and no way to retry.
    if not store.claim_pending_round(body.token):
        raise HTTPException(409, "Tämä tarkennuskierros on jo käytetty.")
    parent = store.get_run(rid)
    forecast_edits = pending.get("forecast_edits")
    try:
        if forecast_edits:
            # Same two-branch logic as round2_run: paid forecast-edit round imports a
            # new fid and re-runs from stage 0. Rebuild typed edits from the staged
            # dicts; staged clarification answers ride along like in round2_run.
            edits = [ForecastEdit(**e) for e in forecast_edits]
            new_rid = await _start_forecast_import_round(
                rid, parent, edits,
                clarifications=pending["clarifications"],
                clarifications_free_text=pending["clarifications_free_text"],
                show_old_numbers=body.show_old_numbers,
                scenario_probabilities=pending.get("scenario_probabilities"),
            )
        else:
            new_rid = _start_refinement_round(
                rid, parent, pending["clarifications"], pending["clarifications_free_text"],
                show_old_numbers=body.show_old_numbers,
                scenario_probabilities=pending.get("scenario_probabilities"),
            )
    except BaseException:
        # BaseException: a client disconnect cancels this handler mid-import
        # (CancelledError), and the paid token must survive that too.
        store.release_pending_round(body.token)
        raise
    return {"run_id": new_rid, "parent_run_id": rid}


async def _stream_execute(rid, only=None, from_order=None):
    """Execute stages and stream events. Admin-only POST rerun endpoints —
    the public GET /stream must never reach this (it re-ran paid pipelines)."""
    run = _run_with_params(rid)
    if not run:
        raise HTTPException(404, "run not found")
    p = store.get_pipeline(run["pipeline_id"])
    store.set_run_status(rid, "running")
    async for event in runner.run_stages(
        run, p["stages"], only=only, from_order=from_order
    ):
        yield {"data": json.dumps(event, ensure_ascii=False)}


@app.post("/api/runs/{rid}/stages/{order}/rerun")
async def rerun_stage(rid: str, order: int):
    return EventSourceResponse(_stream_execute(rid, only=order))


@app.post("/api/runs/{rid}/stages/{order}/rerun-from")
async def rerun_from(rid: str, order: int):
    return EventSourceResponse(_stream_execute(rid, from_order=order))


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
    run = _require_run_access(rid, request)
    # Emailed links target the run that finished when the mail was sent; after
    # a browser-side refinement that's stale. Tell the client where the newest
    # family member is so it can jump there instead of re-showing an old report.
    latest = store.latest_family_run_id(rid)
    if latest and latest != rid:
        run = {**run, "latest_run_id": latest}
    return run


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


@app.patch("/api/access-keys/{key}")
def patch_access_key(key: str, body: AccessKeyLimitIn):
    """Admin-only: change a key's quota in place (e.g. upgrade a checkout-minted
    1-credit key to unlimited so its existing report chain keeps working)."""
    row = store.set_access_key_limit(key, body.generations_limit)
    if not row:
        raise HTTPException(404, "key not found")
    return dict(row)


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
        # Without Stripe configured, /round2/checkout 503s. Tell the client up
        # front so it stops offering a "buy an extra round" button that can only
        # dead-end in a red error once the free rounds run out.
        "paid_rounds_enabled": bool(STRIPE_SECRET_KEY),
        "free_rounds_per_report": int(os.getenv("ROUND2_MAX_PER_RUN") or 2),
        "remaining": None if unlimited else max(0, limit - row["generations_used"]),
    }


def _assert_generation_pipeline(pipeline_id: str) -> str:
    """Only single-writer pipelines may start a NEW report run.

    The retired 6-stage pipeline still exists in the database (16 historical
    runs reference it), and list_pipelines() is oldest-first — so it is
    pipelines[0]. Any silent fallback to "the first pipeline", or an explicit
    pipeline_id from a caller, could put it back in front of a paying customer.
    It is retired: refuse rather than generate through it.
    """
    p = store.get_pipeline(pipeline_id) if pipeline_id else None
    if not p:
        raise HTTPException(404, "pipeline not found")
    if not str(p.get("name") or "").startswith(seed.SINGLE_WRITER_PIPELINE_PREFIX):
        raise HTTPException(
            400,
            f"Pipeline {p.get('name')!r} on poistettu käytöstä eikä sillä voi "
            "ajaa uusia raportteja. Käytä yhden kirjoittajan pipelineä.",
        )
    return pipeline_id


def _default_pipeline_id(pipeline_id: str | None) -> str:
    # Self-serve runs go through the single-writer pipeline (FAKTAT +
    # enrichment + one writer). Callers may pass an explicit pipeline_id (the
    # admin/operator UI does), but it is validated the same way — and there is
    # deliberately NO "first pipeline" fallback (see _assert_generation_pipeline).
    if pipeline_id:
        return _assert_generation_pipeline(pipeline_id)
    sw = next((p for p in store.list_pipelines() or []
               if p.get("name") == seed.SINGLE_WRITER_PIPELINE_NAME), None)
    if not sw:
        raise HTTPException(503, "Oletuspipelineä ei löydy — aja /api/reseed.")
    return sw["id"]


def _create_generation_run(
    *, fid: int, company_name: str, company_code=None, industry_text=None,
    industry_code=None, industry_id=None, industry_tree=None,
    delivery_email=None, user_input="", pipeline_id=None, access_key=None,
    forecast_mode=False,
) -> str:
    """Shared by the invite-key expert flow and the public paid checkout flow:
    build the stage-0 params and kick off a background run. Forecast mode stops
    after stage 0 so the same purchased round can be edited before writing."""
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
    if forecast_mode:
        # Exposed so the UI can tell the ~1 min forecast data-fetch phase apart
        # from the 10-20 min report generation and show the right progress copy.
        params["forecast_mode"] = True
    rid = store.create_run(
        pid, None, True, identifier=str(fid), params=params, access_key=access_key,
    )
    if forecast_mode:
        _start_bg(rid, only=0, completion_status="awaiting_forecast")
    else:
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
        forecast_mode=body.mode == "forecast",
    )
    return {"run_id": rid, "mode": body.mode}


def _pick_checkout_candidate(candidates: list[dict]) -> dict | None:
    """Automated FID pick for the unattended checkout flow (no human in the
    loop to use the /raportti disambiguation picker). Prefer the "Profinder"
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


def _checkout_candidate(candidates: list[dict], requested_fid: int | None) -> dict | None:
    """Pick the model the buyer actually paid for.

    The client site already knows the exact fid — it is what the search row the
    buyer clicked was built from — so honour it instead of re-guessing. The
    membership check IS the validation: `fid` reaches us through Stripe metadata,
    i.e. from the client, and `candidates` are exactly the models `business_id`
    resolves to, so a forged fid can never point the run at another company.

    Without this, a konserni purchase fell through to `_pick_checkout_candidate`,
    which prefers the NON-K parent row: "St1 Nordic Oy – Konserni" was bought and
    emo figures were delivered (2026-08-03). Nothing downstream could recover —
    `meta.level` follows the fetched model, and a round-2 refinement never
    re-runs stage 0.
    """
    if requested_fid is not None:
        for c in candidates:
            if c.get("fid") == requested_fid:
                return c
        # Not fatal (fall back to the heuristic below), but it means the buyer's
        # choice was dropped — the exact condition that produced the emo/konserni
        # mixup, so it must be visible in the logs rather than silent.
        print(
            f"checkout: requested fid {requested_fid} is not among the models for "
            f"this business_id ({[c.get('fid') for c in candidates]}) — falling "
            "back to the heuristic pick",
            flush=True,
        )
    return _pick_checkout_candidate(candidates)


_CHECKOUT_LOCKS: dict[str, asyncio.Lock] = {}


@app.post("/api/public/checkout-generate")
async def public_checkout_generate(body: CheckoutGenerateIn, request: Request):
    """Public, unauthenticated: called by the client site's Stripe success page
    right after payment is verified server-side. Resolves the paid company to a
    Valuatum FID, mints a single-use access key, and starts generation — closing
    the "operator fulfils manually" gap for the self-serve paid flow. Same
    honeypot + IP rate limit as /api/orders; idempotent on stripe_session_id so
    a page reload after payment doesn't double-generate or double-mint a key.
    Only a SUCCESSFUL prior run is reused this way — a failed one (e.g. the
    Turun Tislaamo $4 spend-cap trip) is retried fresh instead of permanently
    stuck: the demo flow's session id is deterministic on (company, email),
    so without this check the same person retrying the same company would be
    handed the same dead run forever, no matter how many times they tried."""
    if body.website.strip():  # honeypot filled → bot; pretend success
        return {"ok": True}
    if not _rate_ok("checkout", _client_ip(request)):
        raise HTTPException(429, "liian monta tilausta — yritä myöhemmin uudelleen")
    # With Stripe configured, the claimed session must be a real PAID checkout
    # session — the endpoint is unauthenticated, so without this check anyone
    # could start a paid pipeline with a made-up session id. Demo mode (no
    # Stripe key) deliberately skips this: the whole flow is a free demo then.
    if STRIPE_SECRET_KEY:
        try:
            session = await _stripe_get_checkout_session(
                body.stripe_session_id, with_line_items=True
            )
        except httpx.HTTPStatusError:
            raise HTTPException(402, "Maksua ei voitu vahvistaa.")
        if session.get("payment_status") != "paid":
            raise HTTPException(402, "Maksua ei voitu vahvistaa.")
        # Paid, but paid for WHAT? Every product in the Stripe account produces
        # paid sessions, and this endpoint is unauthenticated — "paid" alone let
        # a luottoriskit.fi credit-report purchase start an arvonmääritys run.
        if not _is_valuation_session(session):
            print(
                "checkout-generate: refusing session "
                f"{body.stripe_session_id[:20]} — paid, but not an arvonmääritys "
                f"purchase (metadata={json.dumps(session.get('metadata') or {})[:300]})",
                flush=True,
            )
            raise HTTPException(403, "Tämä maksu ei koske arvonmääritysraporttia.")
    # Serialize per session id: two identical requests racing past the
    # check-then-insert used to be able to start two paid runs.
    # ponytail: in-process lock — single-instance deploy; a DB claim if we scale out.
    lock = _CHECKOUT_LOCKS.setdefault(body.stripe_session_id[:128], asyncio.Lock())
    async with lock:
        existing = store.get_order_by_session(body.stripe_session_id)
        if existing:
            existing_run = store.get_run(existing.get("run_id") or "")
            if not existing_run or existing_run.get("status") != "error":
                return {"run_id": existing.get("run_id"), "key": existing.get("access_key")}
        _check_not_paused()
        try:
            candidates = await valuatum.search_company(body.business_id)
        except httpx.HTTPStatusError as e:
            raise HTTPException(e.response.status_code, e.response.text[:500])
        except RuntimeError as e:
            raise HTTPException(500, str(e))
        candidate = _checkout_candidate(candidates, body.fid)
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
            forecast_mode=body.mode == "forecast",
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
