"""Tests for the v2 pipeline: code assembler, validators, and — most importantly
— the cover guard (the cover headline corrupted twice in production, so this is
a required regression guard)."""
import json
import os

import pytest

from app import (
    assemble, dcf_detail, headcount_efficiency, render, revenue_anomalies, runner,
    scenario_compare, sensitivity, validators,
)

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
def test_cover_shows_scenario_expected_value_as_primary():
    # 2026-07-10: the cover headline flipped from the conservative base case
    # to the scenario expected value (kansisivu design 1c) — the base case is
    # still shown, but as a secondary bar in the scenario chart, not the hero.
    rep = _report()
    rep["machine_readable"]["scenarios"] = [
        {"name": "Pessimistinen", "value_teur": 0, "probability_pct": 20},
        {"name": "Konservatiivinen", "value_teur": 1000, "probability_pct": 55},
        {"name": "Optimistinen", "value_teur": 4200, "probability_pct": 25},
    ]
    html = render._cover(rep, render._derive(rep))
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


def test_cover_cleans_industry_and_omits_trust_boilerplate():
    rep = _report()
    rep["meta"]["industry"] = (
        "Ei tiedossa (input-datassa industry ja industry_code puuttuvat; "
        "julkinen lähde mukaan ohjelmistokehitys- ja digitaalisten palveluiden toimiala)"
    )
    rep["confidence"] = {
        "level": "Matala",
        "deciding_rule": "Terminaaliarvon dominanssi ja tietopuutteet.",
    }
    rep["_provenance"] = {"run_id": "cf3ce067"}
    text = render._norm_ws(render._strip_tags(render.render_html(rep)))
    assert "Ei tiedossa" not in text
    assert "input-datassa" not in text
    assert "Ohjelmistokehitys- ja digitaalisten palveluiden toimiala" in text
    assert "Arvion luottamustaso" not in text
    assert "Raportti-ID" not in text
    assert "Laadittu automaattisesti" not in text
    assert "Tarkastanut ja hyväksynyt" not in text


def test_metric_cards_filter_confidence_card():
    html = render._block_metric_cards({"cards": [
        {"label": "Realistinen base case", "value": "2 352 tEUR"},
        {"label": "Luottamustaso", "value": "Matala – pitkä perustelu"},
    ]})
    text = render._norm_ws(render._strip_tags(html))
    assert "Realistinen base case" in text
    assert "Luottamustaso" not in text
    assert "pitkä perustelu" not in text


def test_wide_table_gets_compact_class():
    html = render._render_table(
        ["Erä", "2026", "2027", "2028", "2029", "2030", "2031"],
        [["FCFF", 100, 110, 120, 130, 140, 150]],
    )
    assert 'class="tbl wide"' in html


# --------------------------------------------------------------- mandate block
def test_mandate_block_renders_when_present():
    rep = _report()
    rep["meta"]["mandate"] = {
        "valuation_date": "2026-06-26", "purpose": "Indikatiivinen arviointi",
        "standard_of_value": "Käypä arvo jatkavan toiminnan periaatteella",
    }
    text = render._norm_ws(render._strip_tags(render.render_html(rep)))
    assert "Toimeksianto" in text
    assert "Indikatiivinen arviointi" in text
    assert "Käypä arvo jatkavan toiminnan periaatteella" in text


def test_mandate_block_absent_without_data():
    text = render._norm_ws(render._strip_tags(render.render_html(_report())))
    assert "Toimeksianto" not in text


# --------------------------------------------------------------- appendix + numbering
def test_toc_and_section_numbering_has_no_gap():
    # SECTION_ORDER has no id "7" by design — with every canonical section
    # present, the displayed numbering must still run 1..N with no gap,
    # rather than showing the raw id (which would jump 6 -> 8).
    from app.runner import SECTION_ORDER
    rep = {"meta": {"company_name": "X"},
           "cover": {"headline_value": "1 tEUR", "base_case_value": "1 tEUR"},
           "sections": [{"id": sid, "title": f"T{sid}", "blocks": []} for sid in SECTION_ORDER]}
    html = render.render_html(rep)
    text = render._norm_ws(render._strip_tags(html))
    assert "7 T8" in text  # id "8" is the 7th real section — no jump to "8 T8"
    assert "8 T8" not in text


def test_appendix_divider_appears_once_before_appendix_sections():
    rep = {"meta": {"company_name": "X"},
           "cover": {"headline_value": "1 tEUR", "base_case_value": "1 tEUR"},
           "sections": [{"id": "1", "title": "A", "blocks": []},
                        {"id": "14", "title": "B", "blocks": []},
                        {"id": "15", "title": "LÄHTEET", "blocks": []},
                        {"id": "16", "title": "METODOLOGIA", "blocks": []},
                        {"id": "17", "title": "LIITE", "blocks": []}]}
    html = render.render_html(rep)
    marker = '<section class="page appendix-divider">'
    assert html.count(marker) == 1
    assert html.index(marker) > html.index(">B</h2>")
    assert html.index(marker) < html.index(">LÄHTEET</h2>")


def test_section_refs_remapped_from_internal_id_to_display_number():
    # The LLM writes cross-refs in internal-id space; SECTION_ORDER skips id 7,
    # so id 9 (DCF) must render as "osio 8", id 17 (liite) as "osio 16", while
    # ids 1-6 are unchanged. Rendering (_inline) then turns each ref into a
    # clickable #sec-N deep link — a reader hitting a restated figure should be
    # able to jump straight back to where it was first derived (Esa's "mistä
    # tuo 256 tEUR tuli?" complaint, 2026-07-10).
    from app.runner import SECTION_ORDER
    sections = [{"id": sid, "title": f"T{sid}", "blocks": []} for sid in SECTION_ORDER]
    sections[0]["blocks"] = [
        {"type": "paragraph", "text": "DCF-erittely osiossa 9 ja liite osiossa 17."},
        {"type": "paragraph", "text": "Historia esitetään osiossa 5."},
    ]
    render._resolve_section_refs(sections)
    assert render._inline(sections[0]["blocks"][0]["text"]) == (
        'DCF-erittely <a href="#sec-8" class="secref">osiossa 8</a> ja liite '
        '<a href="#sec-16" class="secref">osiossa 16</a>.'
    )
    assert render._inline(sections[0]["blocks"][1]["text"]) == (
        'Historia esitetään <a href="#sec-5" class="secref">osiossa 5</a>.'
    )


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


# --------------------------------------------------------------- sensitivity
def _engine_input_data():
    # Clean end-of-year discounting (wacc=10%) so exponents come out exactly
    # 1 and 2 — easy to hand-verify. cumulative_discounted_fcff[0]=400 and
    # equity_value_before_floor=450 are picked ground truths (bridge_adj=50).
    return {
        "valuation_engine": {
            "wacc_parameters": {"wacc_pct": 10.0},
            "dcf": {
                "years": [2026, 2027],
                "ebit": [400.0, 495.0],
                "depreciation_total": [20.0, 25.0],
                "taxes_paid": [-80.0, -99.0],
                "tax_fin_expenses": [1.0, 1.0],
                "tax_fin_income": [0.0, 0.0],
                "change_in_working_capital": [-10.0, -15.0],
                "operating_cash_flow": [331.0, 407.0],
                "change_in_non_interest_bearing_financial_liabilities": [0.0, 0.0],
                "gross_capex": [-231.0, -297.0],
                "free_operating_cash_flow": [100.0, 110.0],
                "other_items_fcf": [0.0, 0.0],
                "fcff": [100.0, 110.0],
                "discounted_fcff": [100 / 1.1, 110 / 1.1 ** 2],
                "cumulative_discounted_fcff": [400.0, 200.0],
                "bridge": {"interest_bearing_debt": -10.0, "cash": 60.0},
                "equity_value_before_floor": 450.0,
            },
            "eva": {"noplat": [320.0, 396.0]},
        },
        "forecast": {
            "years": [2026, 2027],
            "net_sales": [5000.0, 5500.0],
            "net_sales_growth_pct": [10.0, 10.0],
            "ebitda": [500.0, 620.0],
            "ebit": [400.0, 495.0],
            "ebit_pct": [8.0, 9.0],
        },
        "forecast_parameters": {
            "tax_rate_pct": [20.0, 20.0],
            "capex_pct_of_sales": [3.0, 3.2],
            "working_capital": {
                "trade_receivables_pct_of_sales": [12.0, 12.0],
                "trade_payables_pct_of_sales": [7.0, 7.0],
            },
        },
    }


def test_sensitivity_returns_both_matrices_when_data_available():
    blocks = sensitivity.build_sensitivity_blocks(_engine_input_data())
    assert len(blocks) == 2
    for b in blocks:
        assert b["chart_type"] == "heatmap_or_matrix"
        assert len(b["x_axis"]) == 5
        assert len(b["series"]) == 5
        assert all(len(row["values"]) == 5 for row in b["series"])


def test_sensitivity_wacc_growth_center_cell_matches_ground_truth():
    blocks = sensitivity.build_sensitivity_blocks(_engine_input_data())
    wg = next(b for b in blocks if b["chart_id"] == "wacc_growth_sensitivity")
    center = wg["series"][2]["values"][2]  # middle row/col = base wacc/growth
    assert abs(center - 450.0) <= 1.0


def test_sensitivity_revenue_ebit_center_cell_matches_ground_truth():
    blocks = sensitivity.build_sensitivity_blocks(_engine_input_data())
    rm = next(b for b in blocks if b["chart_id"] == "revenue_ebit_sensitivity")
    center = rm["series"][2]["values"][2]  # base revenue x base EBIT-%
    assert abs(center - 450.0) <= 1.0


def test_sensitivity_higher_wacc_means_lower_value():
    blocks = sensitivity.build_sensitivity_blocks(_engine_input_data())
    wg = next(b for b in blocks if b["chart_id"] == "wacc_growth_sensitivity")
    low_wacc_row = wg["series"][0]["values"][2]
    high_wacc_row = wg["series"][-1]["values"][2]
    assert low_wacc_row > high_wacc_row


def test_sensitivity_returns_empty_without_dcf_data():
    assert sensitivity.build_sensitivity_blocks({}) == []
    assert sensitivity.build_sensitivity_blocks(
        {"valuation_engine": {"dcf": {}}}) == []


def test_assemble_injects_sensitivity_blocks_into_section_11():
    run = {"results": [
        {"order": 0, "status": "ok", "parsed_json": _engine_input_data()},
        {"order": 4, "status": "ok", "parsed_json": {"sections": [
            {"id": "11", "title": "HERKKYYS", "blocks": [{"type": "heading", "text": "x"}]}]}},
        {"order": 6, "status": "ok", "parsed_json": {
            "report_type": "ai_valuation_report", "cover": {"headline_value": "1"},
            "sections": [{"id": "1"}]}},
    ]}
    rep = assemble.assemble(run)
    sec11 = next(s for s in rep["sections"] if s["id"] == "11")
    types = [b["type"] for b in sec11["blocks"]]
    assert types == ["heading", "chart", "chart"]


def test_assemble_replaces_existing_sensitivity_matrix_without_duplicating():
    run = {"results": [
        {"order": 0, "status": "ok", "parsed_json": _engine_input_data()},
        {"order": 4, "status": "ok", "parsed_json": {"sections": [
            {"id": "11", "title": "HERKKYYS", "blocks": [
                {"type": "heading", "text": "x"},
                {"type": "chart", "chart_type": "heatmap_or_matrix",
                 "chart_id": "wacc_growth_sensitivity", "series": []}]}]}},
        {"order": 6, "status": "ok", "parsed_json": {
            "report_type": "ai_valuation_report", "cover": {"headline_value": "1"},
            "sections": [{"id": "1"}]}},
    ]}
    rep = assemble.assemble(run)
    sec11 = next(s for s in rep["sections"] if s["id"] == "11")
    ids = [b.get("chart_id") for b in sec11["blocks"] if b.get("type") == "chart"]
    assert ids.count("wacc_growth_sensitivity") == 1  # pre-existing copy stripped
    assert ids.count("revenue_ebit_sensitivity") == 1
    assert [b["type"] for b in sec11["blocks"]] == ["heading", "chart", "chart"]


