"""The run loop: substitute {{variables}}, call OpenRouter (or the fetcher for
stage 0), parse JSON, run the validator, persist, and yield SSE events.

This is an async generator so the HTTP layer can stream progress live.
"""
import json
import os
import re
import time
from datetime import datetime, timezone

from . import openrouter, store, validators
from .models import DATA_FETCHER_MODEL
from fetchers.company_data import fetch_company_data


def _spend_cap_exceeded(rid):
    """Optional hard ceiling on OpenRouter spend (env VALU_RUN_USD_CAP /
    VALU_DAILY_USD_CAP, both default off). Checked before each paid stage."""
    run_cap = float(os.getenv("VALU_RUN_USD_CAP") or 0)
    day_cap = float(os.getenv("VALU_DAILY_USD_CAP") or 0)
    if run_cap:
        rc = (store.get_run(rid) or {}).get("total_cost_usd", 0.0) or 0.0
        if rc >= run_cap:
            return f"Per-run spend cap saavutettu (${rc:.4f} ≥ ${run_cap:.2f})."
    if day_cap:
        dc = store.usd_spent_today()
        if dc >= day_cap:
            return f"Päivän spend cap saavutettu (${dc:.4f} ≥ ${day_cap:.2f})."
    return None

# Canonical report section order. Section 7 is intentionally absent.
SECTION_ORDER = [
    "1", "2", "3", "4", "5", "6", "8", "9", "10", "11", "12", "13", "14",
    "15", "16",
]

# Well-known context keys by stage order (slugified name is also added).
WELL_KNOWN = {
    0: "input_data",
    1: "enrichment",
    2: "profile_analysis",
    3: "sections_numeric",
    4: "scenarios",
    5: "analysis_sections",
    6: "summary",
}

_VAR_RE = re.compile(r"\{\{\s*([a-zA-Z0-9_]+)\s*\}\}")


def _now():
    return datetime.now(timezone.utc).isoformat()


def _slug(name):
    return re.sub(r"_+", "_", re.sub(r"[^a-z0-9]+", "_", name.lower())).strip("_")


def _output_value(result):
    if result.get("parsed_json") is not None:
        return result["parsed_json"]
    return {"raw": result.get("raw_response", "")}


def substitute(template, context):
    """Replace {{var}} from context. Returns (text, error_or_None)."""
    missing = []

    def repl(m):
        key = m.group(1)
        if key not in context:
            missing.append(key)
            return m.group(0)
        v = context[key]
        return v if isinstance(v, str) else json.dumps(v, ensure_ascii=False, indent=2)

    text = _VAR_RE.sub(repl, template)
    if missing:
        return None, (
            f"variable {{{{{missing[0]}}}}} not available — is the stage that "
            f"produces it enabled and ordered before this one?"
        )
    return text, None


def _merge_sections(left, right):
    by_id = {}
    for sec in left + right:
        if isinstance(sec, dict) and sec.get("id") is not None:
            by_id[str(sec["id"])] = sec
    return [by_id[k] for k in sorted(by_id, key=_section_sort_key)]


def _section_sort_key(section_id):
    sid = str(section_id)
    if sid in SECTION_ORDER:
        return (SECTION_ORDER.index(sid), 0)
    try:
        return (len(SECTION_ORDER), int(sid))
    except ValueError:
        return (len(SECTION_ORDER), 0)


def _sections_bundle(sections):
    return {"sections": sections}


def _contribute(context, stage, output):
    """Expose a stage output under stable names for later prompt templates."""
    primary = WELL_KNOWN.get(stage["order"], _slug(stage["name"]))
    context[primary] = output
    context[_slug(stage["name"])] = output

    if not isinstance(output, dict):
        return

    if "growth_assessment" in output:
        context["growth_assessment"] = output["growth_assessment"]
    if "scoring" in output:
        context["scoring"] = output["scoring"]
    if stage["order"] == 4:
        # Later prompts need expected_value_teur and realistic_base_case_teur,
        # so keep the whole stage-4 object under the scenarios key.
        context["scenarios"] = output

    sections = output.get("sections")
    if isinstance(sections, list):
        if stage["order"] == 3:
            context["sections_numeric"] = output
        if stage["order"] in (2, 4, 5):
            current = context.get("sections_analysis")
            current_sections = (
                current.get("sections", []) if isinstance(current, dict) else []
            )
            context["sections_analysis"] = _sections_bundle(
                _merge_sections(current_sections, sections)
            )


