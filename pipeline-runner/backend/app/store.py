"""CRUD over SQLite. Single source of truth for pipelines/stages/runs/results."""
import uuid
from datetime import datetime, timedelta, timezone

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
        "web_search": bool(r.get("web_search")),
        "validator_code": r["validator_code"],
        "input_mapping": db.jload(r["input_mapping"]) or {},
    }


# Everything a stage row carries except the two big text columns, plus the one
# fact the boot check needs from `prompt_template` — is it still a placeholder —
# computed in SQL so the body never crosses the wire. The LIKE pattern is bound,
# not inlined: psycopg parses % in the SQL text as a placeholder marker, so a
# literal '%PROMPTI TÄHÄN%' raises "only '%s', '%b', '%t' are allowed as
# placeholders, got '%P'" on Postgres while passing silently on SQLite.
_LIGHT_COLUMNS = (
    'SELECT id, pipeline_id, "order", name, enabled, model, temperature,'
    ' max_tokens, reasoning_effort, expects_json, web_search, input_mapping,'
    ' (prompt_template LIKE ?) AS is_placeholder FROM stages'
)
_PLACEHOLDER_LIKE = "%PROMPTI TÄHÄN%"


def _light_stage(row):
    return {**_stage_row_to_dict({**row, "prompt_template": None,
                                  "validator_code": None}),
            "is_placeholder": bool(row["is_placeholder"])}


def get_pipeline(pid, light=False):
    p = db.query_one("SELECT * FROM pipelines WHERE id=?", (pid,))
    if not p:
        return None
    if light:
        stages = db.query(
            _LIGHT_COLUMNS + ' WHERE pipeline_id=? ORDER BY "order"',
            (_PLACEHOLDER_LIKE, pid),
        )
        p["stages"] = [_light_stage(s) for s in stages]
        return p
    stages = db.query(
        'SELECT * FROM stages WHERE pipeline_id=? ORDER BY "order"', (pid,)
    )
    p["stages"] = [_stage_row_to_dict(s) for s in stages]
    return p


def list_pipelines(light=False):
    # Oldest first, so the original default pipeline stays [0] after a second
    # (single-writer) pipeline is added. Consumers that want a specific pipeline
    # select by name; this only fixes the "primary is first" assumption.
    #
    # Two queries total, never one per pipeline: a round trip to Supabase costs
    # ~1 s from this region, so the old 1+2N pattern made /api/pipelines a ~6 s
    # call that a flaky connection could never finish. `light` additionally
    # leaves the ~470 kB of prompt bodies in the database — the picker and the
    # boot check both work without them.
    pipelines = db.query("SELECT * FROM pipelines ORDER BY created_at, id")
    if not pipelines:
        return []
    if light:
        rows = db.query(_LIGHT_COLUMNS + ' ORDER BY "order"',
                        (_PLACEHOLDER_LIKE,))
        to_dict = _light_stage
    else:
        rows = db.query('SELECT * FROM stages ORDER BY "order"')
        to_dict = _stage_row_to_dict
    by_pipeline = {}
    for row in rows:
        by_pipeline.setdefault(row["pipeline_id"], []).append(to_dict(row))
    for p in pipelines:
        p["stages"] = by_pipeline.get(p["id"], [])
    return pipelines


def create_pipeline(name):
    pid = _uuid()
    db.execute(
        "INSERT INTO pipelines(id,name,created_at,updated_at) VALUES(?,?,?,?)",
        (pid, name, _now(), _now()),
    )
    return get_pipeline(pid)


def touch_pipeline(pid):
    db.execute("UPDATE pipelines SET updated_at=? WHERE id=?", (_now(), pid))


def rename_pipeline(pid, name):
    db.execute("UPDATE pipelines SET name=?, updated_at=? WHERE id=?", (name, _now(), pid))


