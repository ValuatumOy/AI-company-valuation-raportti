"""Tests for the v2 pipeline: code assembler, validators, and — most importantly
— the cover guard (the cover headline corrupted twice in production, so this is
a required regression guard)."""
import json
import os

import pytest

from app import assemble, render, validators

VDIR = os.path.join(os.path.dirname(__file__), "..", "validators_seed")
FIXTURES = os.path.join(os.path.dirname(__file__), "fixtures")


def _v(name):
    with open(os.path.join(VDIR, name), encoding="utf-8") as f:
        return f.read()


def _golden():
    with open(os.path.join(FIXTURES, "sample_report.json"), encoding="utf-8") as f:
        return json.load(f)


# Minimal section 16 the stage-6 validator now requires.
_DISCLAIMER_SEC = {"id": "16", "title": "Vastuuvapaus", "blocks": [
    {"type": "paragraph", "text": "Tämä ei ole sijoitusneuvontaa. Valuatum Oy ei vastaa."}]}


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
           "sections": [_DISCLAIMER_SEC]}
    r = validators.run_validator(_v("stage6_final.py"), out, ctx)
    assert r["passed"], r


def test_stage6_validator_requires_both_cover_figures():
    ctx = {"scenarios": {"expected_value_teur": 1400, "realistic_base_case_teur": 1000}}
    good = {"cover": {"headline_value": "1 400 tEUR", "base_case_value": "1 000 tEUR"},
            "machine_readable": {"expected_value": 1400, "base": 1000},
            "sections": [{"id": "1", "blocks": [
                {"type": "paragraph", "text": "Odotusarvo 1 400 tEUR ja base case 1 000 tEUR."}]}]}
    good["sections"].append(_DISCLAIMER_SEC)
    assert validators.run_validator(_v("stage6_final.py"), good, ctx)["passed"]
    missing = json.loads(json.dumps(good))
    missing["cover"].pop("base_case_value")
    assert not validators.run_validator(_v("stage6_final.py"), missing, ctx)["passed"]


# --------------------------------------------------- stage 2/5 grounding (advisory)
def _grounding_ctx():
    return {"input_data": {"actuals": {"revenue": 8903, "ebit": 1200}},
            "enrichment": {
                "competitors": [{"name": "Kilpailija Oy", "size_or_revenue": "50 M€"}],
                "market_signals": [{"signal": "kierros", "amount_or_information": "6 300 tEUR"}],
                "market_size": "1 200 M€"}}


def test_grounding_flags_fabricated_figure_but_never_blocks():
    out = {"sections": [{"id": "3", "blocks": [
        {"type": "paragraph", "text": "Kokonaismarkkina on arviomme mukaan 5 000 M€ tänä vuonna."}]}]}
    r = validators.run_validator(_v("stage_grounding.py"), out, _grounding_ctx())
    assert r["passed"]  # advisory — surfaces but never fails the run
    adv = next(c for c in r["checks"] if "advisory" in c["name"])
    assert "5 000" in adv["detail"]


def test_grounding_passes_sourced_and_derived_figures():
    out = {"sections": [{"id": "3", "blocks": [
        {"type": "paragraph", "text": "Liikevaihto 8 903 tEUR, markkina 1 200 M€, "
         "kilpailija 50 M€, rahoituskierros 6 300 tEUR vuonna 2024."}]}]}
    r = validators.run_validator(_v("stage_grounding.py"), out, _grounding_ctx())
    adv = next(c for c in r["checks"] if "advisory" in c["name"])
    assert r["passed"] and "all prose figures reconcile" in adv["detail"]


# ------------------------------------ stage-3 fabrication gate (BLOCKING)
def _gate(r):
    return next(c for c in r["checks"] if "invented euro figure" in c["name"])