def test_report_qa_flags_duplicate_block_and_sensitivity_drift_advisory():
    from app import report_qa
    tbl = {"type": "table", "columns": ["a", "b"], "rows": [["x", "1"], ["y", "2"]]}
    rep = {"cover": {"base_case_value": "669 tEUR"}, "sections": [
        {"id": "9", "blocks": [tbl]},
        {"id": "11", "blocks": [dict(tbl), {
            "type": "chart", "chart_id": "wacc_growth_sensitivity",
            "series": [{"values": [1, 2, 3]}, {"values": [4, 526, 6]}, {"values": [7, 8, 9]}]}]},
    ]}
    w = report_qa.warnings(rep)
    assert any("duplikaattilohko" in x for x in w)
    assert any("herkkyysmatriisin" in x for x in w)  # center 526 vs base 669 -> drift
    assert report_qa.warnings({}) == [] and report_qa.warnings(None) == []  # never crashes


def test_wacc_risk_caveat_fires_on_weak_credit_and_negative_equity_but_not_healthy():
    weak = {
        "valuation_engine": {"wacc_parameters": {
            "cost_of_debt_pct": 5.0, "liquidity_premium_pct": 1.0, "target_d_to_de_pct": 30.0}},
        "credit_risk": {"rating": ["B"], "company_bankruptcy_risk_pct": [4.53]},
        "forecast": {"equity_excl_capital_loans": [-208.0, -598.0, -1288.0]},
    }
    blocks = dcf_detail.build_wacc_risk_caveat_blocks(weak)
    assert blocks and blocks[0]["variant"] == "warning"
    assert "luottoluokitus B" in blocks[0]["text"]
    healthy = {
        "valuation_engine": {"wacc_parameters": {"cost_of_debt_pct": 4.0}},
        "credit_risk": {"rating": ["A"], "company_bankruptcy_risk_pct": [0.3]},
        "forecast": {"equity_excl_capital_loans": [500.0, 600.0]},
    }
    assert dcf_detail.build_wacc_risk_caveat_blocks(healthy) == []
    assert dcf_detail.build_wacc_risk_caveat_blocks({}) == []


def test_terminal_margin_range_shows_alt_at_best_historical_margin():
    idata = {
        "valuation_engine": {"dcf": {
            "discounted_fcff": [-112.0], "cumulative_discounted_fcff": [1633.0],
            "equity_value_before_floor": 669.0}},
        "forecast": {"ebit_pct": [10.0, 23.3]},
        "actuals": {"income_statement": {"ebit": [354.0, -100.0], "net_sales": [3770.0, 1000.0]}},
    }
    blocks = sensitivity.build_terminal_margin_range_blocks(idata)
    tbl = next(b for b in blocks if b.get("table_id") == "deterministic_terminal_margin_range")
    assert len(tbl["rows"]) == 2
    assert "23,3" in tbl["rows"][0][0] and "9,4" in tbl["rows"][1][0]
    assert sensitivity.build_terminal_margin_range_blocks({}) == []  # no data -> no block


def test_assemble_refreshes_stale_baked_dcf_detail_blocks():
    # A run whose stored section 9 already carries deterministic DCF blocks (baked
    # in by an earlier assemble). Re-assemble must REPLACE them, not skip — so a
    # re-render reflects current code and never duplicates the FCFF table.
    stale = [
        {"type": "table", "table_id": "deterministic_dcf_fcff_drivers",
         "columns": ["Erä", "2026"], "rows": [["EBIT", "1"]]},
        {"type": "table", "table_id": "deterministic_dcf_equity_bridge",
         "columns": ["Erä", "Arvo"], "rows": [["Yritysarvo (EV)", "1"]]},
        {"type": "callout", "title": "Mistä diskontattu FCFF tulee?", "text": "vanha teksti"},
    ]
    run = {"results": [
        {"order": 0, "status": "ok", "parsed_json": _engine_input_data()},
        {"order": 6, "status": "ok", "parsed_json": {
            "report_type": "ai_valuation_report", "cover": {"headline_value": "1"},
            "sections": [{"id": "9", "title": "DCF", "blocks": list(stale)}]}},
    ]}
    rep = assemble.assemble(run)
    sec9 = next(s for s in rep["sections"] if s["id"] == "9")
    fcff = [b for b in sec9["blocks"] if b.get("table_id") == "deterministic_dcf_fcff_drivers"]
    assert len(fcff) == 1  # replaced, not duplicated
    callout = next(b for b in sec9["blocks"]
                   if b.get("type") == "callout" and "diskontattu FCFF" in (b.get("title") or ""))
    assert "verohyödystä" in callout["text"]  # fresh code text, not the stale "vanha teksti"


def test_dcf_detail_table_uses_years_as_columns_and_explains_discounting():
    blocks = dcf_detail.build_dcf_detail_blocks(_engine_input_data())
    table = next(b for b in blocks if b.get("table_id") == "deterministic_dcf_fcff_drivers")
    assert table["columns"] == ["Erä", "2026", "2027", "TRM"]
    labels = [r[0] for r in table["rows"]]
    assert "EBIT" in labels
    assert "- Maksetut verot" in labels
    assert "- Käyttöpääoman muutos" in labels
    assert "- Bruttoinvestoinnit" in labels
    assert "Vapaa kassavirta (FCFF)" in labels
    assert "Diskontattu FCFF" in labels
    assert "Kumulatiivinen diskontattu FCFF" in labels
    assert next(r for r in table["rows"] if r[0] == "Diskontattu FCFF")[-1] == "218"
    bridge = next(b for b in blocks if b.get("table_id") == "deterministic_dcf_equity_bridge")
    assert ["Yritysarvo (EV)", "400"] in bridge["rows"]
    assert ["Oman pääoman arvo ennen lattiaa", "450"] in bridge["rows"]
    callout = next(b for b in blocks if b.get("type") == "callout")
    assert "diskontattu FCFF" in callout["title"]
    assert "EBITistä" in callout["text"]
    assert "nykyarvorivi" in callout["text"]


def test_cumulative_discounted_fcff_row_is_a_true_forward_running_sum():
    blocks = dcf_detail.build_dcf_detail_blocks(_engine_input_data())
    table = next(b for b in blocks if b.get("table_id") == "deterministic_dcf_fcff_drivers")
    _p = lambda s: int(str(s).replace(" ", "").replace("−", "-"))
    disc = next(r for r in table["rows"] if r[0] == "Diskontattu FCFF")
    cum = next(r for r in table["rows"] if r[0] == "Kumulatiivinen diskontattu FCFF")
    run = 0.0
    for d_cell, c_cell in zip(disc[1:-1], cum[1:-1]):  # forecast cols (drop label + TRM)
        run += _p(d_cell)
        assert abs(run - _p(c_cell)) <= 1  # each cumulative cell = running sum so far
    assert cum[-1] == "400"  # terminal col = EV (engine cumulative_discounted_fcff[0])


def test_dcf_detail_surfaces_partial_dcf_but_stays_silent_when_absent():
    # Partly-populated dcf (drivers present, core FCFF series missing) -> warning.
    partial = {"valuation_engine": {"dcf": {
        "years": [2026, 2027], "ebit": [10, 12], "gross_capex": [-2, -3],
    }}}
    blocks = dcf_detail.build_dcf_detail_blocks(partial)
    assert len(blocks) == 1 and blocks[0]["type"] == "callout"
    assert blocks[0]["variant"] == "warning"
    # Wholly empty dcf (legit no-forecast case) -> stay silent.
    assert dcf_detail.build_dcf_detail_blocks({"valuation_engine": {"dcf": {}}}) == []
    assert dcf_detail.build_dcf_detail_blocks({}) == []


def _headcount_input_data():
    return {
        "headcount": {"years": [2023, 2024], "values": [5, 6]},
        "actuals": {
            "years": [2023, 2024],
            "income_statement": {"ebit": [50.0, 60.0]},
            # per_employee is fetched with money=True (tEUR/employee) — the live
            # report bug was fetching these unscaled, so every ratio rounded to
            # "0"/"-0" (66 200 €/employee came back as raw 0.0662).
            "per_employee": {
                "net_sales": [66.2, 83.2],
                "value_added": [52.4, 50.0],
                "personnel_costs": [-47.6, -55.4],
                "ebitda": [4.8, -5.4],
                "net_earnings": [18.2, -11.4],
            },
        },
    }


def test_headcount_efficiency_table_has_years_as_columns_and_ebit_per_employee():
    blocks = headcount_efficiency.build_headcount_efficiency_blocks(_headcount_input_data())
    table = next(b for b in blocks if b.get("table_id") == "deterministic_headcount_efficiency")
    assert table["columns"] == ["Erä", "2023", "2024"]
    labels = [r[0] for r in table["rows"]]
    assert labels == [
        "Henkilöstö", "Liikevaihto / henkilö", "Jalostusarvo / henkilö",
        "Henkilökulut / henkilö", "Käyttökate / henkilö", "Liiketulos / henkilö",
        "Nettotulos / henkilö",
    ]
    # ebit_per_employee is derived locally: 50.0 tEUR / 5 employees * 1000 = 10 000 €
    assert next(r for r in table["rows"] if r[0] == "Liiketulos / henkilö") == ["Liiketulos / henkilö", "10 000", "10 000"]
    assert next(r for r in table["rows"] if r[0] == "Henkilöstö") == ["Henkilöstö", "5", "6"]
    # per_employee fields scale tEUR -> EUR the same way (66.2 tEUR -> 66 200 €)
    assert next(r for r in table["rows"] if r[0] == "Liikevaihto / henkilö") == ["Liikevaihto / henkilö", "66 200", "83 200"]


def test_headcount_efficiency_returns_empty_without_headcount():
    assert headcount_efficiency.build_headcount_efficiency_blocks({}) == []
    assert headcount_efficiency.build_headcount_efficiency_blocks(
        {"headcount": {"years": [2023], "values": [None]}}) == []


def test_assemble_injects_headcount_efficiency_into_section_5():
    run = {"results": [
        {"order": 0, "status": "ok", "parsed_json": _headcount_input_data()},
        {"order": 2, "status": "ok", "parsed_json": {"sections": [
            {"id": "5", "title": "HISTORIALLINEN KEHITYS", "blocks": [
                {"type": "heading", "text": "x"}]}]}},
        {"order": 6, "status": "ok", "parsed_json": {
            "report_type": "ai_valuation_report", "cover": {"headline_value": "1"},
            "sections": [{"id": "1"}]}},
    ]}
    rep = assemble.assemble(run)
    sec5 = next(s for s in rep["sections"] if s["id"] == "5")
    assert sec5["blocks"][-1]["table_id"] == "deterministic_headcount_efficiency"


def test_assemble_replaces_old_vertical_dcf_table_with_deterministic_detail():
    run = {"results": [
        {"order": 0, "status": "ok", "parsed_json": _engine_input_data()},
        {"order": 3, "status": "ok", "parsed_json": {"sections": [
            {"id": "9", "title": "DCF", "blocks": [
                {"type": "table", "title": "WACC-parametrit", "columns": ["Erä", "Arvo"],
                 "rows": [["WACC", "10 %"]]},
                {"type": "table", "title": "Vapaat kassavirrat ja nykyarvo",
                 "columns": ["Vuosi", "FCFF", "Diskontattu FCFF"],
                 "rows": [[2026, 100, 91], [2027, 110, 91],
                          ["Terminaalijakso", "", 218], ["Yhteensä (EV)", "", 400]]},
                {"type": "table", "title": "Yritysarvosta oman pääoman arvoon",
                 "columns": ["Erä", "Arvo"],
                 "rows": [["Yritysarvo (EV)", 400], ["Oman pääoman arvo", 450]]},
                {"type": "paragraph", "text": "DCF-menetelmän tulos ennen floor-käsittelyä: 450 tEUR."},
            ]}]}},
        {"order": 6, "status": "ok", "parsed_json": {
            "report_type": "ai_valuation_report", "cover": {"headline_value": "1"},
            "sections": [{"id": "1"}]}}],
    }
    rep = assemble.assemble(run)
    sec9 = next(s for s in rep["sections"] if s["id"] == "9")
    table_ids = [b.get("table_id") for b in sec9["blocks"] if isinstance(b, dict)]
    assert "deterministic_dcf_fcff_drivers" in table_ids
    assert "deterministic_dcf_equity_bridge" in table_ids
    assert not any(
        isinstance(b, dict) and b.get("columns") == ["Vuosi", "FCFF", "Diskontattu FCFF"]
        for b in sec9["blocks"]
    )