def add_stage(pid, s: dict):
    sid = _uuid()
    db.execute(
        'INSERT INTO stages(id,pipeline_id,"order",name,enabled,model,'
        "prompt_template,temperature,max_tokens,reasoning_effort,expects_json,"
        "web_search,validator_code,input_mapping) "
        "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            sid, pid, s["order"], s["name"], int(s.get("enabled", True)),
            s["model"], s.get("prompt_template", ""), s.get("temperature", 0.2),
            s.get("max_tokens", 16000), s.get("reasoning_effort"),
            int(s.get("expects_json", True)), int(s.get("web_search", False)),
            s.get("validator_code"), db.jdump(s.get("input_mapping", {})),
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
        "web_search=?,validator_code=?,input_mapping=? WHERE id=?",
        (
            merged["order"], merged["name"], int(merged["enabled"]),
            merged["model"], merged["prompt_template"], merged["temperature"],
            merged["max_tokens"], merged["reasoning_effort"],
            int(merged["expects_json"]), int(merged.get("web_search", False)),
            merged["validator_code"], db.jdump(merged.get("input_mapping", {})), sid,
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

def create_run(pid, input_data, stop_on_failure, identifier=None, params=None,
               parent_run_id=None, access_key=None):
    rid = _uuid()
    now = _now()
    # heartbeat_at is stamped at creation, not left NULL: the orphan sweep treats
    # a missing heartbeat as "nobody owns this", and a run created seconds before
    # a sibling container boots must not look abandoned.
    db.execute(
        "INSERT INTO runs(id,pipeline_id,input_data,status,stop_on_failure,"
        "total_cost_usd,created_at,identifier,params,parent_run_id,access_key,"
        "heartbeat_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
        (rid, pid, db.jdump(input_data), "running", int(stop_on_failure), 0.0,
         now, identifier, db.jdump(params or {}), parent_run_id, access_key, now),
    )
    return rid


def clone_run(parent_id, params=None, identifier=None):
    """Round-2 run: reuse the parent's pipeline + stage-0 FAKTAT data (which is
    deterministic and expensive-free to keep), link back via parent_run_id, and
    carry the user's clarifications in params. Copies the parent's order-0 stage
    result so a scoped `from_order=1` re-run finds stage 0 pre-loaded.

    `identifier` overrides the run's stage-0 FID: forecast-edit rounds (ACE
    #3048) point the clone at a freshly imported fid and re-run from stage 0,
    so the parent's order-0 result is NOT copied (stage 0 must refetch)."""
    parent = get_run(parent_id)
    if not parent:
        raise ValueError("parent run not found")
    merged_params = dict(parent.get("params") or {})
    merged_params.update(params or {})
    new_identifier = identifier if identifier is not None else parent.get("identifier")
    # A re-fid'd forecast-edit round must refetch stage 0 against the new fid.
    # Drop the parent's input_data (else runner's manual-paste shortcut would reuse
    # the OLD fid's FAKTAT and silently ignore the edit) — stage 0 fetches by the
    # new identifier instead.
    new_input_data = None if identifier is not None else parent.get("input_data")
    rid = create_run(
        parent["pipeline_id"], new_input_data,
        parent.get("stop_on_failure", True), new_identifier,
        merged_params, parent_run_id=parent_id,
        access_key=parent.get("access_key"),  # refinement stays owned by the expert
    )
    # Same reason: don't seed the parent's order-0 result (it holds the OLD fid's
    # data) for a re-fid'd round.
    if identifier is None:
        for res in parent.get("results") or []:
            if res.get("order") == 0:
                upsert_result(rid, {**res, "run_id": rid})
                break
    return rid


def _family_ids(rid):
    """(root_id, descendant_ids) for `rid`'s report family: walk parent_run_id
    up to the root round-1 run, then BFS over children.

    Only ids are needed, so this must never call get_run(): that pulls every
    stage result and JSON-decodes it, and GET /api/runs/{rid} calls this on the
    run it has already loaded — a 600 kB run was fetched and parsed twice per
    request, which is where its 6 s went."""
    row = db.query_one("SELECT id, parent_run_id FROM runs WHERE id=?", (rid,))
    while row and row.get("parent_run_id"):
        parent = db.query_one(
            "SELECT id, parent_run_id FROM runs WHERE id=?", (row["parent_run_id"],)
        )
        if not parent:
            break
        row = parent
    if not row:
        return None, []
    descendants = []
    frontier = [row["id"]]
    while frontier:
        children = [r["id"] for r in db.query(
            "SELECT id FROM runs WHERE parent_run_id IN (%s)"
            % ",".join("?" * len(frontier)),
            tuple(frontier),
        )]
        descendants += children
        frontier = children
    return row["id"], descendants


def family_run_ids(rid):
    """Every run id in `rid`'s report family, root first."""
    root, descendants = _family_ids(rid)
    return ([root] + descendants) if root else []


def append_forecast_preview(rid, entry):
    """Keep what the user typed into the forecast-description box, plus the AI's
    proposal. /forecast-preview is otherwise stateless: before this, a customer
    could describe the forecast change they wanted, see a good proposal, never
    click "use these", and leave no trace of having asked at all.

    ponytail: read-modify-write on runs.params, no row lock. A concurrent
    generate-forecast could drop one preview entry; the trail is a record, not
    a transaction, so that trade is fine.
    """
    row = db.query_one("SELECT params FROM runs WHERE id=?", (rid,))
    if not row:
        return
    params = db.jload(row.get("params")) or {}
    previews = params.get("forecast_previews") or []
    previews.append({"at": _now(), **entry})
    params["forecast_previews"] = previews[-20:]
    db.execute("UPDATE runs SET params=? WHERE id=?", (db.jdump(params), rid))


def append_forecast_import_failure(rid, entry):
    """Record a forecast import that ValuBuild refused.

    The import runs before any paid stage, so a failure leaves no stage result,
    no child run and no trace of what the customer tried to do — the edits never
    reach params, because params are only written once the import succeeds.
    Without this the whole attempt is invisible to everyone but the person who
    saw the error on screen.

    ponytail: same read-modify-write as append_forecast_preview; a record, not a
    transaction.
    """
    row = db.query_one("SELECT params FROM runs WHERE id=?", (rid,))
    if not row:
        return
    params = db.jload(row.get("params")) or {}
    failures = params.get("forecast_import_failures") or []
    failures.append({"at": _now(), **entry})
    params["forecast_import_failures"] = failures[-20:]
    db.execute("UPDATE runs SET params=? WHERE id=?", (db.jdump(params), rid))


def refinement_count(rid):
    """How many refinement runs already exist in `rid`'s report FAMILY (every
    descendant of the root round-1 run). Used to cap total refinements per
    purchased report. Counting only the parent PATH to `rid` (the old
    lineage_depth) left a hole: the emailed report link always points at the
    run that just finished — for round 1 that's the root, whose path depth is
    forever 0, so reopening the email let a customer start unlimited fresh
    rounds off the root while each browser-side round chained normally."""
    _root, descendants = _family_ids(rid)
    return len(descendants)


def latest_family_run_id(rid):
    """Newest run in `rid`'s family by created_at. After a browser-side
    refinement the emailed link still targets an older run — the UI uses this
    to jump to the current version instead of re-showing a stale report."""
    root, descendants = _family_ids(rid)
    if not root:
        return None
    ids = [root] + descendants
    rows = db.query(
        "SELECT id FROM runs WHERE id IN (%s) ORDER BY created_at DESC, id DESC"
        % ",".join("?" * len(ids)),
        tuple(ids),
    )
    return rows[0]["id"] if rows else root


def set_run_status(rid, status):
    db.execute("UPDATE runs SET status=? WHERE id=?", (status, rid))


def rebind_run_forecast(rid, identifier, params):
    """Point an awaiting round-1 run at an imported forecast model.

    input_data must be cleared with the identifier swap: otherwise the runner's
    manual-input shortcut could silently reuse the old FID's stage-0 data.
    Existing stage-0 is intentionally kept until the new stage-0 execution
    overwrites it, so the operation remains auditable if starting the task fails.
    """
    db.execute(
        "UPDATE runs SET identifier=?, input_data=?, params=?, status=? WHERE id=?",
        (str(identifier), None, db.jdump(params or {}), "running", rid),
    )


def delete_run(rid):
    """Delete a run and its stage results. Explicit child delete so it works on
    SQLite (where FK cascade needs PRAGMA) and Postgres alike."""
    db.execute("DELETE FROM stage_results WHERE run_id=?", (rid,))
    db.execute("DELETE FROM runs WHERE id=?", (rid,))


def reset_stale_runs():
    """An interrupted forecast import returns to the retryable pre-generation
    state on startup.

    This used to also flip every 'running' run to 'error'. It no longer does:
    a running row is now resolved by the orphan sweep (main._recover_stale_runs),
    which resumes what it can and only fails what it cannot. The blanket UPDATE
    was also unsafe during a rolling deploy — it marked runs owned by the still
    live previous container as failed."""
    db.execute(
        "UPDATE runs SET status=? WHERE status=?",
        ("awaiting_forecast", "importing_forecast"),
    )


def touch_heartbeat(rid):
    """Proof of life for a run in progress. The runner stamps this every few
    seconds while a stage is executing; a stale stamp is what tells a freshly
    booted process that nobody is working on the run any more. Without it,
    'running' is ambiguous — mid-writer-stage and killed-20-minutes-ago look
    identical in the database."""
    db.execute("UPDATE runs SET heartbeat_at=? WHERE id=?", (_now(), rid))


def stale_running_runs(stale_seconds, max_age_hours=None):
    """Runs marked 'running' whose heartbeat has gone quiet — i.e. orphaned by a
    restart, deploy or crash.

    `max_age_hours` (when given) ignores anything created longer ago than that,
    so a startup after a long outage does not resurrect ancient work.
    A NULL heartbeat means the row predates this column; those are only stale if
    they are also old enough to fail the age check."""
    now = datetime.now(timezone.utc)
    cutoff = (now - timedelta(seconds=stale_seconds)).isoformat()
    rows = db.query("SELECT * FROM runs WHERE status=?", ("running",))
    out = []
    for r in rows:
        heartbeat = r.get("heartbeat_at")
        if heartbeat and heartbeat > cutoff:
            continue  # someone is actively working on it — hands off
        if not heartbeat and (r.get("created_at") or "") > cutoff:
            continue  # just created by another process, not yet stamped
        if max_age_hours:
            age_cutoff = (now - timedelta(hours=max_age_hours)).isoformat()
            if (r.get("created_at") or "") < age_cutoff:
                continue
        r["params"] = db.jload(r.get("params")) or {}
        out.append(r)
    return out


def bump_restart_attempts(rid):
    """Count the recoveries of one run and return the new total. The counter is
    what stops a boot loop: a run that kills the process (OOM on a huge payload)
    would otherwise be resumed on every startup, forever."""
    row = db.query_one("SELECT params FROM runs WHERE id=?", (rid,))
    params = (db.jload(row.get("params")) if row else None) or {}
    attempts = int(params.get("_restart_attempts") or 0) + 1
    params["_restart_attempts"] = attempts
    db.execute("UPDATE runs SET params=? WHERE id=?", (db.jdump(params), rid))
    return attempts


def fail_stale_run(rid, message):
    """Give up on an orphaned run, leaving a state a human can actually read.

    The stage rows matter as much as the run row: a stage frozen at 'running'
    is why an abandoned run used to surface as an opaque 'missing report
    sections' gate failure instead of naming what broke. Refunds the generation
    credit under the same guards as runner.run_stages, since the finalization
    that normally refunds never ran."""
    run = get_run(rid)
    if not run:
        return None
    for result in run.get("results") or []:
        if result.get("status") in ("running", "pending"):
            db.execute(
                "UPDATE stage_results SET status=?, error_message=?, finished_at=? "
                "WHERE run_id=? AND \"order\"=?",
                ("error", message, _now(), rid, result["order"]),
            )
    set_run_status(rid, "error")
    refunded = False
    if (run.get("access_key")
            and not run.get("parent_run_id")
            and not (run.get("params") or {}).get("_credit_refunded")):
        refund_generation(run["access_key"])
        mark_credit_refunded(rid)
        refunded = True
    return {"run": run, "credit_refunded": refunded}


def usd_spent_today():
    today = datetime.now(timezone.utc).date().isoformat()
    rows = db.query("SELECT total_cost_usd FROM runs WHERE created_at >= ?", (today,))
    return sum((r["total_cost_usd"] or 0.0) for r in rows)


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
        model=res.get("model"),
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
        "model": r["model"],
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
    run["identifier"] = run.get("identifier")
    run["params"] = db.jload(run.get("params")) or {}
    results = db.query(
        'SELECT * FROM stage_results WHERE run_id=? ORDER BY "order"', (rid,)
    )
    run["results"] = [_result_row(r) for r in results]
    return run


def report_readiness(rid):
    """Is this run safe to hand a client? Gate the report endpoints on it."""
    from . import assemble
    from .runner import SECTION_ORDER

    run = get_run(rid)
    if not run:
        return {"ready": False, "issues": ["run not found"]}
    issues = []
    if run["status"] != "ok":
        issues.append(f"run status is '{run['status']}', not 'ok'")
    for r in run["results"]:
        if r["order"] == 0:
            continue
        if r["status"] == "error":
            issues.append(f"stage {r['order']} ({r['name']}) errored")
        elif r["status"] == "validation_failed" or r.get("validator_passed") is False:
            issues.append(f"stage {r['order']} ({r['name']}) failed its number/consistency checks")
    rep = assemble.assemble(run)
    present = {str(s.get("id")) for s in (rep or {}).get("sections", [])} if rep else set()
    missing = [s for s in SECTION_ORDER if s not in present]
    if missing:
        issues.append(f"missing report sections: {', '.join(missing)}")
    issues = list(dict.fromkeys(issues))
    # Advisory QA over the assembled report (duplicate blocks, sensitivity
    # calibration, prose-vs-table figures). Non-blocking: surfaced, never gates.
    from . import report_qa
    qa_warnings = report_qa.warnings(rep) if rep else []
    return {"ready": not issues, "issues": issues, "warnings": qa_warnings}


def list_runs(limit=100):
    rows = db.query(
        "SELECT id,pipeline_id,input_data,status,total_cost_usd,created_at,params "
        "FROM runs ORDER BY created_at DESC LIMIT ?",
        (limit,),
    )
    out = []
    for r in rows:
        inp = db.jload(r.get("input_data"))
        company = None
        if isinstance(inp, dict):
            company = (inp.get("meta") or {}).get("company_name")
        if not company:
            # Self-serve runs (expert/checkout) are created with input_data=None
            # — stage 0 fills it in later, but only inside stage_results, never
            # back onto runs.input_data. The company name lives in params instead.
            params = db.jload(r.get("params"))
            if isinstance(params, dict):
                company = params.get("company_name")
        out.append({
            "id": r["id"],
            "pipeline_id": r["pipeline_id"],
            "status": r["status"],
            "total_cost_usd": r["total_cost_usd"],
            "created_at": r["created_at"],
            "company_name": company,
        })
    return out


# ---- companies (remembered name + FID for one-click reuse) ------------------

def upsert_company(fid, company_name, company_code=None, actuals=5,
                   estimates=10, input_data=None, last_run_id=None):
    """Remember a fetched company keyed by its Valuatum FID. The FID is typed at
    fetch time and lives nowhere else (input_data.meta only has y_tunnus), so we
    capture it here. input_data is stored too, enabling instant re-run without a
    fresh Valuatum fetch."""
    exists = db.query_one("SELECT fid FROM companies WHERE fid=?", (fid,))
    if exists:
        db.execute(
            "UPDATE companies SET company_name=?,company_code=?,actuals=?,"
            "estimates=?,input_data=?,last_run_id=COALESCE(?,last_run_id),"
            "updated_at=? WHERE fid=?",
            (company_name, company_code, actuals, estimates,
             db.jdump(input_data), last_run_id, _now(), fid),
        )
    else:
        db.execute(
            "INSERT INTO companies(fid,company_name,company_code,actuals,"
            "estimates,input_data,last_run_id,updated_at) "
            "VALUES(?,?,?,?,?,?,?,?)",
            (fid, company_name, company_code, actuals, estimates,
             db.jdump(input_data), last_run_id, _now()),
        )


def list_companies(limit=200):
    rows = db.query(
        "SELECT fid,company_name,company_code,actuals,estimates,updated_at,"
        "CASE WHEN input_data IS NULL OR input_data='' THEN 0 ELSE 1 END AS has_data "
        "FROM companies ORDER BY updated_at DESC LIMIT ?",
        (limit,),
    )
    return [{
        "fid": r["fid"],
        "company_name": r["company_name"],
        "company_code": r["company_code"],
        "actuals": r["actuals"],
        "estimates": r["estimates"],
        "updated_at": r["updated_at"],
        "has_data": bool(r["has_data"]),
    } for r in rows]


def get_company(fid):
    r = db.query_one("SELECT * FROM companies WHERE fid=?", (fid,))
    if not r:
        return None
    r["input_data"] = db.jload(r["input_data"])
    return r


def delete_company(fid):
    db.execute("DELETE FROM companies WHERE fid=?", (fid,))


def final_report_json(rid):
    """The JSON to feed the report generator: assembled wrapper + sections."""
    from . import assemble

    return assemble.assemble(get_run(rid))


def costs_summary(limit=200):
    """Per-run, per-model and grand-total cost aggregation across all runs."""
    runs = db.query(
        "SELECT id,input_data,status,total_cost_usd,created_at,params FROM runs "
        "ORDER BY created_at DESC LIMIT ?",
        (limit,),
    )
    by_model: dict[str, dict] = {}
    grand = 0.0
    out_runs = []
    for r in runs:
        # Same company-name derivation as list_runs: meta.company_name, else
        # params.company_name (self-serve runs start with input_data=None).
        inp = db.jload(r.get("input_data"))
        company = None
        if isinstance(inp, dict):
            company = (inp.get("meta") or {}).get("company_name")
        if not company:
            params = db.jload(r.get("params"))
            if isinstance(params, dict):
                company = params.get("company_name")
        sr = db.query(
            'SELECT "order",name,status,cost_usd,tokens_prompt,tokens_completion '
            "FROM stage_results WHERE run_id=?",
            (r["id"],),
        )
        rtotal = sum(s["cost_usd"] or 0 for s in sr)
        grand += rtotal
        out_runs.append({
            "id": r["id"],
            "company_name": company,
            "status": r["status"],
            "created_at": r["created_at"],
            "total_cost_usd": rtotal,
            "stage_count": len(sr),
        })
    # model stored on result since v2; fall back to live stage if missing.
    rows = db.query(
        # NB: no '?' string literal here — the Postgres shim does a blind
        # sql.replace("?", "%s"), so a literal '?' becomes a phantom placeholder
        # and 500s the whole endpoint. The Python `row["model"] or "?"` below
        # supplies the unknown-model fallback instead.
        "SELECT COALESCE(sr.model, st.model) AS model, sr.cost_usd AS cost, "
        "sr.tokens_prompt AS tp, sr.tokens_completion AS tc "
        "FROM stage_results sr LEFT JOIN stages st ON st.id = sr.stage_id"
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


# ---- website orders (public intake, operator fulfils) -----------------------

def create_order(company, email, user_input=None):
    oid = _uuid()
    db.execute(
        "INSERT INTO orders(id,company,email,user_input,status,created_at) "
        "VALUES(?,?,?,?,?,?)",
        (oid, company, email, user_input, "open", _now()),
    )
    return oid


def list_orders(limit=200):
    return [dict(r) for r in db.query(
        "SELECT * FROM orders ORDER BY created_at DESC LIMIT ?", (limit,))]


def set_order_status(oid, status):
    db.execute("UPDATE orders SET status=? WHERE id=?", (status, oid))
    return db.query_one("SELECT * FROM orders WHERE id=?", (oid,))


def get_order_by_session(stripe_session_id):
    # ORDER BY: a retried demo-mode checkout (deterministic session id, see
    # public_checkout_generate's failed-run retry) can leave more than one
    # order row under the same session id — always resolve to the newest.
    return db.query_one(
        "SELECT * FROM orders WHERE stripe_session_id=? "
        "ORDER BY created_at DESC LIMIT 1",
        (stripe_session_id,),
    )


def create_paid_order(company, email, user_input, stripe_session_id, fid, access_key,
                      run_id, amount_total_cents=None, currency=None):
    """A paid, auto-generated order (client-site checkout -> instant pipeline
    run), as opposed to create_order's manual-fulfilment intake. Kept in the
    same table so the operator dashboard sees both kinds together.

    The amount comes from the Stripe session the caller already fetched to
    verify payment — promo codes and Stripe Tax both move it off the list price.
    Going forward only; orders predating this have no amount."""
    oid = _uuid()
    db.execute(
        "INSERT INTO orders(id,company,email,user_input,status,created_at,"
        "stripe_session_id,fid,access_key,run_id,amount_total_cents,currency) "
        "VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
        (oid, company, email, user_input, "in_progress", _now(),
         stripe_session_id, fid, access_key, run_id, amount_total_cents, currency),
    )
    return oid


# ---- paid extra refinement rounds --------------------------------------------
# Round 3+ costs money (ROUND2_MAX_PER_RUN free rounds are exhausted). Stripe
# metadata is too small to carry the clarification answers, so they're staged
# here keyed by a token; Stripe metadata only carries the token.

def create_pending_round(rid, access_key, clarifications, clarifications_free_text,
                         scenario_probabilities=None, forecast_edits=None):
    token = _uuid()
    db.execute(
        "INSERT INTO pending_rounds(token,run_id,access_key,clarifications,"
        "clarifications_free_text,scenario_probabilities,forecast_edits,consumed,created_at) "
        "VALUES(?,?,?,?,?,?,?,0,?)",
        (token, rid, access_key, db.jdump(clarifications), clarifications_free_text,
         db.jdump(scenario_probabilities) if scenario_probabilities else None,
         db.jdump(forecast_edits) if forecast_edits else None, _now()),
    )
    return token


def get_pending_round(token):
    row = db.query_one("SELECT * FROM pending_rounds WHERE token=?", (token,))
    if row:
        row["clarifications"] = db.jload(row["clarifications"]) or []
        row["scenario_probabilities"] = db.jload(row.get("scenario_probabilities"))
        row["forecast_edits"] = db.jload(row.get("forecast_edits"))
    return row


def claim_pending_round(token):
    """Atomically flip consumed 0→1; False means another redeem already claimed
    the token. Pair with release_pending_round if the round then fails to start,
    so a paid token never burns without a round (the forecast-edit branch calls
    ValuBuild after payment and can fail there)."""
    cur = db.execute(
        "UPDATE pending_rounds SET consumed=1 WHERE token=? AND consumed=0", (token,))
    return cur.rowcount == 1


def release_pending_round(token):
    db.execute("UPDATE pending_rounds SET consumed=0 WHERE token=?", (token,))


# ---- expert access keys -----------------------------------------------------
# Capped, invite-only keys (prefix `exp_`) that let a trusted expert self-serve a
# few report generations. Round-1 runs consume quota; round-2 refinements don't.

def get_access_key(key):
    if not key:
        return None
    return db.query_one("SELECT * FROM access_keys WHERE key=?", (key,))


def create_access_key(label, generations_limit=3, expires_at=None):
    key = "exp_" + _uuid()
    db.execute(
        "INSERT INTO access_keys(key,label,generations_limit,generations_used,"
        "active,expires_at,created_at) VALUES(?,?,?,0,1,?,?)",
        (key, label, int(generations_limit), expires_at, _now()),
    )
    return get_access_key(key)


def set_access_key_limit(key, generations_limit):
    db.execute("UPDATE access_keys SET generations_limit=? WHERE key=?",
               (int(generations_limit), key))
    return get_access_key(key)


def list_access_keys():
    return db.query("SELECT * FROM access_keys ORDER BY created_at DESC")


def consume_generation(key):
    """Atomically claim one generation. Returns True if claimed, False if the key
    is missing/inactive/expired/exhausted. The conditional UPDATE does the
    check-and-increment in one statement, so two concurrent requests can't both
    slip past the last unit (works on psycopg3 + sqlite3 rowcount).
    generations_limit <= 0 means UNLIMITED — always claims (still counts usage)."""
    cur = db.execute(
        "UPDATE access_keys SET generations_used = generations_used + 1 "
        "WHERE key=? AND active=1 "
        "AND (generations_limit <= 0 OR generations_used < generations_limit) "
        "AND (expires_at IS NULL OR expires_at > ?)",
        (key, _now()),
    )
    return getattr(cur, "rowcount", 0) > 0


def refund_generation(key):
    """Give back one consumed generation credit — called when a run FAILS so a
    spend-cap trip (or any error) does not permanently eat the user's credit.
    Floors at 0 (`generations_used > 0`) so it can never over-refund."""
    db.execute(
        "UPDATE access_keys SET generations_used = generations_used - 1 "
        "WHERE key=? AND generations_used > 0",
        (key,),
    )


def mark_credit_refunded(rid):
    """Idempotency marker on the run's params so re-running the same failed run
    (the restart trap — cost persists, re-hits the cap) can't refund twice."""
    row = db.query_one("SELECT params FROM runs WHERE id=?", (rid,))
    params = (db.jload(row.get("params")) if row else None) or {}
    params["_credit_refunded"] = True
    db.execute("UPDATE runs SET params=? WHERE id=?", (db.jdump(params), rid))


# ---- report monitor ---------------------------------------------------------

def monitor_summary(from_iso, to_iso):
    """One entry per report FAMILY whose root run was created in the range.

    A sale buys a root run plus its refinement rounds, so cost and paid rounds
    are summed over the family. `status` and the timing are the root run's —
    that is the run the customer paid for and waited on.
    """
    roots = db.query(
        "SELECT id,status,created_at,params FROM runs "
        "WHERE parent_run_id IS NULL AND created_at BETWEEN ? AND ? "
        "ORDER BY created_at DESC",
        (from_iso, to_iso),
    )
    if not roots:
        return []
    # One query per generation rather than a walk per family; chains are short.
    family_of = {r["id"]: r["id"] for r in roots}
    frontier = list(family_of)
    while frontier:
        children = db.query(
            "SELECT id,parent_run_id FROM runs WHERE parent_run_id IN (%s)"
            % ",".join("?" * len(frontier)),
            tuple(frontier),
        )
        frontier = []
        for c in children:
            if c["id"] in family_of:  # guards a cycle looping this forever
                continue
            family_of[c["id"]] = family_of[c["parent_run_id"]]
            frontier.append(c["id"])

    ids = list(family_of)
    holes = ",".join("?" * len(ids))
    cost, started, finished, size = {}, {}, {}, {}
    for root in family_of.values():
        size[root] = size.get(root, 0) + 1
    for s in db.query(
        "SELECT run_id,cost_usd,started_at,finished_at FROM stage_results "
        "WHERE run_id IN (%s)" % holes, tuple(ids)
    ):
        f = family_of[s["run_id"]]
        cost[f] = cost.get(f, 0.0) + (s["cost_usd"] or 0)
        # Timing is the root run's alone: a family spans the hours a customer
        # takes to ask for a round, which is not generation time.
        if s["run_id"] != f:
            continue
        # Timestamps are all _now(), one format — string min/max is chronological.
        if s["started_at"]:
            started[f] = min(started.get(f, s["started_at"]), s["started_at"])
        if s["finished_at"]:
            finished[f] = max(finished.get(f, s["finished_at"]), s["finished_at"])

    orders = {}
    for o in db.query(
        "SELECT run_id,email,company,amount_total_cents,currency,created_at "
        "FROM orders WHERE run_id IN (%s) ORDER BY created_at" % holes, tuple(ids)
    ):
        f = family_of[o["run_id"]]
        agg = orders.setdefault(f, {
            "email": o["email"], "company": o["company"],
            "amountTotalCents": None, "currency": None, "sales": 0,
        })
        agg["sales"] += 1
        # The base sale plus every paid extra round on the same report.
        if o["amount_total_cents"] is not None:
            agg["amountTotalCents"] = (
                (agg["amountTotalCents"] or 0) + o["amount_total_cents"])
        agg["currency"] = agg["currency"] or o["currency"]

    out = []
    for r in roots:
        rid = r["id"]
        params = db.jload(r.get("params")) or {}
        out.append({
            "runId": rid,
            "createdAt": r["created_at"],
            "company": params.get("company_name") or "",
            "status": r["status"],
            "costUsd": cost.get(rid, 0.0),
            "startedAt": started.get(rid),
            "finishedAt": finished.get(rid),
            "rounds": size.get(rid, 1) - 1,
            "order": orders.get(rid),
        })
    return out