def test_stage3_fabrication_gate_passes_traceable_and_blocks_invented():
    code = _v("stage3_numbers.py")
    ctx = {"input_data": {"actuals": {"revenue": 8903, "ebit": 1200, "equity": 3820}}}
    # every euro figure traces to input_data -> gate passes
    ok = {"sections": [{"id": "8", "blocks": [
        {"type": "paragraph", "text": "Oma pääoma 3 820 tEUR, liikevaihto 8 903 tEUR."}]}]}
    assert _gate(validators.run_validator(code, ok, ctx))["passed"]
    # a net figure derivable in one step (revenue - equity = 5 083) -> passes
    deriv = {"sections": [{"id": "8", "blocks": [
        {"type": "paragraph", "text": "Erotus on 5 083 tEUR."}]}]}
    assert _gate(validators.run_validator(code, deriv, ctx))["passed"]
    # an invented euro figure tracing to nothing -> BLOCKS the run
    bad = {"sections": [{"id": "8", "blocks": [
        {"type": "paragraph", "text": "Yhtiön piilotettu arvo on 987 654 tEUR."}]}]}
    rb = validators.run_validator(code, bad, ctx)
    assert not _gate(rb)["passed"] and not rb["passed"]


def test_stage3_fabrication_gate_ignores_years_and_percentages():
    code = _v("stage3_numbers.py")
    ctx = {"input_data": {"actuals": {"revenue": 8903}}}
    safe = {"sections": [{"id": "8", "blocks": [
        {"type": "paragraph", "text": "Vuonna 2027 kasvu oli 4 321 % ja 12 kuukautta."}]}]}
    assert _gate(validators.run_validator(code, safe, ctx))["passed"]  # year+% not euro figs


def test_source_url_cell_renders_clickable_domain_link():
    cell = render._num_cell("https://www.ytj.fi/yritys/123")
    assert '<a class="src"' in cell
    assert 'href="https://www.ytj.fi/yritys/123"' in cell
    assert ">ytj.fi</a>" in cell  # www stripped from visible text, full URL in href


def test_table_coerces_dict_rows_and_never_dumps_raw_dict():
    # Regression: the Virnex forecast table. Stage 3 emitted transposed rows as
    # {"row","values"} dicts; the old renderer stringified them to a raw '{...}'
    # dump in the first cell. Must render as aligned cells + padded header.
    b = {"type": "table", "title": "Ennusteen avainluvut",
         "columns": ["2026", "2027", "2028"],
         "rows": [{"row": "Liikevaihto", "values": ["9 821", "9 632", "9 583"]},
                  {"row": "EBIT", "values": ["-374", "-253", "-137"]}]}
    h = render._block_table(b)
    assert "{'row'" not in h and '{"row"' not in h
    assert h.count("<th>") == 4  # empty label column prepended + 3 year columns
    txt = render._norm_ws(render._strip_tags(h))
    assert "Liikevaihto 9 821 9 632 9 583" in txt
    # list-of-lists rows must pass through unchanged (no regression)
    h2 = render._block_table(
        {"type": "table", "columns": ["Vuosi", "LV"], "rows": [[2026, 100], [2027, 110]]})
    assert "{'" not in h2 and render._norm_ws(render._strip_tags(h2)).count("2026 100") == 1


def test_table_coerces_record_rows_aligns_by_column_name():
    # Virnex risk register: rows keyed by column NAMES, emitted out of column
    # order — must align to the header by name, not by dict insertion order.
    b = {"type": "table", "columns": ["Riski", "Vaikutus"],
         "rows": [{"Vaikutus": "iso", "Riski": "maksuvalmius"}]}
    h = render._block_table(b)
    txt = render._norm_ws(render._strip_tags(h))
    assert txt.endswith("maksuvalmius iso")  # Riski cell first, Vaikutus second
    assert "{'" not in h