def test_assemble_replaces_old_horizontal_llm_fcff_table_too():
    # Live-report bug: OSIO 9 still tells the model to write its own
    # years-as-columns FCFF build-up table (same shape as the deterministic
    # one), which the narrow vuosi/fcff/diskontattu column check never caught
    # — so both tables rendered back to back on the DCF page.
    llm_table = {
        "type": "table", "title": "DCF-laskelma: FCFF ja nykyarvo vuosittain",
        "columns": ["Erä", "2026E", "2027E"],
        "rows": [
            ["EBIT", -11.4, -2.2],
            ["Liiketoiminnan kassavirta", 7.3, 7.4],
            ["Diskontattu FCFF", 9.4, 6.7],
        ],
    }
    run = {"results": [
        {"order": 0, "status": "ok", "parsed_json": _engine_input_data()},
        {"order": 3, "status": "ok", "parsed_json": {"sections": [
            {"id": "9", "title": "DCF", "blocks": [llm_table]}]}},
        {"order": 6, "status": "ok", "parsed_json": {
            "report_type": "ai_valuation_report", "cover": {"headline_value": "1"},
            "sections": [{"id": "1"}]}}],
    }
    rep = assemble.assemble(run)
    sec9 = next(s for s in rep["sections"] if s["id"] == "9")
    table_ids = [b.get("table_id") for b in sec9["blocks"] if isinstance(b, dict)]
    assert "deterministic_dcf_fcff_drivers" in table_ids
    assert not any(b is llm_table for b in sec9["blocks"])
    assert sum(1 for b in sec9["blocks"] if isinstance(b, dict)
               and "dcf-laskelma" in str(b.get("title", "")).lower()) == 1


def test_assemble_normalizes_dcf_eva_equivalence_in_sections_and_scoring():
    run = {"results": [
        {"order": 0, "status": "ok", "parsed_json": _engine_input_data()},
        {"order": 3, "status": "ok", "parsed_json": {
            "scoring": {
                "method_scoring": [
                    {"method": "DCF", "status": "hyväksytty", "weight_pct": 40, "value_teur": 450},
                    {"method": "EVA", "status": "hyväksytty", "weight_pct": 60, "value_teur": 430},
                ],
                "weighted_base_case_teur": 438,
            },
            "sections": [
                {"id": "8", "title": "ARVONMÄÄRITYS", "blocks": [
                    {"type": "table", "title": "Painotettu arvonmääritys",
                     "columns": ["Menetelmä", "Arvo", "Paino", "Kontribuutio"],
                     "rows": [["DCF", 450, "40 %", 180], ["EVA", 430, "60 %", 258]]},
                    {"type": "paragraph", "text": "DCF ja EVA painotetaan 40/60 menetelmien pisteillä."},
                    {"type": "chart", "title": "Menetelmien antamat arvot", "chart_type": "bar",
                     "x_axis": ["DCF", "EVA"], "series": [{"values": [450, 430]}]},
                ]},
                {"id": "10", "title": "EVA", "blocks": [
                    {"type": "paragraph", "text": "EVA antaa arvoksi 430 tEUR."},
                ]},
            ]}},
        {"order": 6, "status": "ok", "parsed_json": {
            "report_type": "ai_valuation_report", "cover": {"headline_value": "1"},
            "sections": [{"id": "1"}]}}],
    }
    rep = assemble.assemble(run)
    methods = {m["method"]: m for m in rep["_scoring"]["method_scoring"]}
    assert methods["DCF"]["weight_pct"] == 100
    assert methods["EVA"]["status"] == "viite"
    assert methods["EVA"]["weight_pct"] == 0
    assert methods["EVA"]["value_teur"] == 450.0
    assert rep["_scoring"]["weighted_base_case_teur"] == 450.0
    sec8 = next(s for s in rep["sections"] if s["id"] == "8")
    assert any(b.get("table_id") == "deterministic_dcf_eva_equivalence"
               for b in sec8["blocks"] if isinstance(b, dict))
    assert not any(b.get("title") == "Menetelmien antamat arvot" for b in sec8["blocks"] if isinstance(b, dict))
    sec10 = next(s for s in rep["sections"] if s["id"] == "10")
    assert sec10["blocks"][1]["table_id"] == "deterministic_eva_reconciliation"
    assert ["Oman pääoman arvo ennen lattiaa", "450"] in sec10["blocks"][1]["rows"]


def test_verottaja_crosscheck_income_and_substance_branches():
    from app import valuation_equivalence as veq

    # Income-dominant: avg(100,200,300)=200 -> tuottoarvo 200/0.15=1333; equity 500;
    # tuottoarvo>substanssi -> kaypa=(1333+500)/2=917.
    inc_data = {"actuals": {
        "income_statement": {"net_income": [50, 100, 200, 300]},  # last 3 = 100,200,300
        "balance_sheet": {"equity": [400, 500]},
    }}
    blocks = veq._verottaja_blocks(inc_data, 450)
    rows = {r[0]: r[1] for r in blocks[0]["rows"]}
    assert blocks[0]["table_id"] == "deterministic_valuation_crosscheck"
    assert rows["Tuottoarvo (3 v:n keskitulos / 15 %)"] == "1 333"
    assert rows["Substanssiarvo (oma pääoma)"] == "500"
    assert rows["Verottajan käypä arvo"] == "917"

    # Substance-dominant: tiny earnings, big equity -> kaypa = substanssiarvo.
    sub_data = {"actuals": {
        "income_statement": {"net_income": [10, 10, 10]},
        "balance_sheet": {"equity": [1000]},
    }}
    rows2 = {r[0]: r[1] for r in veq._verottaja_blocks(sub_data, 450)[0]["rows"]}
    assert rows2["Verottajan käypä arvo"] == "1 000"

    # Real Valuatum model-data keys (net_earnings / equity_excl_capital_loans) must
    # fire too — with only the legacy keys read, the block silently no-opped on
    # every production report.
    real_data = {"actuals": {
        "income_statement": {"net_earnings": [50, 100, 200, 300]},
        "balance_sheet": {"equity_excl_capital_loans": [400, 500]},
    }}
    rows_real = {r[0]: r[1] for r in veq._verottaja_blocks(real_data, 450)[0]["rows"]}
    assert rows_real["Tuottoarvo (3 v:n keskitulos / 15 %)"] == "1 333"
    assert rows_real["Substanssiarvo (oma pääoma)"] == "500"
    assert rows_real["Verottajan käypä arvo"] == "917"

    # Distressed company: negative equity floors substanssiarvo (and käypä arvo) at 0.
    neg_data = {"actuals": {
        "income_statement": {"net_earnings": [-40, -30, -20]},
        "balance_sheet": {"equity_excl_capital_loans": [-500]},
    }}
    rows_neg = {r[0]: r[1] for r in veq._verottaja_blocks(neg_data, -450)[0]["rows"]}
    assert rows_neg["Substanssiarvo (oma pääoma)"] == "0"
    assert rows_neg["Verottajan käypä arvo"] == "0"

    # Missing data -> no block (graceful skip).
    assert veq._verottaja_blocks({}, 450) == []
    assert veq._verottaja_blocks({"actuals": {"income_statement": {"net_earnings": [10]}}}, 450) == []


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


def _scenario_block(equity, ratio):
    return {"type": "scenario_table", "scenario": "realistinen",
            "value_teur": 1000, "probability_pct": 50,
            "perusluvut": {"columns": ["Tunnusluku", "2034"],
                            "rows": [["Oma pääoma", equity]]},
            "avainluvut": {"columns": ["Tunnusluku", "2034"],
                            "rows": [["Omavaraisuusaste", ratio]]}}


def test_stage4_validator_passes_consistent_equity_ratio():
    out = _s4()
    out["sections"] = [{"id": "11", "blocks": [_scenario_block(150, 22.5)]}]
    r = validators.run_validator(_v("stage4_scenarios.py"), out, {})
    assert r["passed"], r


def test_stage4_validator_catches_positive_equity_negative_ratio():
    # The reported bug: perusskenaarion oma pääoma positiivinen mutta
    # omavaraisuusaste negatiivinen samassa sarakkeessa.
    out = _s4()
    out["sections"] = [{"id": "11", "blocks": [_scenario_block(150, -27.8)]}]
    r = validators.run_validator(_v("stage4_scenarios.py"), out, {})
    assert not r["passed"]


def _zero_scenario_block(ebits, debt=17669):
    # Supercell bug shape: pessimistic scenario claims 0 tEUR while its own
    # perusluvut hold EBIT positive in every column.
    cols = ["2026E", "2030E", "2035E"]
    return {"type": "scenario_table", "scenario": "pessimistinen",
            "value_teur": 0, "probability_pct": 20,
            "perusluvut": {"columns": ["Erä"] + cols,
                            "rows": [["Liikevaihto", 1694537, 1694537, 1694537],
                                     ["Liikevoitto"] + ebits,
                                     ["Korolliset velat", debt, debt, debt]]},
            "avainluvut": {"columns": ["Erä"] + cols, "rows": []}}


def test_stage4_validator_catches_zero_value_scenario_with_positive_ebit():
    out = _s4()
    out["sections"] = [{"id": "11", "blocks": [
        _zero_scenario_block([239886, 239886, 239886])]}]
    ctx = {"input_data": {"valuation_engine": {
        "wacc_parameters": {"wacc_pct": 9.46},
        "dcf": {"bridge": {"cash": 653764.0}}}}}
    r = validators.run_validator(_v("stage4_scenarios.py"), out, ctx)
    assert not r["passed"]
    c = next(c for c in r["checks"] if "nolla-arvoinen" in c["name"])
    assert not c["passed"]
    assert "ristiriidassa" in c["detail"]


def test_stage4_validator_allows_zero_value_scenario_with_collapsing_ebit():
    # A genuinely distressed path (EBIT goes negative) may be worth zero.
    out = _s4()
    out["sections"] = [{"id": "11", "blocks": [
        _zero_scenario_block([50, -20, -90])]}]
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


def test_stage6_validator_requires_expected_value_as_cover_headline():
    # 2026-07-10: the cover headline is now the scenario expected value, not
    # the conservative base case — the base case is still mandatory on the
    # cover, but as the secondary figure.
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
    wrong_headline = json.loads(json.dumps(good))
    wrong_headline["cover"]["headline_value"] = "1 000 tEUR"  # base case, not expected value
    assert not validators.run_validator(_v("stage6_final.py"), wrong_headline, ctx)["passed"]


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


def test_grounding_surfaces_public_claims_without_inline_source_marks():
    out = {"sections": [{"id": "3", "blocks": [
        {"type": "paragraph", "text": "Markkina kasvaa nopeasti ja kilpailija on selvästi suurempi."}]}]}
    r = validators.run_validator(_v("stage_grounding.py"), out, _grounding_ctx())
    chk = next(c for c in r["checks"] if "inline source marks" in c["name"])
    assert r["passed"]  # advisory — should not block the run
    assert "lack '(lähde: ...)'" in chk["detail"]

    sourced = {"sections": [{"id": "3", "blocks": [
        {"type": "paragraph", "text": "Markkina kasvaa nopeasti (lähde: example.com, 2026-07-02)."}]}]}
    r2 = validators.run_validator(_v("stage_grounding.py"), sourced, _grounding_ctx())
    chk2 = next(c for c in r2["checks"] if "inline source marks" in c["name"])
    assert chk2["detail"] == "ok"


# --------------------------------------------------- revenue anomaly detection
def _jump_drop_input():
    return {
        "meta": {"company_name": "Virnex Group Oy", "y_tunnus": "1234567-8"},
        "actuals": {
            "years": [2022, 2023, 2024],
            "income_statement": {"net_sales": [600, 9000, 4200]},
        },
    }


