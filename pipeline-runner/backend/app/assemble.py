"""Code assembler for the final report JSON.

Stage 6 (LLM) writes the wrapper (meta, cover, confidence, data_quality,
expected_value, machine_readable) plus sections 1, 2, 15, 16. Every other stage
emits its own `sections` array. This module — NOT the LLM — merges all section
arrays from every stage into one list sorted by the canonical section order
1,2,3,4,5,6,8,9,10,11,12,13,14,15,16 (there is no section 7), and returns the
final report object that feeds the renderer.
"""
from .runner import SECTION_ORDER

_WRAPPER_MARKERS = ("report_type", "cover", "machine_readable", "meta")


def _ok_outputs_by_order(run):
    out = {}
    for r in (run or {}).get("results", []):
        if r.get("status") in ("ok", "validation_failed"):
            pj = r.get("parsed_json")
            if isinstance(pj, dict):
                out[r["order"]] = pj
    return out


def _order_index(sid):
    s = str(sid)
    return SECTION_ORDER.index(s) if s in SECTION_ORDER else len(SECTION_ORDER) + _safe_int(s)


def _safe_int(s):
    try:
        return int(s)
    except (TypeError, ValueError):
        return 0


def merge_sections(outputs_by_order):
    """Collect + dedupe sections from every stage output, sorted canonically."""
    by_id = {}
    for order in sorted(outputs_by_order):
        secs = outputs_by_order[order].get("sections")
        if not isinstance(secs, list):
            continue
        for s in secs:
            if isinstance(s, dict) and s.get("id") is not None:
                by_id[str(s["id"])] = s  # later (higher-order) stage wins
    return [by_id[k] for k in sorted(by_id, key=_order_index)]


def assemble(run):
    """Build the final report dict from a finished run. Best-effort: returns
    whatever can be assembled even if stage 6 did not complete."""
    outputs = _ok_outputs_by_order(run)
    if not outputs:
        return None

    # Wrapper = the stage-6 output (highest order with wrapper-ish keys).
    wrapper = None
    for order in sorted(outputs, reverse=True):
        o = outputs[order]
        if any(k in o for k in _WRAPPER_MARKERS):
            wrapper = dict(o)
            break
    if wrapper is None:
        wrapper = dict(outputs[max(outputs)])

    wrapper["sections"] = merge_sections(outputs)
    return wrapper