def test_table_pads_ragged_rows_to_header_width():
    b = {"type": "table", "columns": ["Lähde", "Kuvaus", "Haettu"],
         "rows": [{"row": "url", "values": ["desc", "2026-06-30"]},
                  {"row": "url2", "values": ["only-desc"]}]}  # ragged: missing date
    h = render._block_table(b)
    body = h.split("<tbody>")[1]
    import re as _re
    counts = {tr.count("<td") for tr in _re.findall(r"<tr>(.*?)</tr>", body, _re.S)}
    assert counts == {3}  # both rows padded to 3 cells


def test_table_handles_rows_given_as_dict():
    b = {"type": "table", "columns": ["", "2026"],
         "rows": {"Liikevaihto": ["100"], "EBIT": ["10"]}}
    h = render._block_table(b)
    txt = render._norm_ws(render._strip_tags(h))
    assert "Liikevaihto 100" in txt and "EBIT 10" in txt and "{'" not in h


# ------------------------------------------ non-table block shape-drift hardening
def test_text_fields_flatten_list_and_dict_never_dump():
    assert render._norm_ws(render._strip_tags(
        render._block_paragraph({"text": ["Eka.", "Toka."]}))) == "Eka. Toka."
    assert "Otsikko" in render._block_heading({"text": {"text": "Otsikko"}})
    assert "{'" not in render._block_paragraph({"text": {"a": "x"}})


def test_metric_cards_accept_record_and_nested_value():
    h = render._block_metric_cards({"cards": {"Liikevaihto": "1 598", "EBIT": "210"}})
    t = render._norm_ws(render._strip_tags(h))
    assert "Liikevaihto" in t and "1 598" in t and "{'" not in h
    h2 = render._block_metric_cards({"cards": [{"label": "Arvo", "value": {"text": "669"}}]})
    assert "669" in h2 and "{'" not in h2


def test_key_value_accepts_record_dict():
    h = render._block_key_value({"title": "Avainluvut", "items": {"ROE": "12 %"}})
    t = render._norm_ws(render._strip_tags(h))
    assert "ROE" in t and "12 %" in t and "{'" not in h


def test_callout_renders_paragraphs_and_items():
    h = render._block_callout(
        {"variant": "kill", "title": "R", "paragraphs": ["Kappale."],
         "items": ["A", "B"], "ordered": True})
    assert "<ol" in h and "Kappale." in h and ">A</li>" in h and "{'" not in h


def test_scenario_drivers_accept_record_dict():
    h = render._block_scenario_table(
        {"scenario": "optimistinen", "value_teur": 5000, "probability_pct": 20,
         "drivers": {"EBIT-%": "8 %"}, "perusluvut": {}, "avainluvut": {}})
    t = render._norm_ws(render._strip_tags(h))
    assert "EBIT-%" in t and "8 %" in t and "{'" not in h


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


# --------------------------------------------------------------- golden report
def test_golden_renders_signature_visuals_and_markdown():
    h = render.render_html(_golden())
    lo = h.lower()
    # inline markdown emphasis must not leak raw asterisks to the client
    assert "<strong>oman pääoman arvo</strong>" in lo
    assert "<em>kannattava kasvu</em>" in lo
    assert "**" not in render._strip_tags(h)
    # standalone Snapshot page is not generated (design contract)
    assert "Snapshot" not in h
    # legal disclaimer always present
    assert "sijoitusneuvo" in lo


def test_disclaimer_injected_when_section_16_missing():
    rep = _golden()
    rep["sections"] = [s for s in rep["sections"] if str(s.get("id")) != "16"]
    h = render.render_html(rep)
    assert "Vastuuvapaus" in h
    assert "Valuatum Oy ei vastaa" in h


def _pdf_page_count(path):
    import re
    data = open(path, "rb").read()
    return len(re.findall(rb"/Type\s*/Page(?![s])", data))