def test_revenue_anomaly_detector_flags_large_jump_and_drop():
    brief = revenue_anomalies.detect(_jump_drop_input())
    assert brief["has_anomaly"]
    assert [a["direction"] for a in brief["anomalies"]] == ["increase", "decrease"]
    assert brief["anomalies"][0]["change_pct"] == 1400.0
    assert any("acquisition" in term for term in brief["anomalies"][0]["search_terms"])
    assert any("divestment" in term for term in brief["anomalies"][1]["search_terms"])


def test_stage0_contribution_exposes_revenue_anomaly_context():
    ctx = {}
    runner._contribute(ctx, {"order": 0, "name": "Vaihe 0 - FAKTAT"}, _jump_drop_input())
    assert ctx["input_data"]["meta"]["company_name"] == "Virnex Group Oy"
    assert ctx["revenue_anomalies"]["has_anomaly"]


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


# ------------------------------------ stage-3 DCF/EVA bridge reconciliation
def _bridge_ctx():
    return {"input_data": {"valuation_engine": {
        "wacc_parameters": {"wacc_pct": 9.46},
        "dcf": {
            "years": [2025, 2026], "fcff": [100.0, 110.0],
            "discounted_fcff": [95.0, 90.0],
            "cumulative_discounted_fcff": [2000.0, 1905.0],
            "equity_value_before_floor": 2450.0,
        },
        "eva": {"invested_capital": 1200.0, "discounted_eva": [50.0, 40.0],
                "equity_value_before_floor": 2450.0},
    }}}


def _bridge_output(**bridge_over):
    # Internally consistent AND matches _bridge_ctx()'s engine ground truth:
    # pv_forecast (185) + pv_terminal (1815) = EV (2000) = cumulative[0];
    # EV (2000) + debt (-31, engine's ib_debt_nega_prev_year is pre-negated) +
    # cash (481) = equity (2450) = engine's own figure.
    dcf_bridge = {"pv_forecast_period_teur": 185.0, "pv_terminal_value_teur": 1815.0,
                  "enterprise_value_teur": 2000.0, "interest_bearing_debt_teur": -31.0,
                  "cash_teur": 481.0, "equity_value_before_floor_teur": 2450.0}
    dcf_bridge.update(bridge_over)
    return {"scoring": {
        "method_scoring": [
            {"method": "DCF", "status": "hyväksytty", "weight_pct": 100, "value_teur": 2450},
            {"method": "EVA", "status": "viite", "weight_pct": 0, "value_teur": 2450}],
        "dcf_bridge": dcf_bridge,
        "eva_bridge": {"invested_capital_teur": 1200.0, "pv_explicit_eva_teur": 90.0,
                       "pv_terminal_eva_teur": 1160.0, "equity_value_before_floor_teur": 2450.0},
    }, "sections": []}


def _bridge_chk(r, needle):
    return next(c for c in r["checks"] if needle in c["name"])


def test_stage3_dcf_bridge_reconciles():
    r = validators.run_validator(_v("stage3_numbers.py"), _bridge_output(), _bridge_ctx())
    assert _bridge_chk(r, "PV(ennustejakso) + PV(terminaali) = EV")["passed"]
    assert _bridge_chk(r, "EV + korolliset velat")["passed"]
    assert _bridge_chk(r, "stated equity_value_before_floor matches valuation_engine.dcf")["passed"]


def test_stage3_dcf_bridge_catches_internal_arithmetic_error():
    # The reported production bug: EV + debt + cash != the report's own stated
    # equity value (a 200+ tEUR unexplained gap).
    bad = _bridge_output(equity_value_before_floor_teur=2662.0)
    r = validators.run_validator(_v("stage3_numbers.py"), bad, _bridge_ctx())
    assert not _bridge_chk(r, "EV + korolliset velat")["passed"]


def test_stage3_dcf_bridge_catches_drift_from_engine_ground_truth():
    # Internally self-consistent (185+1315=1500=EV; 1500-31+481=1950=equity)
    # but both numbers have drifted away from what the engine actually gave
    # (EV 2000, equity 2450) — a "report contradicts its own inputs" bug,
    # distinct from an internal-arithmetic error.
    bad = _bridge_output(enterprise_value_teur=1500.0, pv_terminal_value_teur=1315.0,
                          equity_value_before_floor_teur=1950.0)
    r = validators.run_validator(_v("stage3_numbers.py"), bad, _bridge_ctx())
    assert not _bridge_chk(r, "stated EV matches valuation_engine.dcf.cumulative_discounted_fcff[0]")["passed"]


def test_stage3_eva_bridge_catches_hidden_terminal_component():
    out = _bridge_output()
    # EVA total states 2300 but the visible components (1200 + 90) only add to
    # 1290 if pv_terminal_eva is wrong — exactly the reported "hidden terminal
    # EVA" bug (a total that can't be verified from the shown line items).
    out["scoring"]["eva_bridge"]["pv_terminal_eva_teur"] = 100.0
    r = validators.run_validator(_v("stage3_numbers.py"), out, _bridge_ctx())
    assert not _bridge_chk(r, "EVA bridge: investoitu pääoma")["passed"]


def test_stage3_catches_dcf_eva_engine_divergence():
    ctx = _bridge_ctx()
    ctx["input_data"]["valuation_engine"]["eva"]["equity_value_before_floor"] = 2300.0
    r = validators.run_validator(_v("stage3_numbers.py"), _bridge_output(), ctx)
    assert not _bridge_chk(r, "DCF/EVA equivalence: equity values match")["passed"]


def test_stage3_catches_eva_double_weighting():
    out = _bridge_output()
    out["scoring"]["method_scoring"][1].update(
        {"status": "hyväksytty", "weight_pct": 50, "value_teur": 2450}
    )
    r = validators.run_validator(_v("stage3_numbers.py"), out, _bridge_ctx())
    assert not _bridge_chk(r, "EVA is reference-only")["passed"]


def test_stage3_weight_sum_check():
    out = _bridge_output()
    out["scoring"]["method_scoring"][0]["weight_pct"] = 70  # 70 + 50 = 120%
    r = validators.run_validator(_v("stage3_numbers.py"), out, _bridge_ctx())
    assert not _bridge_chk(r, "menetelmien painot")["passed"]


def test_stage3_bridge_checks_skip_gracefully_when_absent():
    r = validators.run_validator(_v("stage3_numbers.py"), {"scoring": {}, "sections": []}, {})
    assert r["passed"], r


def test_user_input_number_is_allowed_not_flagged_as_fabrication():
    # A figure the USER supplied (via context.user_input) must be treated as a
    # legitimate assumption, not a fabrication — the feature would be unusable
    # otherwise. Same figure absent from user_input is blocked (prev test).
    code = _v("stage3_numbers.py")
    out = {"sections": [{"id": "8", "blocks": [
        {"type": "paragraph", "text": "Käyttäjän oletuksen mukainen strateginen arvo 250 000 tEUR."}]}]}
    ctx_with = {"input_data": {"actuals": {"revenue": 8903}},
                "user_input": "Oletus: strateginen arvo 250 000 tEUR yrityskaupassa."}
    assert _gate(validators.run_validator(code, out, ctx_with))["passed"]
    ctx_without = {"input_data": {"actuals": {"revenue": 8903}}}
    assert not _gate(validators.run_validator(code, out, ctx_without))["passed"]


def test_source_url_cell_renders_clickable_domain_link():
    cell = render._num_cell("https://www.ytj.fi/yritys/123")
    assert '<a class="src"' in cell
    assert 'href="https://www.ytj.fi/yritys/123"' in cell
    assert ">ytj.fi</a>" in cell  # www stripped from visible text, full URL in href


def test_key_value_source_url_renders_clickable_domain_link():
    h = render._block_key_value({"items": [
        {"key": "Toimiala", "value": "Ohjelmistokehitys", "source": "https://www.ytj.fi/yritys/123"}
    ]})
    assert '<a class="src"' in h
    assert 'href="https://www.ytj.fi/yritys/123"' in h
    assert ">ytj.fi</a>" in h


def test_inline_citation_links_to_url_found_elsewhere_in_report():
    rep = _report()
    rep["sections"].append({
        "id": "8", "title": "PROFIILI", "blocks": [
            {"type": "paragraph", "text": "Kilpailija Solita on selvästi suurempi "
                                           "(lähde: kauppalehti.fi, 2026-06-30)."},
        ],
    })
    rep["sections"].append({
        "id": "15", "title": "LÄHTEET", "blocks": [
            {"type": "table", "columns": ["Lähde", "Tieto"],
             "rows": [["https://www.kauppalehti.fi/uutiset/x", "Solita-uutinen"]]},
        ],
    })
    html = render.render_html(rep)
    assert '<a class="src" href="https://www.kauppalehti.fi/uutiset/x">kauppalehti.fi</a>' in html
    assert "(lähde: <a" in html and ", 2026-06-30)" in html


def test_inline_citation_stays_plain_text_when_url_unknown():
    # No matching URL anywhere in the report — must not fabricate a link.
    # (Reset the ContextVar explicitly: render_html() normally does this per
    # call, but this test calls a block renderer directly.)
    render._source_domain_map.set({})
    h = render._block_paragraph(
        {"text": "Kilpailija Solita on selvästi suurempi (lähde: kauppalehti.fi, 2026-06-30)."})
    assert "<a" not in h
    assert "(lähde: kauppalehti.fi, 2026-06-30)" in h


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
    assert h.count("</th>") == 4  # empty label column prepended + 3 year columns
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
    rep["cover"]["headline_value"] = ""
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


def test_raw_float_cells_render_finnish_formatted():
    # Supercell bug: LLM tables carried full-precision engine floats which
    # rendered as US-formatted garbage ("4289677.53181") in a Finnish PDF.
    h = render._render_table(["Erä", "2026", "2030"],
                             [["Liikevaihto", 4289677.53181, 15024511.43214],
                              ["Kasvu-%", 44.4, 13.11],
                              ["Vuosi (koskematon)", 2026, 2030]])
    assert "4 289 678" in h
    assert "15 024 511" in h
    assert "4289677" not in h
    assert "44,4" in h and "13,11" in h
    assert ">2026<" in h and ">2030<" in h  # year ints stay unformatted


def test_clean_replaces_leaked_schema_tokens():
    # Supercell bug: raw pipeline field names leaked into client prose.
    s = render._clean("julkinen lähde market_signals ja asiakkaan "
                      "client_reported_signals ovat tyhjät (tukee_kasvua); "
                      "no_of_shares_total ja fair_value_dcf puuttuvat; "
                      "revenue_anomaly_review: ei selitystä")
    for leaked in ("market_signals", "client_reported_signals", "tukee_kasvua",
                   "no_of_shares_total", "fair_value_dcf", "revenue_anomaly_review"):
        assert leaked not in s, s
    assert "markkinasignaalit" in s
    assert "osakemäärä" in s


def test_stage3_validator_catches_unlabeled_year_table():
    # Supercell p10: history table with year columns but bare-number rows.
    out = {"scoring": {"method_scoring": [
        {"method": "DCF", "status": "hyväksytty", "weight_pct": 100, "value_teur": 100}]},
        "sections": [{"id": "5", "blocks": [
            {"type": "table", "title": "Avainluvut historialta",
             "columns": ["2021", "2022", "2023", "2024", "2025"],
             "rows": [[1795282, 1550932, 1424584, 1694537, 2970611],
                      [44.43, 43.7, 41.49, 14.56, 41.48]]}]}]}
    r = validators.run_validator(_v("stage3_numbers.py"), out, {})
    c = next(c for c in r["checks"] if "rivinimet" in c["name"])
    assert not c["passed"]


def test_stage3_validator_accepts_labeled_year_table():
    out = {"scoring": {"method_scoring": [
        {"method": "DCF", "status": "hyväksytty", "weight_pct": 100, "value_teur": 100}]},
        "sections": [{"id": "5", "blocks": [
            {"type": "table", "title": "Avainluvut historialta",
             "columns": ["Erä", "2021", "2022"],
             "rows": [["Liikevaihto", 1795282, 1550932],
                      ["EBITDA-%", 44.43, 43.7]]}]}]}
    r = validators.run_validator(_v("stage3_numbers.py"), out, {})
    c = next(c for c in r["checks"] if "rivinimet" in c["name"])
    assert c["passed"], c


