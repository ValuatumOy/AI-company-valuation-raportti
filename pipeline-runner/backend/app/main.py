"""FastAPI app. The OpenRouter key lives here, never in the browser."""
import json
import os

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse
from sse_starlette.sse import EventSourceResponse

load_dotenv()

from . import openrouter, report, runner, seed, store, validators, valuatum  # noqa: E402
from .models import (  # noqa: E402
    CompareIn, FetchIn, PipelineIn, ReorderIn, RunIn, StageIn, ValidateIn,
    ValuatumExportIn,
)
from fetchers.company_data import fetch_company_data  # noqa: E402

app = FastAPI(title="Valuation Pipeline Runner")

_origins = os.getenv("ALLOWED_ORIGINS", "*")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"] if _origins == "*" else [o.strip() for o in _origins.split(",")],
    allow_methods=["*"],
    allow_headers=["*"],
)

# Shared-token gate. If APP_TOKEN is unset (local dev) auth is disabled.
# When set, every /api/* call (except /api/health) needs:
#   Authorization: Bearer <APP_TOKEN>
_APP_TOKEN = os.getenv("APP_TOKEN", "")


@app.middleware("http")
async def auth_gate(request, call_next):
    if _APP_TOKEN and request.method != "OPTIONS":
        path = request.url.path
        if path.startswith("/api/") and path != "/api/health":
            sent = request.headers.get("authorization", "")
            if sent != f"Bearer {_APP_TOKEN}":
                from fastapi.responses import JSONResponse

                return JSONResponse({"detail": "unauthorized"}, status_code=401)
    return await call_next(request)


@app.get("/api/health")
def health():
    from . import db

    return {
        "ok": True,
        "auth": bool(_APP_TOKEN),
        "db": "postgres" if db.IS_PG else "sqlite",
    }


@app.on_event("startup")
async def _startup():
    seed.ensure_seeded()
    await openrouter.refresh_models()


# ---- pipelines / stages -----------------------------------------------------

@app.get("/api/pipelines")
def get_pipelines():
    return store.list_pipelines()


@app.post("/api/pipelines")
def post_pipeline(body: PipelineIn):
    return store.create_pipeline(body.name)


@app.post("/api/reseed")
def post_reseed():
    return seed.reseed_defaults(force=True)


@app.get("/api/pipelines/{pid}")
def get_pipeline(pid: str):
    p = store.get_pipeline(pid)
    if not p:
        raise HTTPException(404, "pipeline not found")
    return p


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
            yield {"data": json.dumps(ev, ensure_ascii=False)}

    return EventSourceResponse(gen())


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
def post_run(body: RunIn):
    p = store.get_pipeline(body.pipeline_id)
    if not p:
        raise HTTPException(404, "pipeline not found")
    rid = store.create_run(body.pipeline_id, body.input_data, body.stop_on_failure)
    # stash transient run-time params for the stream handler
    _RUN_PARAMS[rid] = {
        "identifier": body.identifier,
        "params": body.params,
    }
    return {"run_id": rid}


_RUN_PARAMS: dict[str, dict] = {}


def _run_with_params(rid):
    run = store.get_run(rid)
    if run:
        run.update(_RUN_PARAMS.get(rid, {}))
    return run


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
async def stream_run(rid: str):
    return EventSourceResponse(_stream(rid))


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


@app.get("/api/runs/{rid}/report-source")
def report_source(rid: str):
    j = store.final_report_json(rid)
    if j is None:
        raise HTTPException(400, "ei valmista loppuvaiheen JSONia tälle ajolle")
    return j


@app.get("/api/runs/{rid}/report.html")
def report_html(rid: str):
    j = store.final_report_json(rid)
    if j is None:
        raise HTTPException(400, "ei valmista loppuvaiheen JSONia tälle ajolle")
    try:
        path = report.generate_html(rid, j)
    except Exception as e:
        raise HTTPException(500, str(e))
    return HTMLResponse(open(path, encoding="utf-8").read())


@app.get("/api/runs/{rid}/report.pdf")
def report_pdf(rid: str):
    j = store.final_report_json(rid)
    if j is None:
        raise HTTPException(400, "ei valmista loppuvaiheen JSONia tälle ajolle")
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
def get_run(rid: str):
    r = store.get_run(rid)
    if not r:
        raise HTTPException(404, "run not found")
    return r


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
