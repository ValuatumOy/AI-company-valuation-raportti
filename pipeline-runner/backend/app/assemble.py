"""Code assembler for the final report JSON.

Stage 6 (LLM) writes the wrapper (meta, cover, confidence, data_quality,
expected_value, machine_readable) plus sections 1, 2, 15, 16. Every other stage
emits its own `sections` array. This module — NOT the LLM — merges all section
arrays from every stage into one list sorted by the canonical section order
1,2,3,4,5,6,8,9,10,11,12,13,14,15,16 (there is no section 7), and returns the
final report object that feeds the renderer.
"""
from . import dcf_detail, headcount_efficiency, sensitivity, valuation_equivalence
from .runner import SECTION_ORDER

_WRAPPER_MARKERS = ("report_type", "cover", "machine_readable", "meta")
_DCF_SECTION_ID = "9"
_SENSITIVITY_SECTION_ID = "11"
_HISTORY_SECTION_ID = "5"


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


def _inject_sensitivity_blocks(sections, input_data):
    """Append the deterministic WACC×growth and revenue×EBIT-margin matrices
    to section 11 — computed in code (see app/sensitivity.py), never by the
    LLM, per the hard rule against inventing sensitivity matrices."""
    blocks = sensitivity.build_sensitivity_blocks(input_data)
    if not blocks:
        return sections
    for sec in sections:
        if isinstance(sec, dict) and str(sec.get("id")) == _SENSITIVITY_SECTION_ID:
            sec["blocks"] = list(sec.get("blocks") or []) + blocks
            break
    return sections


def _table_columns(block):
    cols = block.get("columns")
    return [str(c).strip().lower() for c in cols] if isinstance(cols, list) else []


def _row_labels(block):
    out = []
    for r in block.get("rows") or []:
        if isinstance(r, list) and r:
            out.append(str(r[0]).strip().lower())
    return out


def _is_old_fcff_table(block):
    if not isinstance(block, dict) or block.get("type") != "table":
        return False
    if str(block.get("table_id", "")).startswith("deterministic_dcf_"):
        return False
    cols = _table_columns(block)
    has_year = any(c in ("vuosi", "year") for c in cols)
    has_fcff = any("fcff" in c for c in cols)
    has_discounted = any(("diskont" in c or "discount" in c) for c in cols)
    return has_year and has_fcff and has_discounted


def _is_old_dcf_bridge_table(block):
    if not isinstance(block, dict) or block.get("type") != "table":
        return False
    if str(block.get("table_id", "")).startswith("deterministic_dcf_"):
        return False
    title = str(block.get("title") or "").lower()
    if "yritysarvosta" in title and "oman pääoman" in title:
        return True
    labels = _row_labels(block)
    bridge_markers = ("terminaaliarvon", "yritysarvo", "oman pääoman arvo")
    return sum(any(m in lab for m in bridge_markers) for lab in labels) >= 2


def _inject_dcf_detail_blocks(sections, input_data):
    """Replace the prompt-generated DCF cash-flow table with deterministic
    year-as-columns FCFF driver and EV-to-equity bridge tables."""
    blocks = dcf_detail.build_dcf_detail_blocks(input_data)
    if not blocks:
        return sections
    for sec in sections:
        if not (isinstance(sec, dict) and str(sec.get("id")) == _DCF_SECTION_ID):
            continue
        current = list(sec.get("blocks") or [])
        if any(
            isinstance(b, dict) and b.get("table_id") == "deterministic_dcf_fcff_drivers"
            for b in current
        ):
            return sections
        kept = []
        insert_at = None
        for b in current:
            if _is_old_fcff_table(b) or _is_old_dcf_bridge_table(b):
                if insert_at is None:
                    insert_at = len(kept)
                continue
            kept.append(b)
        if insert_at is None:
            insert_at = 0
            for i, b in enumerate(kept):
                if not isinstance(b, dict):
                    continue
                text = (str(b.get("title") or "") + " " + " ".join(_table_columns(b))).lower()
                if b.get("type") == "table" and "wacc" in text:
                    insert_at = i + 1
                    break
        sec["blocks"] = kept[:insert_at] + blocks + kept[insert_at:]
        break
    return sections


def _inject_headcount_efficiency_blocks(sections, input_data):
    """Append the deterministic per-employee ratio table to section 5 — computed
    in code (see app/headcount_efficiency.py), never by the LLM."""
    blocks = headcount_efficiency.build_headcount_efficiency_blocks(input_data)
    if not blocks:
        return sections
    for sec in sections:
        if not (isinstance(sec, dict) and str(sec.get("id")) == _HISTORY_SECTION_ID):
            continue
        current = list(sec.get("blocks") or [])
        if any(
            isinstance(b, dict) and b.get("table_id") == "deterministic_headcount_efficiency"
            for b in current
        ):
            return sections
        sec["blocks"] = current + blocks
        break
    return sections


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

    sections = merge_sections(outputs)
    _inject_dcf_detail_blocks(sections, outputs.get(0))
    _inject_sensitivity_blocks(sections, outputs.get(0))
    _inject_headcount_efficiency_blocks(sections, outputs.get(0))
    wrapper["sections"] = sections

    # Attach the structured scoring (stage 3) + scenarios (stage 4) objects so
    # the renderer can derive the signature visuals (range bar, method-value
    # chart, weights donut, confidence). Underscore-prefixed = renderer-only.
    s3 = outputs.get(3) or {}
    if isinstance(s3.get("scoring"), dict):
        wrapper.setdefault("_scoring", s3["scoring"])
    s4 = outputs.get(4)
    if isinstance(s4, dict):
        wrapper.setdefault("_scenarios", s4)
    valuation_equivalence.normalize_report(wrapper, outputs.get(0))
    return wrapper