def test_stage4_validator_catches_unlabeled_scenario_table():
    out = _s4()
    out["sections"] = [{"id": "11", "blocks": [
        {"type": "scenario_table", "scenario": "realistinen",
         "value_teur": 1000, "probability_pct": 50,
         "perusluvut": {"columns": ["2026E", "2030E", "2035E"],
                         "rows": [[4289678, 15024511, 17862833],
                                  [1499260, 3441496, 1380522]]},
         "avainluvut": {"columns": ["2026E", "2030E"], "rows": []}}]}]
    r = validators.run_validator(_v("stage4_scenarios.py"), out, {})
    c = next(c for c in r["checks"] if "skenaariotaulukoiden riveillä" in c["name"])
    assert not c["passed"]


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

    async def fake_exec(stage, ctx, inp, ident, params, correction=None, rid=None):
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


def test_clone_run_reuses_stage0_and_links_parent():
    from app import seed, store

    seed.ensure_seeded()
    pid = store.list_pipelines()[0]["id"]
    rid = store.create_run(pid, {"meta": {"company_name": "Parent"}}, True,
                           params={"user_input": "alkuperäinen"})
    store.upsert_result(rid, {"order": 0, "name": "Vaihe 0", "status": "ok",
                              "parsed_json": {"meta": {"company_name": "Parent"}}})
    child_id = store.clone_run(rid, params={
        "clarifications": [{"id": "tam", "answer": "15 M€"}]})
    child = store.get_run(child_id)
    assert child["parent_run_id"] == rid
    assert child["input_data"] == {"meta": {"company_name": "Parent"}}
    assert child["params"]["user_input"] == "alkuperäinen"  # parent params merged
    assert child["params"]["clarifications"][0]["answer"] == "15 M€"
    s0 = [r for r in child["results"] if r["order"] == 0]
    assert s0 and s0[0]["parsed_json"]["meta"]["company_name"] == "Parent"


def test_access_key_quota_is_atomic_and_bounded():
    from app import store

    k = store.create_access_key("Matti", generations_limit=2)
    key = k["key"]
    assert key.startswith("exp_")
    assert store.consume_generation(key) is True
    assert store.consume_generation(key) is True
    assert store.consume_generation(key) is False       # 3rd blocked at the cap
    assert store.consume_generation("exp_does_not_exist") is False
    assert store.get_access_key(key)["generations_used"] == 2
    # generations_limit == 0 means unlimited (e.g. a CEO/internal key).
    u = store.create_access_key("CEO", generations_limit=0)["key"]
    assert all(store.consume_generation(u) for _ in range(5))


def test_rate_limit_buckets_are_independent(monkeypatch):
    """/api/orders is called from the visitor's BROWSER (real per-user IPs) but
    /api/public/checkout-generate is called SERVER-SIDE by the client site, so
    every customer shares one Vercel egress IP. They used to share the 5/hour
    order limiter, which silently turned it into a global 5-reports-per-hour
    ceiling (2026-07-10). Separate buckets: exhausting one must not touch the
    other, and 'checkout' must allow far more than 5."""
    from app import main

    monkeypatch.setattr(main, "_RATE_HITS", {})
    ip = "203.0.113.7"

    # 'order' is the tight browser-facing bucket: 5 per hour, then closed.
    assert all(main._rate_ok("order", ip) for _ in range(5))
    assert main._rate_ok("order", ip) is False

    # Exhausting 'order' must NOT close 'checkout' for the same IP — that
    # conflation is the exact bug.
    assert main._rate_ok("checkout", ip) is True
    # …and 'checkout' has room for a real hour of sales, not 5.
    assert all(main._rate_ok("checkout", ip) for _ in range(39))
    assert main._rate_ok("checkout", ip) is False

    # Different IPs never share a bucket.
    assert main._rate_ok("order", "198.51.100.2") is True


def test_expert_me_reports_paid_round_availability(monkeypatch):
    """The client must not offer a 'buy an extra round' button when Stripe is
    unconfigured — /round2/checkout would 503 into a red error."""
    from starlette.testclient import TestClient
    from app import main, seed, store

    seed.ensure_seeded()
    monkeypatch.setattr(main, "_APP_TOKEN", "admintok")
    monkeypatch.setattr(main, "STRIPE_SECRET_KEY", "")
    monkeypatch.setenv("ROUND2_MAX_PER_RUN", "5")
    key = store.create_access_key("E", generations_limit=1)["key"]
    c = TestClient(main.app)

    me = c.get("/api/expert/me", headers={"Authorization": f"Bearer {key}"}).json()
    assert me["paid_rounds_enabled"] is False
    assert me["free_rounds_per_report"] == 5

    monkeypatch.setattr(main, "STRIPE_SECRET_KEY", "sk_test_x")
    me = c.get("/api/expert/me", headers={"Authorization": f"Bearer {key}"}).json()
    assert me["paid_rounds_enabled"] is True


def test_expert_key_is_capped_and_scoped(monkeypatch):
    from starlette.testclient import TestClient
    from app import main, seed, store

    seed.ensure_seeded()
    monkeypatch.setattr(main, "_APP_TOKEN", "admintok")
    # Don't actually run the Valuatum export / pipeline in a unit test.
    monkeypatch.setattr(main, "_start_bg", lambda *a, **k: True)
    c = TestClient(main.app)
    pid = store.list_pipelines()[0]["id"]
    key = store.create_access_key("E", generations_limit=1)["key"]
    exp = {"Authorization": f"Bearer {key}"}
    gen = {
        "fid": 12345,
        "company_name": "Testi Oy",
        "pipeline_id": pid,
        "industry_text": "Software",
        "industry_code": "62.100",
        "industry_id": 123,
        "industry_tree": [{"code": "62", "name": "IT"}],
        "delivery_email": "owner@testi.fi",
    }

    # One generation allowed via the self-serve endpoint, consuming quota.
    r = c.post("/api/expert/generate", json=gen, headers=exp)
    assert r.status_code == 200
    rid = r.json()["run_id"]
    own = c.get(f"/api/runs/{rid}", headers=exp)
    assert own.status_code == 200   # owns it
    assert own.json()["params"]["industry_text"] == "Software"
    assert own.json()["params"]["industry_code"] == "62.100"
    assert own.json()["params"]["industry_id"] == 123
    assert own.json()["params"]["industry_tree"] == [{"code": "62", "name": "IT"}]
    assert own.json()["params"]["delivery_email"] == "owner@testi.fi"
    # Quota now exhausted → second generation blocked.
    assert c.post("/api/expert/generate", json=gen, headers=exp).status_code == 403
    # Deny-by-default: raw run creation + admin surfaces are off-limits.
    assert c.post("/api/runs", json={"pipeline_id": pid, "stop_on_failure": False},
                  headers=exp).status_code == 403
    assert c.post("/api/reseed", headers=exp).status_code == 403
    assert c.get("/api/access-keys", headers=exp).status_code == 403
    assert c.get("/api/runs", headers=exp).status_code == 403          # list-all
    # Cannot read a run owned by someone else (admin-created, no access_key).
    other = store.create_run(pid, {"meta": {}}, False)
    assert c.get(f"/api/runs/{other}", headers=exp).status_code == 403
    # Self-check reflects usage.
    me = c.get("/api/expert/me", headers=exp).json()
    assert me["remaining"] == 0 and me["generations_limit"] == 1


def test_public_company_search_dedupes_and_needs_no_auth(monkeypatch):
    from starlette.testclient import TestClient
    from app import main

    monkeypatch.setattr(main, "_APP_TOKEN", "admintok")

    async def fake_search(q):
        return [
            {"fid": 1, "company_name": "Testi Oy", "company_code": "184362",
             "industry_text": "Software", "industry_code": None, "industry_id": None,
             "analyst_name": "Analyst A"},
            {"fid": 2, "company_name": "Testi Oy", "company_code": "184362",
             "industry_text": "Software", "industry_code": None, "industry_id": None,
             "analyst_name": "Profinder"},  # same company, preferred model
            {"fid": 3, "company_name": "Testi Group Oy", "company_code": "184362K",
             "industry_text": "Software", "industry_code": None, "industry_id": None,
             "analyst_name": "Analyst B"},  # different (K-suffix) company
        ]

    monkeypatch.setattr(main.valuatum, "search_company", fake_search)
    c = TestClient(main.app)  # no Authorization header
    r = c.get("/api/public/company-search?q=Testi")
    assert r.status_code == 200
    out = r.json()
    assert len(out) == 2  # one row per distinct company_code
    assert out[0]["fid"] == 2  # Profinder model preferred over the plain one


def test_public_checkout_generate_mints_key_and_starts_run(monkeypatch):
    from starlette.testclient import TestClient
    from app import main, seed, store

    seed.ensure_seeded()
    monkeypatch.setattr(main, "_APP_TOKEN", "admintok")  # public path must bypass this
    monkeypatch.setattr(main, "_start_bg", lambda *a, **k: True)

    async def fake_search(q):
        assert q == "1612398-8"
        return [{"fid": 184362, "company_name": "Valuatum Oy", "company_code": "184362",
                  "industry_text": "Software", "industry_code": "62.100",
                  "industry_id": 1, "industry_tree": None, "analyst_name": "Profinder"}]

    monkeypatch.setattr(main.valuatum, "search_company", fake_search)
    c = TestClient(main.app)  # no Authorization header at all
    body = {
        "business_id": "1612398-8", "company_name": "Valuatum Oy",
        "email": "owner@valuatum.com", "user_input": "lisatieto",
        "stripe_session_id": "cs_test_123",
    }
    r = c.post("/api/public/checkout-generate", json=body)
    assert r.status_code == 200
    data = r.json()
    key, rid = data["key"], data["run_id"]
    assert key.startswith("exp_") or key  # minted, not admin
    run = store.get_run(rid)
    assert run["access_key"] == key
    assert run["params"]["delivery_email"] == "owner@valuatum.com"
    assert run["params"]["company_code"] == "184362"

    # Idempotent: replaying the same Stripe session must not mint a second key
    # or run (the success page can be loaded more than once for one payment).
    r2 = c.post("/api/public/checkout-generate", json=body)
    assert r2.status_code == 200
    assert r2.json() == {"run_id": rid, "key": key}

    # The minted key can now drive the normal expert flow (view/round2) itself.
    me = c.get("/api/expert/me", headers={"Authorization": f"Bearer {key}"})
    assert me.status_code == 200
    assert me.json()["remaining"] == 0  # the checkout generation already spent it


def test_public_checkout_generate_retries_after_failed_run(monkeypatch):
    # Regression: the demo flow's session id is deterministic on
    # (company, email), unlike a real Stripe session id — so a person whose
    # first attempt hit e.g. the spend cap must NOT be handed that same dead
    # run forever on every later retry (Turun Tislaamo incident, 2026-07-10).
    from starlette.testclient import TestClient
    from app import main, seed, store

    seed.ensure_seeded()
    monkeypatch.setattr(main, "_APP_TOKEN", "admintok")
    monkeypatch.setattr(main, "_start_bg", lambda *a, **k: True)

    async def fake_search(q):
        return [{"fid": 184362, "company_name": "Valuatum Oy", "company_code": "184362",
                  "industry_text": "Software", "industry_code": "62.100",
                  "industry_id": 1, "industry_tree": None, "analyst_name": "Profinder"}]

    monkeypatch.setattr(main.valuatum, "search_company", fake_search)
    c = TestClient(main.app)
    body = {
        "business_id": "1612398-8", "company_name": "Turun Tislaamo Oy",
        "email": "colleague@example.com", "user_input": "",
        "stripe_session_id": "demo:existing:Turun Tislaamo Oy:colleague@example.com",
    }
    r = c.post("/api/public/checkout-generate", json=body)
    first_rid = r.json()["run_id"]
    store.set_run_status(first_rid, "error")  # simulate the spend-cap failure

    r2 = c.post("/api/public/checkout-generate", json=body)
    assert r2.status_code == 200
    second_rid = r2.json()["run_id"]
    assert second_rid != first_rid  # fresh run, not the same dead one every time


