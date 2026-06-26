"""CRUD over SQLite. Single source of truth for pipelines/stages/runs/results."""
import uuid
from datetime import datetime, timezone

from . import db


def _now():
    return datetime.now(timezone.utc).isoformat()


def _uuid():
    return uuid.uuid4().hex


# ---- pipelines / stages -----------------------------------------------------

def _stage_row_to_dict(r):
    return {
        "id": r["id"],
        "pipeline_id": r["pipeline_id"],
        "order": r["order"],
        "name": r["name"],
        "enabled": bool(r["enabled"]),
        "model": r["model"],
        "prompt_template": r["prompt_template"],
        "temperature": r["temperature"],
        "max_tokens": r["max_tokens"],
        "reasoning_effort": r["reasoning_effort"],
        "expects_json": bool(r["expects_json"]),
        "validator_code": r["validator_code"],
        "input_mapping": db.jload(r["input_mapping"]) or {},
    }


def get_pipeline(pid):
    p = db.query_one("SELECT * FROM pipelines WHERE id=?", (pid,))
    if not p:
        return None
    stages = db.query(
        'SELECT * FROM stages WHERE pipeline_id=? ORDER BY "order"', (pid,)
    )
    p["stages"] = [_stage_row_to_dict(s) for s in stages]
    return p


def list_pipelines():
    return [get_pipeline(p["id"]) for p in db.query("SELECT id FROM pipelines")]


def create_pipeline(name):
    pid = _uuid()
    db.execute(
        "INSERT INTO pipelines(id,name,created_at,updated_at) VALUES(?,?,?,?)",
        (pid, name, _now(), _now()),
    )
    return get_pipeline(pid)


def touch_pipeline(pid):
    db.execute("UPDATE pipelines SET updated_at=? WHERE id=?", (_now(), pid))


def add_stage(pid, s: dict):
    sid = _uuid()
    db.execute(
        'INSERT INTO stages(id,pipeline_id,"order",name,enabled,model,'
        "prompt_template,temperature,max_tokens,reasoning_effort,expects_json,"
        "validator_code,input_mapping) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            sid, pid, s["order"], s["name"], int(s.get("enabled", True)),
            s["model"], s.get("prompt_template", ""), s.get("temperature", 0.2),
            s.get("max_tokens", 16000), s.get("reasoning_effort"),
            int(s.get("expects_json", True)), s.get("validator_code"),
            db.jdump(s.get("input_mapping", {})),
        ),
    )
    touch_pipeline(pid)
    return get_stage(sid)


def get_stage(sid):
    r = db.query_one("SELECT * FROM stages WHERE id=?", (sid,))
    return _stage_row_to_dict(r) if r else None


def update_stage(sid, s: dict):
    cur = get_stage(sid)
    if not cur:
        return None
    merged = {**cur, **s}
    db.execute(
        'UPDATE stages SET "order"=?,name=?,enabled=?,model=?,prompt_template=?,'
        "temperature=?,max_tokens=?,reasoning_effort=?,expects_json=?,"
        "validator_code=?,input_mapping=? WHERE id=?",
        (
            merged["order"], merged["name"], int(merged["enabled"]),
            merged["model"], merged["prompt_template"], merged["temperature"],
            merged["max_tokens"], merged["reasoning_effort"],
            int(merged["expects_json"]), merged["validator_code"],
            db.jdump(merged.get("input_mapping", {})), sid,
        ),
    )
    touch_pipeline(cur["pipeline_id"])
    return get_stage(sid)


def delete_stage(sid):
    cur = get_stage(sid)
    db.execute("DELETE FROM stages WHERE id=?", (sid,))
    if cur:
        touch_pipeline(cur["pipeline_id"])


def reorder(pid, stage_ids):
    # stage 0 (fetcher) keeps order 0; the provided list maps to 1..N.
    n = 1
    for sid in stage_ids:
        st = get_stage(sid)
        if st and st["order"] == 0:
            continue
        db.execute('UPDATE stages SET "order"=? WHERE id=?', (n, sid))
        n += 1
    touch_pipeline(pid)
    return get_pipeline(pid)


# ---- runs / results ---------------------------------------------------------

def create_run(pid, input_data, stop_on_failure):
    rid = _uuid()
    db.execute(
        "INSERT INTO runs(id,pipeline_id,input_data,status,stop_on_failure,"
        "total_cost_usd,created_at) VALUES(?,?,?,?,?,?,?)",
        (rid, pid, db.jdump(input_data), "running", int(stop_on_failure), 0.0, _now()),
    )
    return rid


def set_run_status(rid, status):
    db.execute("UPDATE runs SET status=? WHERE id=?", (status, rid))


def add_run_cost(rid, delta):
    db.execute(
        "UPDATE runs SET total_cost_usd = total_cost_usd + ? WHERE id=?", (delta, rid)
    )


