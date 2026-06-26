"""Tests for the v2 pipeline: code assembler, validators, and — most importantly
— the cover guard (the cover headline corrupted twice in production, so this is
a required regression guard)."""
import json
import os

import pytest

from app import assemble, render, validators

VDIR = os.path.join(os.path.dirname(__file__), "..", "validators_seed")


def _v(name):
    with open(os.path.join(VDIR, name), encoding="utf-8") as f:
        return f.read()


def _report():
    return {
        "meta": {"company_name": "Star Asfaltti Oy", "y_tunnus": "2003123-4",
                 "industry": "Maarakentaminen", "report_date": "2026-06-26", "unit": "tEUR"},
        "cover": {"headline_label": "Skenaarioiden odotusarvo",
                  "headline_value": "1 598 tEUR", "base_case_value": "1 000 tEUR",
                  "secondary_lines": ["Luottamustaso: Kohtalainen"]},
        "machine_readable": {"expected_value": 1598},
        "sections": [
            {"id": "1", "title": "TIIVISTELMÄ", "blocks": [
                {"type": "paragraph", "text": "Odotusarvo 1 598 tEUR, base case 1 000 tEUR."}]},
            {"id": "11", "title": "SKENAARIOT", "blocks": [
                {"type": "scenario_table", "scenario": "realistinen", "value_teur": 1000,
                 "probability_pct": 50, "drivers": [{"key": "EBIT-%", "value": "12 %"}],
                 "perusluvut": {"columns": ["Vuosi"], "rows": [["2034"]]},
                 "avainluvut": {"columns": ["Kasvu"], "rows": [["6 %"]]}}]},
        ],
    }


# --------------------------------------------------------------- cover guard
def test_render_html_contains_both_cover_figures():
    html = render.render_html(_report())
    text = render._norm_ws(render._strip_tags(html))
    assert "1 598 tEUR" in text
    assert "1 000 tEUR" in text


def test_cover_guard_passes_on_intact_cover():
    render._cover_guard(_report(), render._derive(_report()))  # must not raise


def test_cover_guard_rejects_per_glyph_corruption(monkeypatch):
    orig = render._cover
    monkeypatch.setattr(
        render, "_cover",
        lambda r, d: orig(r, d).replace("1 598 tEUR", "1 5 9 8 t E U R"),
    )
    with pytest.raises(render.CoverGuardError):
        render._cover_guard(_report(), render._derive(_report()))


# --------------------------------------------------------------- assembler
def test_assembler_orders_sections_without_section_7():
    run = {"results": [
        {"order": 2, "status": "ok", "parsed_json": {"sections": [{"id": "3"}]}},
        {"order": 3, "status": "ok", "parsed_json": {
            "sections": [{"id": "5"}, {"id": "6"}, {"id": "8"}, {"id": "9"}, {"id": "10"}]}},
        {"order": 4, "status": "validation_failed", "parsed_json": {"sections": [{"id": "11"}]}},
        {"order": 5, "status": "ok", "parsed_json": {
            "sections": [{"id": "4"}, {"id": "12"}, {"id": "13"}, {"id": "14"}]}},
        {"order": 6, "status": "ok", "parsed_json": {
            "report_type": "ai_valuation_report", "cover": {"headline_value": "1"},
            "sections": [{"id": "1"}, {"id": "2"}, {"id": "15"}, {"id": "16"}]}},
    ]}
    rep = assemble.assemble(run)
    ids = [s["id"] for s in rep["sections"]]
    assert ids == ["1", "2", "3", "4", "5", "6", "8", "9", "10", "11", "12", "13", "14", "15", "16"]
    assert "7" not in ids
    assert rep["report_type"] == "ai_valuation_report"


# --------------------------------------------------------------- validators
def _s4(**over):
    base = {"scenarios": [
        {"name": "optimistinen", "value_teur": 3000, "probability_pct": 30, "probability_rationale": "iso markkina"},
        {"name": "realistinen", "value_teur": 1000, "probability_pct": 50, "probability_rationale": "base"},
        {"name": "pessimistinen", "value_teur": 0, "probability_pct": 20, "probability_rationale": "rahoitus katkeaa"}],
        "expected_value_teur": 1400, "realistic_base_case_teur": 1000}
    base.update(over)
    return base


def test_stage4_validator_passes_consistent():
    r = validators.run_validator(_v("stage4_scenarios.py"), _s4(), {})
    assert r["passed"], r


@pytest.mark.parametrize("bad", [
    {"expected_value_teur": 9999},
    {"scenarios": [{"name": "optimistinen", "value_teur": -5, "probability_pct": 30, "probability_rationale": "x"},
                   {"name": "realistinen", "value_teur": 1000, "probability_pct": 50, "probability_rationale": "x"},
                   {"name": "pessimistinen", "value_teur": 0, "probability_pct": 20, "probability_rationale": "x"}]},
])
def test_stage4_validator_catches_bad(bad):
    r = validators.run_validator(_v("stage4_scenarios.py"), _s4(**bad), {})
    assert not r["passed"], r