def test_paid_extra_round_checkout_and_redeem(monkeypatch):
    from starlette.testclient import TestClient
    from app import main, seed, store

    seed.ensure_seeded()
    monkeypatch.setattr(main, "_start_bg", lambda *a, **k: True)
    monkeypatch.setattr(main, "STRIPE_SECRET_KEY", "sk_test_x")
    monkeypatch.setenv("CLIENT_SITE_URL", "https://valuatum-arvonmaaritys.vercel.app")
    c = TestClient(main.app)  # APP_TOKEN unset -> admin
    pid = store.list_pipelines()[0]["id"]
    rid = store.create_run(pid, {"meta": {}}, True)
    store.upsert_result(rid, {"order": 1, "name": "enrichment", "status": "ok",
                              "parsed_json": {"business_thesis": {"one_line_thesis": "x"}}})

    created_session = {"id": "cs_test_1", "url": "https://checkout.stripe.com/pay/cs_test_1"}

    async def fake_create(**kw):
        assert kw["amount_cents"] == 500
        assert kw["metadata"]["rid"] == rid
        return created_session

    monkeypatch.setattr(main, "_stripe_create_checkout_session", fake_create)
    r = c.post(f"/api/runs/{rid}/round2/checkout",
               json={"clarifications": [], "clarifications_free_text": "lisätieto"})
    assert r.status_code == 200
    assert r.json() == {"checkout_url": created_session["url"]}

    # Pull the real token back out of the DB directly (checkout doesn't echo it).
    from app import db
    pending = db.query_one("SELECT * FROM pending_rounds WHERE run_id=?", (rid,))
    token = pending["token"]

    async def fake_get_paid(session_id):
        assert session_id == "cs_test_1"
        return {"payment_status": "paid", "metadata": {"token": token}}

    monkeypatch.setattr(main, "_stripe_get_checkout_session", fake_get_paid)
    r2 = c.post(f"/api/runs/{rid}/round2/redeem", json={"token": token, "stripe_session_id": "cs_test_1"})
    assert r2.status_code == 200
    child = store.get_run(r2.json()["run_id"])
    assert child["parent_run_id"] == rid
    assert child["params"]["clarifications_free_text"] == "lisätieto"

    # Redeeming twice must not run a second round (already consumed).
    r3 = c.post(f"/api/runs/{rid}/round2/redeem", json={"token": token, "stripe_session_id": "cs_test_1"})
    assert r3.status_code == 409

    # A second pending round, but Stripe reports it unpaid -> must not redeem.
    r4 = c.post(f"/api/runs/{rid}/round2/checkout",
                json={"clarifications": [], "clarifications_free_text": "toinen"})
    assert r4.status_code == 200
    pending2 = db.query_one(
        "SELECT * FROM pending_rounds WHERE run_id=? AND token!=?", (rid, token)
    )
    token2 = pending2["token"]

    async def fake_get_unpaid(session_id):
        return {"payment_status": "unpaid", "metadata": {"token": token2}}

    monkeypatch.setattr(main, "_stripe_get_checkout_session", fake_get_unpaid)
    r5 = c.post(f"/api/runs/{rid}/round2/redeem", json={"token": token2, "stripe_session_id": "cs_test_2"})
    assert r5.status_code == 402


def test_round2_captures_round1_enrichment_for_maximal_preserve(monkeypatch):
    from starlette.testclient import TestClient
    from app import main, seed, store

    seed.ensure_seeded()
    monkeypatch.setattr(main, "_start_bg", lambda *a, **k: True)  # don't run it
    c = TestClient(main.app)  # APP_TOKEN unset → admin
    pid = store.list_pipelines()[0]["id"]
    parent = store.create_run(pid, {"meta": {"company_name": "X"}}, True)
    store.upsert_result(parent, {"order": 1, "name": "enrichment", "status": "ok",
                                 "parsed_json": {"business_thesis": {"one_line_thesis": "keep me"}}})
    r = c.post(f"/api/runs/{parent}/round2",
               json={"clarifications": [], "clarifications_free_text": "uusi tieto"})
    assert r.status_code == 200
    child = store.get_run(r.json()["run_id"])
    # Round 1's enrichment is handed to round 2 so it refines instead of re-rolling.
    assert child["params"]["previous_enrichment"]["business_thesis"]["one_line_thesis"] == "keep me"
    assert child["params"]["clarifications_free_text"] == "uusi tieto"
    assert child["parent_run_id"] == parent


def test_round2_show_old_numbers_flag_flows_into_params(monkeypatch):
    from starlette.testclient import TestClient
    from app import main, seed, store

    seed.ensure_seeded()
    monkeypatch.setattr(main, "_start_bg", lambda *a, **k: True)
    c = TestClient(main.app)
    pid = store.list_pipelines()[0]["id"]
    parent = store.create_run(pid, {"meta": {"company_name": "X"}}, True)
    # Explicit opt-in lands in params (drives the round-2 old-numbers directive).
    r = c.post(f"/api/runs/{parent}/round2",
               json={"clarifications": [], "clarifications_free_text": "",
                     "show_old_numbers": True})
    assert r.status_code == 200
    assert store.get_run(r.json()["run_id"])["params"]["show_old_numbers"] is True
    # Omitted → defaults to False (clean report, no old numbers).
    r2 = c.post(f"/api/runs/{parent}/round2",
                json={"clarifications": [], "clarifications_free_text": ""})
    assert r2.status_code == 200
    assert store.get_run(r2.json()["run_id"])["params"]["show_old_numbers"] is False


def test_derive_company_code_appends_K_for_konserni():
    from app import valuatum as v

    # Parent model → plain dash-stripped code (Profinder returns parent statements).
    assert v._derive_company_code(
        {"meta": {"y_tunnus": "1612398-8", "level": "parent"}}, None) == "16123988"
    # Konserni model → K-suffixed so the Profinder backfill returns consolidated
    # statements instead of parent-level ones (verified live: 16123988K works).
    assert v._derive_company_code(
        {"meta": {"y_tunnus": "1612398-8", "level": "consolidated"}}, None) == "16123988K"
    # Operator override always wins — no forced K.
    assert v._derive_company_code(
        {"meta": {"y_tunnus": "1612398-8", "level": "consolidated"}}, "999") == "999"
    # No y-tunnus → no code.
    assert v._derive_company_code({"meta": {"level": "consolidated"}}, None) is None


def test_fmt_clarifications_renders_answers_and_empty_sentinel():
    from app import runner

    assert "ensimmäinen kierros" in runner._fmt_clarifications(None, None)
    txt = runner._fmt_clarifications(
        [{"id": "tam", "question": "Markkinan koko?", "answer": "15 M€"},
         {"id": "x", "answer": ""}],  # blank answer skipped
        "lisätieto")
    assert "Markkinan koko?: 15 M€" in txt
    assert "lisätieto" in txt
    assert "vahvistama" in txt.lower()


def test_gemini_chat_routes_to_google_not_openrouter(monkeypatch):
    import asyncio
    from app import openrouter

    calls = {}

    async def fake_google(**kwargs):
        calls["google"] = kwargs
        return {"text": "{}", "finish_reason": "stop", "tokens_prompt": 1,
                "tokens_completion": 1, "request_payload": {}}

    async def fake_openrouter(**kwargs):
        raise AssertionError("Gemini should not be sent to OpenRouter")

    monkeypatch.setattr(openrouter, "_google_chat", fake_google)
    monkeypatch.setattr(openrouter, "_openrouter_chat", fake_openrouter)

    res = asyncio.run(openrouter.chat(
        model="google/gemini-3.1-pro-preview",
        prompt="{}",
        expects_json=True,
        web_search=True,
    ))

    assert res["finish_reason"] == "stop"
    assert calls["google"]["model"] == "google/gemini-3.1-pro-preview"
    assert calls["google"]["web_search"] is True


def test_google_gemini_payload_uses_generate_content_and_search_tool(monkeypatch):
    import asyncio
    from app import openrouter

    calls = {}

    class FakeResponse:
        status_code = 200
        text = "{}"

        def json(self):
            return {
                "candidates": [{
                    "content": {"parts": [{"text": "{\"ok\": true}"}]},
                    "finishReason": "STOP",
                }],
                "usageMetadata": {
                    "promptTokenCount": 11,
                    "candidatesTokenCount": 7,
                    "thoughtsTokenCount": 3,
                },
            }

    class FakeAsyncClient:
        def __init__(self, timeout):
            calls["timeout"] = timeout

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def post(self, endpoint, headers, json):
            calls["endpoint"] = endpoint
            calls["headers"] = headers
            calls["payload"] = json
            return FakeResponse()

    monkeypatch.setattr(openrouter, "_google_headers", lambda: {
        "x-goog-api-key": "test-key",
        "Content-Type": "application/json",
    })
    monkeypatch.setattr(openrouter.httpx, "AsyncClient", FakeAsyncClient)

    res = asyncio.run(openrouter._google_chat(
        model="google/gemini-3.1-pro-preview",
        prompt="{\"task\":\"enrich\"}",
        max_tokens=1234,
        expects_json=True,
        web_search=True,
    ))

    assert "generativelanguage.googleapis.com" in calls["endpoint"]
    assert calls["endpoint"].endswith(
        "/models/gemini-3.1-pro-preview:generateContent"
    )
    assert calls["payload"]["generationConfig"]["responseMimeType"] == "application/json"
    assert calls["payload"]["generationConfig"]["maxOutputTokens"] == 1234
    assert calls["payload"]["tools"] == [{"google_search": {}}]
    assert calls["payload"]["contents"][0]["parts"][0]["text"] == "{\"task\":\"enrich\"}"
    assert res["text"] == "{\"ok\": true}"
    assert res["tokens_prompt"] == 11
    assert res["tokens_completion"] == 10
    assert res["request_payload"]["provider"] == "google"


def test_single_writer_seed_is_research_writer_split():
    from app import seed

    stages = seed._single_writer_stages()
    assert [s["order"] for s in stages] == [0, 1, 2]
    assert stages[1]["model"] == "google/gemini-3.1-pro-preview"
    assert stages[1]["web_search"] is True
    assert stages[1]["prompt_template"] == seed._load_prompt("1_enrichment.txt")
    assert stages[2]["model"] == "openai/gpt-5.6-sol"
    assert stages[2]["web_search"] is False
    assert stages[2]["input_mapping"]["enrichment"] == "Vaihe 1 enrichment"
    assert "{{enrichment}}" in stages[2]["prompt_template"]
    # Writer must emit a competitor + market section, not just read the data.
    writer_prompt = stages[2]["prompt_template"]
    assert "Kilpailijat ja kilpailuasema" in writer_prompt
    assert "enrichment.competitors" in writer_prompt


def test_legacy_single_writer_web_stage_migrates_to_research_writer_split():
    from app import seed, store

    seed.reseed_defaults(force=True)
    sw = next(
        p for p in store.list_pipelines()
        if p["name"] == seed.SINGLE_WRITER_PIPELINE_NAME
    )
    by_order = {s["order"]: s for s in sw["stages"]}

    # Simulate the old 2-stage experimental preset: FAKTAT + one web-search
    # writer at order 1. Normal boot should migrate this without requiring the
    # operator to force-reseed first.
    if 2 in by_order:
        store.delete_stage(by_order[2]["id"])
    legacy = {
        **by_order[1],
        "order": 1,
        "name": "Vaihe 1 - Koko raportti (yksi malli)",
        "model": "anthropic/claude-fable-5",
        "prompt_template": seed._load_prompt("singlewriter.txt"),
        "web_search": True,
        "max_tokens": 64000,
        "validator_code": seed._load_validator("stage6_final.py"),
        "input_mapping": {"input_data": "Vaihe 0 FAKTAT"},
    }
    store.update_stage(by_order[1]["id"], legacy)

    migrated = seed._ensure_single_writer_pipeline()
    migrated_by_order = {s["order"]: s for s in migrated["stages"]}

    assert sorted(migrated_by_order) == [0, 1, 2]
    assert migrated_by_order[1]["model"] == "google/gemini-3.1-pro-preview"
    assert migrated_by_order[1]["web_search"] is True
    assert "business_thesis" in migrated_by_order[1]["prompt_template"]
    assert migrated_by_order[2]["model"] == "openai/gpt-5.6-sol"
    assert migrated_by_order[2]["web_search"] is False
    assert "{{enrichment}}" in migrated_by_order[2]["prompt_template"]


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


