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
        "web_search": bool(r.get("web_search")),
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
    # Oldest first, so the original default pipeline stays [0] after a second
    # (single-writer) pipeline is added. Consumers that want a specific pipeline
    # select by name; this only fixes the "primary is first" assumption.
    return [get_pipeline(p["id"])
            for p in db.query("SELECT id FROM pipelines ORDER BY created_at, id")]


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
    db.execute(
        "INSERT INTO runs(id,pipeline_id,input_data,status,stop_on_failure,"
        "total_cost_usd,created_at,identifier,params,parent_run_id,access_key) "
        "VALUES(?,?,?,?,?,?,?,?,?,?,?)",
        (rid, pid, db.jdump(input_data), "running", int(stop_on_failure), 0.0,
         _now(), identifier, db.jdump(params or {}), parent_run_id, access_key),
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


def lineage_depth(rid):
    """How many round-2 refinements already led to `rid`, walking
    parent_run_id back to the root round-1 run. Used to cap total
    refinements per purchased report — counting only rid's OWN children
    would miss a chain (refine round 2's result, then round 3's, ...), since
    each new run starts with zero children of its own."""
    depth = 0
    row = get_run(rid)
    while row and row.get("parent_run_id"):
        depth += 1
        row = get_run(row["parent_run_id"])
    return depth


def set_run_status(rid, status):
    db.execute("UPDATE runs SET status=? WHERE id=?", (status, rid))


def delete_run(rid):
    """Delete a run and its stage results. Explicit child delete so it works on
    SQLite (where FK cascade needs PRAGMA) and Postgres alike."""
    db.execute("DELETE FROM stage_results WHERE run_id=?", (rid,))
    db.execute("DELETE FROM runs WHERE id=?", (rid,))


def reset_stale_runs():
    """On startup, any run still marked 'running' is an orphan from a previous
    process (a deploy/restart killed its background task). Flip to error so the
    UI and history show a terminal state instead of a perpetual 'running'."""
    db.execute("UPDATE runs SET status=? WHERE status=?", ("error", "running"))


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


def create_paid_order(company, email, user_input, stripe_session_id, fid, access_key, run_id):
    """A paid, auto-generated order (client-site checkout -> instant pipeline
    run), as opposed to create_order's manual-fulfilment intake. Kept in the
    same table so the operator dashboard sees both kinds together."""
    oid = _uuid()
    db.execute(
        "INSERT INTO orders(id,company,email,user_input,status,created_at,"
        "stripe_session_id,fid,access_key,run_id) VALUES(?,?,?,?,?,?,?,?,?,?)",
        (oid, company, email, user_input, "in_progress", _now(),
         stripe_session_id, fid, access_key, run_id),
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