def test_stage4_validator_requires_rationale():
    out = _s4()
    out["scenarios"][1]["probability_rationale"] = ""
    r = validators.run_validator(_v("stage4_scenarios.py"), out, {})
    assert not r["passed"]


def test_stage4_validator_catches_missing_value():
    out = _s4()
    out["scenarios"][0].pop("value_teur")  # optimistic value absent
    r = validators.run_validator(_v("stage4_scenarios.py"), out, {})
    assert not r["passed"]


def test_stage4_validator_handles_one_percent_probability():
    # 1% must not be misread as 100%: opt=1, real=98, pess=1 sums to 100.
    out = {"scenarios": [
        {"name": "optimistinen", "value_teur": 5000, "probability_pct": 1, "probability_rationale": "x"},
        {"name": "realistinen", "value_teur": 1000, "probability_pct": 98, "probability_rationale": "x"},
        {"name": "pessimistinen", "value_teur": 0, "probability_pct": 1, "probability_rationale": "x"}],
        "expected_value_teur": round(0.01 * 5000 + 0.98 * 1000 + 0.01 * 0),
        "realistic_base_case_teur": 1000}
    r = validators.run_validator(_v("stage4_scenarios.py"), out, {})
    assert r["passed"], r


def test_stage6_validator_passes_nbsp_formatted_cover():
    # Finnish thousands separators may be NBSP (U+00A0) / narrow NBSP (U+202F).
    ctx = {"scenarios": {"expected_value_teur": 1598, "realistic_base_case_teur": 1000}}
    out = {"cover": {"headline_value": "1 598 tEUR", "base_case_value": "1 000 tEUR"},
           "machine_readable": {"expected_value": 1598, "base": 1000},
           "sections": []}
    r = validators.run_validator(_v("stage6_final.py"), out, ctx)
    assert r["passed"], r


def test_stage6_validator_requires_both_cover_figures():
    ctx = {"scenarios": {"expected_value_teur": 1400, "realistic_base_case_teur": 1000}}
    good = {"cover": {"headline_value": "1 400 tEUR", "base_case_value": "1 000 tEUR"},
            "machine_readable": {"expected_value": 1400, "base": 1000},
            "sections": [{"id": "1", "blocks": [
                {"type": "paragraph", "text": "Odotusarvo 1 400 tEUR ja base case 1 000 tEUR."}]}]}
    assert validators.run_validator(_v("stage6_final.py"), good, ctx)["passed"]
    missing = json.loads(json.dumps(good))
    missing["cover"].pop("base_case_value")
    assert not validators.run_validator(_v("stage6_final.py"), missing, ctx)["passed"]


# --------------------------------------------------------------- block safety
def test_blocks_tolerate_missing_and_null_fields():
    secs = [{"id": "5", "blocks": [
        {"type": "metric_cards", "cards": [None, {"value": "10", "label": "rev"}]},
        {"type": "key_value", "items": [None, "x", {"key": "a", "value": "b"}]},
        {"type": "table"},
        {"type": "chart", "chart_type": "bar_line", "series": [{"type": "line", "values": [None]}]},
        {"type": "scenario_table"}, {"type": "callout"}, {"type": "paragraph"},
        {"type": "wat"}]}]
    rep = {"meta": {"company_name": "X"},
           "cover": {"headline_value": "1 tEUR", "base_case_value": "1 tEUR"},
           "sections": secs}
    html = render.render_html(rep)  # must not raise on null cards/items
    assert "rev" in html


def test_cover_guard_rejects_blank_figure():
    rep = _report()
    rep["cover"]["base_case_value"] = ""
    with pytest.raises(render.CoverGuardError):
        render._cover_guard(rep, render._derive(rep))


def test_renderer_drops_noncanonical_section_ids():
    rep = {"meta": {"company_name": "X"},
           "cover": {"headline_value": "1 tEUR", "base_case_value": "1 tEUR"},
           "sections": [{"id": "1", "title": "A", "blocks": []},
                        {"id": "7", "title": "GHOST", "blocks": []},
                        {"id": "16", "title": "Z", "blocks": []}]}
    ordered = render._ordered_sections(rep)
    assert [s["id"] for s in ordered] == ["1", "16"]
    assert "GHOST" not in render.render_html(rep)


@pytest.mark.skipif(not render.pdf_available(), reason="no local Chromium")
def test_render_pdf_smoke(tmp_path):
    out = str(tmp_path / "r.pdf")
    render.render_pdf(_report(), out)
    assert os.path.getsize(out) > 1000
    with open(out, "rb") as f:
        assert f.read(5) == b"%PDF-"