def _failure_feedback(res):
    """Turn a failed stage result into a concrete correction instruction: the
    exact checks that failed (or the error), so the retry model knows what to fix
    instead of blindly re-rolling the same prompt."""
    parts = []
    vr = res.get("validator_report") or {}
    fails = [c for c in vr.get("checks", []) if not c.get("passed")]
    for c in fails[:12]:
        parts.append(f"- {c.get('name')}: {c.get('detail')}")
    if not fails and res.get("error_message"):
        parts.append("- " + str(res["error_message"])[:500])
    return "\n".join(parts)


def _correction_prompt(base_prompt, correction):
    """Append a targeted self-correction instruction + the model's own previous
    output to the original prompt."""
    prev = correction.get("previous")
    prev_str = (json.dumps(prev, ensure_ascii=False, indent=2)
                if not isinstance(prev, str) else prev)
    return (
        base_prompt
        + "\n\n================ KORJAUSOHJE ================\n"
        + "Tuotit jo vastauksen, mutta se ei läpäissyt automaattisia tarkistuksia. "
        + "Korjaa VAIN alla luetellut ongelmat. Älä muuta mitään muuta. Palauta "
        + "täsmälleen sama JSON-rakenne korjattuna ja täydellisenä.\n\n"
        + "Havaitut ongelmat:\n" + (correction.get("feedback") or "(ei eritelty)")
        + "\n\nEdellinen vastauksesi (korjattavana):\n"
        + (prev_str or "(tyhjä)")[:24000]
    )