def test_public_order_intake_and_honeypot():
    from starlette.testclient import TestClient
    from app import main, store

    with TestClient(main.app) as c:
        r = c.post("/api/orders", json={
            "company": "Testi Oy / 1234567-8", "email": "omistaja@testi.fi",
            "user_input": "WACC 10 %, myynti 2 v sisällä"})
        assert r.status_code == 200 and r.json()["ok"]
        oid = r.json()["order_id"]
        assert any(o["id"] == oid and o["status"] == "open"
                   for o in store.list_orders())
        # honeypot filled -> pretend success, store nothing
        r2 = c.post("/api/orders", json={
            "company": "Bot Oy", "email": "bot@spam.io", "website": "http://x"})
        assert r2.status_code == 200 and "order_id" not in r2.json()
        assert not any(o["company"] == "Bot Oy" for o in store.list_orders())
        # bad email rejected by schema
        assert c.post("/api/orders", json={
            "company": "X Oy", "email": "eiemail"}).status_code == 422
        store.set_order_status(oid, "delivered")
        assert any(o["id"] == oid and o["status"] == "delivered"
                   for o in store.list_orders())


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


def test_round2_cap_bites_across_a_refinement_chain(monkeypatch):
    # Regression: the old check counted a run's OWN children, which is always
    # 0 for a freshly created run — so refining round 2's result again (a
    # chain, not repeated calls on the same run) never hit the cap. Confirm
    # round 2 then round 3 succeed (2 refinements included) and round 4 403s.
    from starlette.testclient import TestClient
    from app import main, seed, store

    seed.ensure_seeded()
    monkeypatch.setattr(main, "_start_bg", lambda *a, **k: True)
    monkeypatch.setenv("ROUND2_MAX_PER_RUN", "2")
    c = TestClient(main.app)
    pid = store.list_pipelines()[0]["id"]
    r1 = store.create_run(pid, {"meta": {"company_name": "X"}}, True)

    body = {"clarifications": [], "clarifications_free_text": ""}
    resp2 = c.post(f"/api/runs/{r1}/round2", json=body)
    assert resp2.status_code == 200
    r2 = resp2.json()["run_id"]

    resp3 = c.post(f"/api/runs/{r2}/round2", json=body)
    assert resp3.status_code == 200
    r3 = resp3.json()["run_id"]

    resp4 = c.post(f"/api/runs/{r3}/round2", json=body)
    assert resp4.status_code == 429


def test_round2_cap_skipped_for_unlimited_key(monkeypatch):
    # An unlimited expert key (generations_limit <= 0, i.e. the CEO's
    # iterate-until-good key) is not bound by ROUND2_MAX_PER_RUN; limited
    # keys and keyless calls stay capped (see the chain test above).
    from starlette.testclient import TestClient
    from app import main, seed, store

    seed.ensure_seeded()
    monkeypatch.setattr(main, "_APP_TOKEN", "admintok")
    monkeypatch.setattr(main, "_start_bg", lambda *a, **k: True)
    monkeypatch.setenv("ROUND2_MAX_PER_RUN", "2")
    c = TestClient(main.app)
    pid = store.list_pipelines()[0]["id"]
    key = store.create_access_key("CEO", generations_limit=0)["key"]
    hdr = {"Authorization": f"Bearer {key}"}
    rid = store.create_run(pid, {"meta": {"company_name": "X"}}, True,
                           access_key=key)

    body = {"clarifications": [], "clarifications_free_text": ""}
    for _ in range(3):  # one past the cap that bites for limited keys
        resp = c.post(f"/api/runs/{rid}/round2", json=body, headers=hdr)
        assert resp.status_code == 200
        rid = resp.json()["run_id"]


def test_normalize_query_detects_ytunnus_vs_name():
    from app import valuatum

    assert valuatum._normalize_query("1612398-8") == ("code", "1612398-8")
    assert valuatum._normalize_query("16123988") == ("code", "1612398-8")
    assert valuatum._normalize_query("16123988K") == ("code", "1612398-8")
    assert valuatum._normalize_query("Valuatum Oy") == ("name", "Valuatum Oy")


def test_search_company_maps_models_to_fid_candidates(monkeypatch):
    import asyncio
    from app import valuatum

    monkeypatch.setenv("VALUATUM_TOKEN", "tok")

    class FakeResponse:
        def raise_for_status(self):
            pass

        def json(self):
            return [{
                "companyId": 1,
                "companyName": "Valuatum Oy",
                "companyCode": "1612398-8",
                "industryText": "Software",
                "industryCode": "62.100",
                "industryId": 123,
                "industryTree": [{"id": 12, "code": "62", "name": "IT"}],
                "models": [
                    {"followedModelId": "184362", "analystName": "A"},
                    {"followedModelId": 999, "analystName": "B"},
                ],
            }]

    class FakeClient:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def get(self, url, params=None, headers=None):
            assert url == valuatum.COMPANY_URL
            assert headers["authorization"] == "Bearer tok"
            return FakeResponse()

    monkeypatch.setattr(valuatum.httpx, "AsyncClient", FakeClient)
    out = asyncio.run(valuatum.search_company("1612398-8"))
    assert out == [
        {"fid": 184362, "company_name": "Valuatum Oy", "company_code": "1612398-8",
         "industry_text": "Software", "industry_code": "62.100", "industry_id": 123,
         "industry_tree": [{"id": 12, "code": "62", "name": "IT"}],
         "analyst_name": "A"},
        {"fid": 999, "company_name": "Valuatum Oy", "company_code": "1612398-8",
         "industry_text": "Software", "industry_code": "62.100", "industry_id": 123,
         "industry_tree": [{"id": 12, "code": "62", "name": "IT"}],
         "analyst_name": "B"},
    ]


def test_apply_company_metadata_hydrates_stage0_meta():
    from app import valuatum

    data = {"meta": {"company_name": "Valuatum Oy", "industry": None, "industry_code": None}}
    out = valuatum._apply_company_metadata(data, {
        "industry_text": "62.100 Computer programming activities",
        "industry_code": "62.100",
        "industry_id": 123,
        "industry_tree": [{"code": "62", "name": "Computer programming"}],
    })

    assert out["meta"]["industry"] == "62.100 Computer programming activities"
    assert out["meta"]["industry_code"] == "62.100"
    assert out["meta"]["industry_id"] == 123
    assert out["meta"]["industry_tree"] == [{"code": "62", "name": "Computer programming"}]


def test_company_search_endpoint_is_expert_reachable(monkeypatch):
    from starlette.testclient import TestClient
    from app import main, store

    monkeypatch.setattr(main, "_APP_TOKEN", "admintok")

    async def fake_search(query):
        return [{"fid": 184362, "company_name": "Valuatum Oy",
                  "company_code": "1612398-8", "industry_text": None,
                  "industry_code": None, "industry_id": None, "industry_tree": None,
                  "analyst_name": None}]

    monkeypatch.setattr(main.valuatum, "search_company", fake_search)
    c = TestClient(main.app)
    key = store.create_access_key("E", generations_limit=1)["key"]
    exp = {"Authorization": f"Bearer {key}"}

    r = c.get("/api/company-search", params={"q": "1612398-8"}, headers=exp)
    assert r.status_code == 200
    assert r.json()[0]["fid"] == 184362


def test_email_delivery_sends_pdf_attachment_with_resend(monkeypatch, tmp_path):
    import asyncio
    from app import email_delivery

    pdf = tmp_path / "report.pdf"
    pdf.write_bytes(b"%PDF-1.4 test")
    calls = {}

    monkeypatch.setenv("RESEND_API_KEY", "re_test")
    monkeypatch.setenv("REPORT_EMAIL_FROM", "Valuatum <reports@example.com>")
    monkeypatch.setattr(email_delivery.store, "get_run", lambda rid: {
        "id": rid,
        "params": {"delivery_email": "owner@testi.fi"},
        "parent_run_id": None,
    })
    monkeypatch.setattr(email_delivery.store, "final_report_json", lambda rid: {
        "meta": {"company_name": "Valuatum Oy"},
    })
    monkeypatch.setattr(email_delivery.report, "generate_pdf", lambda rid, report_json: str(pdf))

    class FakeResponse:
        status_code = 200
        text = '{"id":"email_123"}'

        def json(self):
            return {"id": "email_123"}

    class FakeClient:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def post(self, url, headers=None, json=None):
            calls["url"] = url
            calls["headers"] = headers
            calls["json"] = json
            return FakeResponse()

    monkeypatch.setattr(email_delivery.httpx, "AsyncClient", FakeClient)
    out = asyncio.run(email_delivery.send_report_ready("run123"))

    assert out == {"sent": True, "provider": "resend", "id": "email_123"}
    assert calls["url"] == email_delivery.RESEND_URL
    assert calls["headers"]["Authorization"] == "Bearer re_test"
    assert calls["json"]["to"] == ["owner@testi.fi"]
    assert calls["json"]["attachments"][0]["filename"].endswith(".pdf")
    assert calls["json"]["attachments"][0]["content"]


@pytest.mark.skipif(not render.pdf_available(), reason="no local Chromium")
def test_golden_pdf_has_no_blank_pages(tmp_path):
    rep = _golden()
    out = str(tmp_path / "g.pdf")
    render.render_pdf(rep, out)
    n_sections = len(render._ensure_disclaimer(render._ordered_sections(rep)))
    # cover + TOC + appendix divider (section 16's disclaimer is always
    # ensured, so the divider always fires) + one page per section — and
    # crucially NO trailing blank pages.
    assert _pdf_page_count(out) == n_sections + 3


def test_blank_prompt_stage_is_skipped(monkeypatch):
    """An accidentally added admin-UI stage ('Stage 3 – new', empty prompt) must
    be skipped, not executed or cap-failed (2026-07-08 Turun tislaamo run)."""
    import asyncio
    from app import runner, seed, store

    seed.ensure_seeded()
    sw = next(p for p in store.list_pipelines()
              if p["name"] == seed.SINGLE_WRITER_PIPELINE_NAME)
    store.add_stage(sw["id"], {"order": 3, "name": "Stage 3 – new",
                               "model": "google/gemini-2.5-flash",
                               "prompt_template": ""})
    rid = store.create_run(sw["id"], {"meta": {"company_name": "X"}}, False)
    store.add_run_cost(rid, 10.0)  # over the $4 cap — skip must beat the cap check

    async def boom(*a, **k):  # a blank stage must never reach the model
        raise AssertionError("blank stage was executed")

    monkeypatch.setattr(runner, "_execute_stage", boom)

    async def drive():
        async for _ in runner.run_stages(store.get_run(rid),
                                         store.get_pipeline(sw["id"])["stages"],
                                         only=3):
            pass

    asyncio.run(drive())
    s3 = [r for r in store.get_run(rid)["results"] if r["order"] == 3][0]
    assert s3["status"] == "skipped"


def test_truncation_retry_respects_spend_cap(monkeypatch):
    """finish_reason='length' triggers a full-price re-run of the whole stage.
    Once the per-run cap is hit, that retry must NOT fire (the $6.96 writer
    call, 2026-07-08)."""
    import asyncio
    from app import openrouter, runner, seed, store

    seed.ensure_seeded()
    sw = next(p for p in store.list_pipelines()
              if p["name"] == seed.SINGLE_WRITER_PIPELINE_NAME)
    rid = store.create_run(sw["id"], {"meta": {"company_name": "X"}}, False)
    store.add_run_cost(rid, 10.0)  # already over the cap
    stage2 = next(s for s in store.get_pipeline(sw["id"])["stages"]
                  if s["order"] == 2)
    calls = {"n": 0}

    async def fake_chat(**kw):
        calls["n"] += 1
        return {"text": "not json {", "finish_reason": "length",
                "tokens_prompt": 1, "tokens_completion": 1,
                "request_payload": {}}

    monkeypatch.setattr(openrouter, "chat", fake_chat)
    res = asyncio.run(runner._execute_stage(
        stage2, {"input_data": "{}", "enrichment": "{}",
                 "previous_report": "", "user_input": "",
                 "old_numbers_directive": ""},
        None, None, None, rid=rid))
    assert calls["n"] == 1  # no second full-price attempt past the cap
    assert res["status"] == "error"

    # Without a cap breach the retry still fires (self-heal behaviour kept).
    rid2 = store.create_run(sw["id"], {"meta": {"company_name": "X"}}, False)
    calls["n"] = 0
    asyncio.run(runner._execute_stage(
        stage2, {"input_data": "{}", "enrichment": "{}",
                 "previous_report": "", "user_input": "",
                 "old_numbers_directive": ""},
        None, None, None, rid=rid2))
    assert calls["n"] == 2