def test_self_heal_retries_failed_stage(monkeypatch):
    import asyncio
    from app import runner, seed, store

    seed.ensure_seeded()
    pid = store.list_pipelines()[0]["id"]
    rid = store.create_run(pid, {"meta": {"company_name": "X"}}, False)
    p = store.get_pipeline(pid)
    calls = {"n": 0, "correction": None}

    async def fake_exec(stage, ctx, inp, ident, params, correction=None):
        calls["n"] += 1
        if calls["n"] == 1:
            return {**runner._base(stage), "status": "validation_failed",
                    "parsed_json": {"scoring": {}}, "validator_passed": False,
                    "validator_report": {"passed": False, "checks": [
                        {"name": "missing X", "passed": False, "detail": "needs Y"}]},
                    "cost_usd": 0.0, "tokens_prompt": 0, "tokens_completion": 0}
        calls["correction"] = correction  # retry must carry the failure feedback
        return {**runner._base(stage), "status": "ok", "parsed_json": {"scoring": {}},
                "validator_passed": True, "cost_usd": 0.0,
                "tokens_prompt": 0, "tokens_completion": 0}

    monkeypatch.setattr(runner, "_execute_stage", fake_exec)
    run = store.get_run(rid)

    async def drive():
        async for _ in runner.run_stages(run, p["stages"], only=3):
            pass

    asyncio.run(drive())
    assert calls["n"] == 2  # failed once, retried
    # the retry was feedback-driven: it received the failing check as correction
    assert calls["correction"] and "missing X" in calls["correction"]["feedback"]
    s3 = [r for r in store.get_run(rid)["results"] if r["order"] == 3][0]
    assert s3["status"] == "ok"  # self-healed
    # the auto-fix is recorded in the checklist
    names = [c["name"] for c in (s3.get("validator_report") or {}).get("checks", [])]
    assert any("Automaattinen korjaus" in n for n in names)


def test_deliver_gate_blocks_unhealthy_run_unless_forced():
    from starlette.testclient import TestClient
    from app import main, seed, store

    seed.ensure_seeded()
    pid = store.list_pipelines()[0]["id"]
    rid = store.create_run(pid, {"meta": {"company_name": "X"}}, True)
    store.upsert_result(rid, {"order": 6, "name": "s6", "status": "ok", "parsed_json": {
        "report_type": "ai_valuation_report", "cover": {"headline_value": "1 tEUR"},
        "machine_readable": {}, "sections": [{"id": "1", "title": "T", "blocks": [
            {"type": "paragraph", "text": "ok"}]}]}})
    store.upsert_result(rid, {"order": 3, "name": "s3", "status": "validation_failed",
                              "parsed_json": {"sections": []}})
    store.set_run_status(rid, "error")
    with TestClient(main.app) as c:
        assert c.get(f"/api/runs/{rid}/readiness").json()["ready"] is False
        assert c.get(f"/api/runs/{rid}/report.html").status_code == 409   # gated
        assert c.get(f"/api/runs/{rid}/report.html?force=1").status_code == 200  # override


def test_delete_run_removes_run_and_results():
    from starlette.testclient import TestClient
    from app import main, seed, store

    seed.ensure_seeded()
    pid = store.list_pipelines()[0]["id"]
    rid = store.create_run(pid, {"meta": {"company_name": "Del"}}, True)
    store.upsert_result(rid, {"order": 1, "name": "s1", "status": "error"})
    with TestClient(main.app) as c:
        assert c.get(f"/api/runs/{rid}").status_code == 200
        assert c.delete(f"/api/runs/{rid}").json()["ok"] is True
        assert c.get(f"/api/runs/{rid}").status_code == 404
    assert store.get_run(rid) is None


@pytest.mark.skipif(not render.pdf_available(), reason="no local Chromium")
def test_golden_pdf_has_no_blank_pages(tmp_path):
    rep = _golden()
    out = str(tmp_path / "g.pdf")
    render.render_pdf(rep, out)
    n_sections = len(render._ensure_disclaimer(render._ordered_sections(rep)))
    # cover + TOC + one page per section, and crucially NO trailing blank pages
    assert _pdf_page_count(out) == n_sections + 2