async def _execute_stage(stage, context, run_input_data, identifier, params,
                         correction=None):
    """Run one stage. Returns a result dict (not yet persisted). When `correction`
    is given, the prompt is augmented with the prior failure + output so the model
    fixes the specific issue (feedback-driven self-correction)."""
    started = _now()
    t0 = time.time()
    res = {
        "stage_id": stage["id"],
        "order": stage["order"],
        "name": stage["name"],
        "model": stage["model"],
        "started_at": started,
        "status": "running",
        "tokens_prompt": 0,
        "tokens_completion": 0,
        "cost_usd": 0.0,
    }

    # Stage 0 — data fetcher, not OpenRouter.
    if stage["model"] == DATA_FETCHER_MODEL:
        try:
            if run_input_data is not None:
                data = run_input_data  # manual paste path
            elif identifier:
                data = await fetch_company_data(identifier, params or {})
            else:
                raise ValueError(
                    "Stage 0: ei input_dataa eikä tunnistetta. Liitä FAKTAT-JSON "
                    "tai anna Y-tunnus."
                )
            res["raw_response"] = json.dumps(data, ensure_ascii=False, indent=2)
            res["parsed_json"] = data
            res["finish_reason"] = "stop"
            res["status"] = "ok"
        except Exception as e:  # NotImplementedError, ValueError, ...
            res["status"] = "error"
            res["error_message"] = str(e) or repr(e)
        res["latency_ms"] = int((time.time() - t0) * 1000)
        res["finished_at"] = _now()
        return res

    # Other stages — substitute + call OpenRouter.
    prompt, err = substitute(stage["prompt_template"], context)
    if err:
        res["status"] = "error"
        res["error_message"] = err
        res["latency_ms"] = int((time.time() - t0) * 1000)
        res["finished_at"] = _now()
        return res

    if correction:
        prompt = _correction_prompt(prompt, correction)

    try:
        r = await openrouter.chat(
            model=stage["model"],
            prompt=prompt,
            temperature=stage["temperature"],
            max_tokens=stage["max_tokens"],
            reasoning_effort=stage["reasoning_effort"],
            expects_json=stage["expects_json"],
            web_search=stage.get("web_search", False),
        )
    except Exception as e:
        res["status"] = "error"
        res["error_message"] = str(e)
        res["request_payload"] = {"model": stage["model"], "prompt": prompt}
        res["latency_ms"] = int((time.time() - t0) * 1000)
        res["finished_at"] = _now()
        return res

    res["raw_response"] = r["text"]
    res["request_payload"] = r["request_payload"]
    res["finish_reason"] = r["finish_reason"]
    res["tokens_prompt"] = r["tokens_prompt"]
    res["tokens_completion"] = r["tokens_completion"]
    res["cost_usd"] = openrouter.cost_for(
        stage["model"], r["tokens_prompt"], r["tokens_completion"]
    )

    if stage["expects_json"]:
        parsed = openrouter.extract_json(r["text"])
        # Retry once on a truncated/unparseable response. 'length' = hit the
        # token cap, so give it more room. 'error' = the provider returned a
        # truncated/aborted HTTP 200 (seen with z-ai/glm-5.2) — transient, retry
        # at the same size. Both are the #1 intermittent failures on big runs.
        if parsed is None and r.get("finish_reason") in ("length", "error"):
            retry_max = int(stage["max_tokens"])
            if r.get("finish_reason") == "length":
                retry_max = min(retry_max * 2, 120000)
            try:
                r2 = await openrouter.chat(
                    model=stage["model"], prompt=prompt,
                    temperature=stage["temperature"],
                    max_tokens=retry_max,
                    reasoning_effort=stage["reasoning_effort"],
                    expects_json=stage["expects_json"],
                    web_search=stage.get("web_search", False),
                )
                res["raw_response"] = r2["text"]
                res["finish_reason"] = r2["finish_reason"]
                res["tokens_prompt"] += r2["tokens_prompt"]
                res["tokens_completion"] += r2["tokens_completion"]
                res["cost_usd"] += openrouter.cost_for(
                    stage["model"], r2["tokens_prompt"], r2["tokens_completion"])
                parsed = openrouter.extract_json(r2["text"])
            except Exception:
                pass
        if parsed is None:
            res["status"] = "error"
            res["error_message"] = (
                "JSON-parsinta epäonnistui. "
                + ("finish_reason='length' → nosta max_tokens." if r["finish_reason"]
                   == "length" else "Katso raw_response.")
            )
            res["latency_ms"] = int((time.time() - t0) * 1000)
            res["finished_at"] = _now()
            return res
        res["parsed_json"] = parsed
        output = parsed
    else:
        output = {"raw": r["text"]}

    res["status"] = "ok"

    if stage["validator_code"]:
        report = validators.run_validator(stage["validator_code"], output, context)
        res["validator_report"] = report
        res["validator_passed"] = bool(report.get("passed"))
        if not res["validator_passed"]:
            res["status"] = "validation_failed"

    res["latency_ms"] = int((time.time() - t0) * 1000)
    res["finished_at"] = _now()
    return res


def _ev(kind, **kw):
    return {"event": kind, **kw}