def test_failed_run_refunds_generation_credit(monkeypatch):
    """A run that fails (e.g. spend-cap trip) must give the generation credit
    back — a failure otherwise permanently eats the customer's one credit
    (Turun tislaamo 0/1, 2026-07-09). Refund is once-only and skips round-2."""
    import asyncio
    from app import runner, seed, store

    seed.ensure_seeded()
    sw = next(p for p in store.list_pipelines()
              if p["name"] == seed.SINGLE_WRITER_PIPELINE_NAME)
    stages = store.get_pipeline(sw["id"])["stages"]

    def drive(rid):
        async def _d():
            async for _ in runner.run_stages(store.get_run(rid), stages, from_order=1):
                pass
        asyncio.run(_d())

    # Root run tied to a consumed 1-credit key, already over the spend cap so the
    # first paid stage fails immediately.
    key = store.create_access_key("Tilaus: X", generations_limit=1)["key"]
    assert store.consume_generation(key) is True
    rid = store.create_run(sw["id"], {"meta": {"company_name": "X"}}, False, access_key=key)
    store.add_run_cost(rid, 10.0)
    drive(rid)
    assert store.get_run(rid)["status"] == "error"
    assert store.get_access_key(key)["generations_used"] == 0  # refunded
    drive(rid)  # restart trap: re-running the same failed run must not double-refund
    assert store.get_access_key(key)["generations_used"] == 0

    # A round-2 clone (parent_run_id set) is credit-free — a failed one must NOT refund.
    key2 = store.create_access_key("Tilaus: Y", generations_limit=1)["key"]
    store.consume_generation(key2)
    r2 = store.create_run(sw["id"], {"meta": {"company_name": "Y"}}, False,
                          access_key=key2, parent_run_id="parent-xyz")
    store.add_run_cost(r2, 10.0)
    drive(r2)
    assert store.get_run(r2)["status"] == "error"
    assert store.get_access_key(key2)["generations_used"] == 1  # unchanged


def test_reseed_refreshes_all_single_writer_pipelines(monkeypatch):
    """Reseed must refresh EVERY single-writer pipeline, not just the canonical
    one — a stale duplicate ("…vanha ajohistoria") served an old prompt + old
    64k max_tokens to a paid run on 2026-07-09. Any name with the base prefix
    is kept current so it can't happen again."""
    from app import seed, store

    seed.ensure_seeded()
    dup = store.create_pipeline("Yhden kirjoittajan raportti (oletus, vanha ajohistoria)")
    store.add_stage(dup["id"], {
        "order": 2, "name": "Vaihe 2", "model": "anthropic/claude-fable-5",
        "prompt_template": "STALE OLD PROMPT", "max_tokens": 64000,
    })
    seed._ensure_single_writer_pipeline(force=True)

    s2 = next(s for s in store.get_pipeline(dup["id"])["stages"] if s["order"] == 2)
    assert "STALE OLD PROMPT" not in (s2["prompt_template"] or "")  # refreshed
    assert s2["max_tokens"] == 96000  # canonical writer limit, not the stale 64k
    assert len(s2["prompt_template"] or "") > 10000  # real vendored prompt loaded


def test_table_prose_columns_align_left_numeric_right():
    """Esa/CEO 2026-07-08: prose columns (Lähde, ...) were right-aligned and
    ugly; numeric year columns must stay right-aligned."""
    h = render._render_table(
        ["Oletus", "Arvo", "Lähde"],
        [["Markkinaosuus pk-SAM:sta", "15 % → n. 3 000 tEUR/v",
          "asiakkaan vahvistama tavoite kaksinumeroisesta markkinaosuudesta"],
         ["Rakenteellinen käännekohta", "Markkinaa laajentava käänne",
          "asiakkaan vahvistama; valuatum.com"]])
    # Lähde column is prose → every cell in it left-aligned
    assert h.count('style="text-align:left"') >= 6  # 3 cols x (th + 2 rows) all prose-ish here
    h2 = render._render_table(
        ["Erä", "2024", "2025"],
        [["Liikevaihto", "1 274", "980"], ["EBITDA", "-312", "-100"]])
    # numeric year columns keep the default right alignment (no inline style)
    assert h2.count("<th>2024</th>") == 1 and h2.count("<th>2025</th>") == 1


def test_cover_range_track_from_machine_readable_scenarios():
    """Single-writer reports have no _scenarios sidecar — the cover's scenario
    bar chart must build from machine_readable.scenarios (Athlos 2026-07-08)."""
    rep = _golden()
    rep.pop("_scenarios", None)
    rep["machine_readable"] = {"scenarios": [
        {"name": "Pessimistinen", "owner_value_teur": 0, "probability_pct": 35},
        {"name": "Konservatiivinen", "owner_value_teur": 669, "probability_pct": 40},
        {"name": "Optimistinen", "owner_value_teur": 10850, "probability_pct": 25}]}
    rep["expected_value"] = {"value": 2980, "unit": "tEUR"}
    rep["cover"] = {"headline_label": "Skenaarioiden odotusarvo",
                    "headline_value": "2 980 tEUR", "base_case_value": "669 tEUR"}
    h = render.render_html(rep)
    text = render._norm_ws(render._strip_tags(h))
    assert 'class="chart"' in h                  # scenario bar chart rendered
    assert "Skenaarioiden odotusarvo" in text    # expected-value marker (now the hero)
    assert "Optimistinen skenaario" in text


def test_cover_chart_survives_scenario_key_name_drift():
    """2026-07-10 SaaShop incident: Sol emitted `owner_value` instead of
    `value_teur`/`owner_value_teur` (the prompt never pinned the schema) and
    the whole cover chart + explanation grid silently vanished on 4 delivered
    reports. The renderer must accept every observed key spelling."""
    rep = _golden()
    rep.pop("_scenarios", None)
    rep["machine_readable"] = {"scenarios": [
        {"name": "Pessimistinen", "owner_value": 0, "probability_pct": 35},
        {"name": "Konservatiivinen", "owner_value": 0, "probability_pct": 40},
        {"name": "Optimistinen", "owner_value": 1718, "probability_pct": 25}]}
    rep["expected_value"] = {"value": 430, "unit": "tEUR"}
    rep["cover"] = {"headline_label": "Skenaarioiden odotusarvo",
                    "headline_value": "430 tEUR", "base_case_value": "0 tEUR"}
    h = render.render_html(rep)
    assert 'class="chart"' in h
    assert "Optimistinen" in render._strip_tags(h)
    # the deterministic comparison table must survive the same drift
    table = scenario_compare.build_scenario_comparison_block(rep)[0]
    value_row = next(r for r in table["rows"] if r[0] == "Arvo (tEUR)")
    assert value_row == ["Arvo (tEUR)", "0", "0", "1 718"]


def test_dcf_detail_waterfall_steps_match_bridge_ground_truth():
    blocks = dcf_detail.build_dcf_detail_blocks(_engine_input_data())
    wf = next(b for b in blocks if b.get("chart_id") == "deterministic_ev_equity_waterfall")
    assert wf["chart_type"] == "waterfall"
    steps = {s["label"]: s for s in wf["steps"]}
    assert steps["Yritysarvo (EV)"] == {"label": "Yritysarvo (EV)", "value": 400.0, "kind": "start"}
    assert steps["Korolliset velat"]["value"] == -10.0
    assert steps["Kassa"]["value"] == 60.0
    assert steps["Oman pääoman arvo"]["value"] == 450.0
    # EV + debt(signed) + cash must equal the equity total, or the chart lies.
    total = sum(s["value"] for s in wf["steps"] if s["kind"] != "total")
    assert abs(total - 450.0) < 1e-6


def test_svg_waterfall_renders_a_bar_per_step():
    svg = render._svg_waterfall([
        {"label": "EV", "value": 400.0, "kind": "start"},
        {"label": "Velat", "value": -10.0, "kind": "delta"},
        {"label": "Kassa", "value": 60.0, "kind": "delta"},
        {"label": "Oma pääoma", "value": 450.0, "kind": "total"},
    ])
    assert svg.count("<rect") == 4
    assert "EV" in svg and "Oma pääoma" in svg


def test_svg_waterfall_empty_without_numeric_steps():
    assert render._svg_waterfall([]) == ""
    assert render._svg_waterfall([{"label": "x", "value": None, "kind": "start"}]) == ""


def _scenarios_machine_readable():
    return {"scenarios": [
        {"name": "Pessimistinen", "value_teur": 0, "probability_pct": 35},
        {"name": "Konservatiivinen (perusskenaario)", "value_teur": 256, "probability_pct": 40},
        {"name": "Optimistinen", "value_teur": 1104, "probability_pct": 25},
    ]}


def test_scenario_comparison_columns_ordered_pessimistic_to_optimistic():
    report = {"machine_readable": _scenarios_machine_readable()}
    blocks = scenario_compare.build_scenario_comparison_block(report)
    assert len(blocks) == 1
    table = blocks[0]
    assert table["columns"] == ["Tunnusluku", "Pessimistinen", "Konservatiivinen", "Optimistinen"]
    value_row = next(r for r in table["rows"] if r[0] == "Arvo (tEUR)")
    assert value_row == ["Arvo (tEUR)", "0", "256", "1 104"]
    prob_row = next(r for r in table["rows"] if r[0] == "Todennäköisyys (%)")
    assert prob_row == ["Todennäköisyys (%)", "35", "40", "25"]


def test_scenario_comparison_prefers_scenarios_sidecar_over_machine_readable():
    report = {
        "_scenarios": {"scenarios": [
            {"name": "Pessimistinen", "owner_value_teur": 10, "probability_pct": 30},
            {"name": "Optimistinen", "owner_value_teur": 500, "probability_pct": 30},
        ]},
        "machine_readable": _scenarios_machine_readable(),
    }
    table = scenario_compare.build_scenario_comparison_block(report)[0]
    value_row = next(r for r in table["rows"] if r[0] == "Arvo (tEUR)")
    assert value_row == ["Arvo (tEUR)", "10", "500"]  # from _scenarios, not machine_readable


def test_scenario_comparison_empty_with_fewer_than_two_scenarios():
    assert scenario_compare.build_scenario_comparison_block({}) == []
    one = {"machine_readable": {"scenarios": [{"name": "Konservatiivinen", "value_teur": 669}]}}
    assert scenario_compare.build_scenario_comparison_block(one) == []


def test_assemble_prepends_scenario_comparison_and_is_idempotent():
    # Single-writer shape: one stage-2 output carries cover, machine_readable,
    # AND sections together (see /tmp run inspection — no separate stage 6).
    run = {"results": [
        {"order": 0, "status": "ok", "parsed_json": _engine_input_data()},
        {"order": 2, "status": "ok", "parsed_json": {
            "report_type": "ai_valuation_report", "cover": {"headline_value": "1"},
            "sections": [{"id": "1"},
                         {"id": "11", "title": "SKENAARIOT", "blocks": [{"type": "heading", "text": "x"}]}],
            "machine_readable": _scenarios_machine_readable(),
        }},
    ]}
    rep = assemble.assemble(run)
    sec11 = next(s for s in rep["sections"] if s["id"] == "11")
    assert sec11["blocks"][0]["table_id"] == "deterministic_scenario_comparison"
    assert sum(1 for b in sec11["blocks"]
               if b.get("table_id") == "deterministic_scenario_comparison") == 1
    # re-running assemble on the already-assembled result must not duplicate it
    rep2 = assemble.assemble({"results": [
        {"order": 0, "status": "ok", "parsed_json": _engine_input_data()},
        {"order": 2, "status": "ok", "parsed_json": rep},
    ]})
    sec11b = next(s for s in rep2["sections"] if s["id"] == "11")
    assert sum(1 for b in sec11b["blocks"]
               if b.get("table_id") == "deterministic_scenario_comparison") == 1