def upsert_result(rid, res: dict):
    existing = db.query_one(
        'SELECT id FROM stage_results WHERE run_id=? AND "order"=?',
        (rid, res["order"]),
    )
    fields = dict(
        run_id=rid,
        stage_id=res.get("stage_id", ""),
        order=res["order"],
        name=res.get("name", ""),
        status=res.get("status", "pending"),
        request_payload=db.jdump(res.get("request_payload")),
        raw_response=res.get("raw_response"),
        parsed_json=db.jdump(res.get("parsed_json")),
        validator_passed=(
            None if res.get("validator_passed") is None
            else int(res["validator_passed"])
        ),
        validator_report=db.jdump(res.get("validator_report")),
        tokens_prompt=res.get("tokens_prompt", 0),
        tokens_completion=res.get("tokens_completion", 0),
        cost_usd=res.get("cost_usd", 0.0),
        latency_ms=res.get("latency_ms", 0),
        finish_reason=res.get("finish_reason"),
        error_message=res.get("error_message"),
        started_at=res.get("started_at"),
        finished_at=res.get("finished_at"),
    )
    if existing:
        cols = ",".join(f'"{k}"=?' for k in fields if k not in ("run_id", "order"))
        vals = [v for k, v in fields.items() if k not in ("run_id", "order")]
        db.execute(
            f'UPDATE stage_results SET {cols} WHERE run_id=? AND "order"=?',
            (*vals, rid, res["order"]),
        )
    else:
        fields_id = {"id": _uuid(), **fields}
        cols = ",".join(f'"{k}"' for k in fields_id)
        ph = ",".join("?" for _ in fields_id)
        db.execute(
            f"INSERT INTO stage_results({cols}) VALUES({ph})",
            tuple(fields_id.values()),
        )


def _result_row(r):
    return {
        "stage_id": r["stage_id"],
        "run_id": r["run_id"],
        "order": r["order"],
        "name": r["name"],
        "status": r["status"],
        "request_payload": db.jload(r["request_payload"]),
        "raw_response": r["raw_response"],
        "parsed_json": db.jload(r["parsed_json"]),
        "validator_passed": (
            None if r["validator_passed"] is None else bool(r["validator_passed"])
        ),
        "validator_report": db.jload(r["validator_report"]),
        "tokens_prompt": r["tokens_prompt"],
        "tokens_completion": r["tokens_completion"],
        "cost_usd": r["cost_usd"],
        "latency_ms": r["latency_ms"],
        "finish_reason": r["finish_reason"],
        "error_message": r["error_message"],
        "started_at": r["started_at"],
        "finished_at": r["finished_at"],
    }


def get_run(rid):
    run = db.query_one("SELECT * FROM runs WHERE id=?", (rid,))
    if not run:
        return None
    run["input_data"] = db.jload(run["input_data"])
    run["stop_on_failure"] = bool(run["stop_on_failure"])
    results = db.query(
        'SELECT * FROM stage_results WHERE run_id=? ORDER BY "order"', (rid,)
    )
    run["results"] = [_result_row(r) for r in results]
    return run


def list_runs(limit=50):
    rows = db.query(
        "SELECT id,pipeline_id,status,total_cost_usd,created_at "
        "FROM runs ORDER BY created_at DESC LIMIT ?",
        (limit,),
    )
    return rows


def final_report_json(rid):
    """The JSON to feed the report generator: assembled wrapper + sections."""
    from . import assemble

    return assemble.assemble(get_run(rid))


def costs_summary(limit=200):
    """Per-run, per-model and grand-total cost aggregation across all runs."""
    runs = db.query(
        "SELECT id,status,total_cost_usd,created_at FROM runs "
        "ORDER BY created_at DESC LIMIT ?",
        (limit,),
    )
    by_model: dict[str, dict] = {}
    grand = 0.0
    out_runs = []
    for r in runs:
        sr = db.query(
            'SELECT "order",name,status,cost_usd,tokens_prompt,tokens_completion '
            "FROM stage_results WHERE run_id=?",
            (r["id"],),
        )
        rtotal = sum(s["cost_usd"] or 0 for s in sr)
        grand += rtotal
        out_runs.append({
            "id": r["id"],
            "status": r["status"],
            "created_at": r["created_at"],
            "total_cost_usd": rtotal,
            "stage_count": len(sr),
        })
    # per-model needs the model id, which lives on stages — join through runs.
    rows = db.query(
        "SELECT st.model AS model, sr.cost_usd AS cost, "
        "sr.tokens_prompt AS tp, sr.tokens_completion AS tc "
        "FROM stage_results sr JOIN stages st ON st.id = sr.stage_id"
    )
    for row in rows:
        m = row["model"] or "?"
        agg = by_model.setdefault(
            m, {"model": m, "cost_usd": 0.0, "tokens_prompt": 0,
                "tokens_completion": 0, "calls": 0}
        )
        agg["cost_usd"] += row["cost"] or 0
        agg["tokens_prompt"] += row["tp"] or 0
        agg["tokens_completion"] += row["tc"] or 0
        agg["calls"] += 1
    return {
        "grand_total_usd": grand,
        "by_model": sorted(by_model.values(), key=lambda x: -x["cost_usd"]),
        "runs": out_runs,
    }