async def run_stages(run, stages, only=None, from_order=None):
    """Async generator yielding SSE event dicts. Persists each StageResult."""
    rid = run["id"]
    input_data = run.get("input_data")
    stop_on_failure = run.get("stop_on_failure", True)
    identifier = run.get("identifier")
    params = run.get("params", {})

    stages = sorted(stages, key=lambda s: s["order"])
    existing = {r["order"]: r for r in (store.get_run(rid) or {}).get("results", [])}

    context = {}
    if input_data is not None:
        context["input_data"] = input_data
    # User-supplied context / assumptions (free text). Always present so a prompt
    # referencing {{user_input}} never fails on a missing variable, and so the
    # numbers a user states (e.g. an assumed WACC or market size) land in the
    # validator context and are therefore treated as allowed, not fabricated.
    _ui = (params or {}).get("user_input")
    context["user_input"] = (
        _ui.strip() if isinstance(_ui, str) and _ui.strip()
        else "(Käyttäjä ei antanut lisätietoja tai oletuksia.)"
    )

    def in_scope(order):
        if only is not None:
            return order == only
        if from_order is not None:
            return order >= from_order
        return True

    # Preload context from prior results for stages we are NOT executing.
    for s in stages:
        if not in_scope(s["order"]) and s["order"] in existing:
            prev = existing[s["order"]]
            if prev.get("status") in ("ok", "validation_failed"):
                _contribute(context, s, _output_value(prev))

    halted = False
    for s in stages:
        if halted:
            store.upsert_result(rid, {**_base(s), "status": "pending"})
            yield _ev("stage", order=s["order"], status="pending", name=s["name"])
            continue

        if not in_scope(s["order"]):
            continue

        if not s["enabled"]:
            store.upsert_result(rid, {**_base(s), "status": "skipped"})
            yield _ev("stage", order=s["order"], status="skipped", name=s["name"])
            continue

        cap_msg = _spend_cap_exceeded(rid) if s["order"] >= 1 else None
        if cap_msg:
            store.upsert_result(rid, {**_base(s), "status": "error",
                                      "error_message": cap_msg, "finished_at": _now()})
            yield _ev("stage", order=s["order"], status="error", name=s["name"],
                      error_message=cap_msg)
            halted = True
            continue

        store.upsert_result(rid, {**_base(s), "status": "running",
                                  "started_at": _now()})
        yield _ev("stage", order=s["order"], status="running", name=s["name"])

        res = await _execute_stage(s, context, input_data, identifier, params)
        store.add_run_cost(rid, res.get("cost_usd", 0.0))

        # Self-heal: a transient model slip — a blank required field, a one-off
        # malformed/parse error — usually fixes itself on a fresh attempt. Retry a
        # failed model stage ONCE before accepting the failure. Skip when a spend
        # cap is hit or the error is a deterministic substitution error (a retry
        # cannot help those). Keep whichever attempt came out best.
        if (res["status"] in ("error", "validation_failed")
                and s["order"] >= 1
                and "variable {{" not in (res.get("error_message") or "")
                and not _spend_cap_exceeded(rid)):
            # Feedback-driven self-correction: hand the model back its own output
            # plus the exact checks it failed, and ask it to fix only those.
            correction = {
                "feedback": _failure_feedback(res),
                "previous": (res.get("parsed_json")
                             if res.get("parsed_json") is not None
                             else res.get("raw_response")),
            }
            store.upsert_result(rid, {**_base(s), "status": "running",
                                      "started_at": _now()})
            yield _ev("stage", order=s["order"], status="running",
                      name=s["name"], retry=True)
            retry = await _execute_stage(s, context, input_data, identifier,
                                         params, correction=correction)
            store.add_run_cost(rid, retry.get("cost_usd", 0.0))
            rank = {"ok": 2, "validation_failed": 1, "error": 0}
            if rank.get(retry["status"], 0) > rank.get(res["status"], 0):
                # Record what was auto-fixed so it stays auditable in the stage's
                # checklist even though the stage now passes.
                vr = retry.get("validator_report") or {"passed": True, "checks": []}
                vr["auto_corrected"] = True
                vr.setdefault("checks", []).insert(0, {
                    "name": "🔧 Automaattinen korjaus",
                    "passed": True,
                    "detail": "Vaihe korjattiin automaattisesti. Alkuperäinen "
                              "ongelma:\n" + (correction["feedback"] or "(ei eritelty)"),
                })
                retry["validator_report"] = vr
                res = retry

        store.upsert_result(rid, res)

        if res["status"] in ("ok", "validation_failed"):
            _contribute(context, s, _output_value(res))

        yield _ev(
            "stage",
            order=s["order"],
            status=res["status"],
            name=s["name"],
            validator_passed=res.get("validator_passed"),
            finish_reason=res.get("finish_reason"),
            cost_usd=res.get("cost_usd", 0.0),
            error_message=res.get("error_message"),
        )

        if stop_on_failure and res["status"] in ("error", "validation_failed"):
            halted = True

    run_after = store.get_run(rid)
    final = "error" if any(
        r["status"] in ("error", "validation_failed") for r in run_after["results"]
    ) else "ok"
    store.set_run_status(rid, final)
    yield _ev("done", status=final, total_cost_usd=run_after["total_cost_usd"])


def _base(stage):
    return {"stage_id": stage["id"], "order": stage["order"], "name": stage["name"]}
