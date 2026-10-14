import copy
from concurrent.futures import ThreadPoolExecutor

import pytest

from app import assemble, dcf_detail, financials, headcount_efficiency, render
from app.report_i18n import current, report_context


def _input(currency="USD", country="US", language="en"):
    return {
        "meta": {"company_name": "Odotusarvo & Sons <Test>", "report_language": language,
                 "country_code": country, "currency": currency},
        "unit": "t" + currency,
        "actuals": {"years": [2024, 2025],
                    "income_statement": {"net_sales": [1800, 2000], "ebit": [180, 200],
                                         "net_earnings": [100, 120], "personnel_costs": [-200, -200]},
                    "balance_sheet": {"total_assets": [1000, 1100], "equity_excl_capital_loans": [700, 800]},
                    "per_employee": {"net_sales": [18, 20], "net_earnings": [1, 1.2]}},
        "headcount": {"years": [2024, 2025], "values": [100, 100]},
        "forecast": {"years": [2026, 2027], "net_sales": [2200, 2400],
                     "ebit": [330, 480], "ebit_pct": [15, 20]},
        "valuation_engine": {
            "wacc_parameters": {"wacc_pct": 10, "cost_of_debt_pct": 4,
                                "liquidity_premium_pct": 2, "target_d_to_de_pct": 20},
            "dcf": {"years": [2026, 2027], "fcff": [200, 300], "discounted_fcff": [180, 250],
                    "cumulative_discounted_fcff": [5000, 4820], "ebit": [330, 480],
                    "taxes_paid": [-66, -96], "depreciation_total": [20, 20],
                    "gross_capex": [-30, -30], "equity_value_before_floor": 4800,
                    "bridge": {"interest_bearing_debt": -300, "cash": 100}},
            "eva": {"years": [2026, 2027], "noplat": [264, 384], "cost_of_capital": [-100, -100],
                    "eva": [164, 284], "discounted_eva": [150, 240],
                    "invested_capital": 1000, "cumulative_discounted_eva": [4000, 3850],
                    "equity_value_before_floor_raw": 4800,
                    "bridge": {"interest_bearing_debt": -300, "cash": 100}}},
        "credit_risk": {"rating": ["B"], "company_bankruptcy_risk_pct": [3]},
    }


def _run(currency="USD", country="US", language="en"):
    data = _input(currency, country, language)
    unit = data["unit"]
    wrapper = {
        "meta": {"company_name": data["meta"]["company_name"], "unit": unit,
                 "industry": "Retail", "report_date": "2026-10-14"},
        "cover": {"headline_value": "5 000 " + unit, "base_case_value": "4 800 " + unit},
        "machine_readable": {
            "expected_value": 5000,
            "scenarios": [{"name": "Pessimistic", "value_teur": 2000, "probability_pct": 25},
                          {"name": "Conservative base case", "value_teur": 4800, "probability_pct": 50},
                          {"name": "Optimistic", "value_teur": 8000, "probability_pct": 25}]},
        "sections": [{"id": sid, "title": title, "blocks": []} for sid, title in
                     [("1", "Summary"), ("5", "Historical results"), ("6", "Forecast"),
                      ("8", "Valuation methods"), ("9", "DCF"), ("10", "EVA"), ("11", "Scenarios")]],
    }
    return {"results": [{"order": 0, "status": "ok", "parsed_json": data},
                        {"order": 6, "status": "ok", "parsed_json": wrapper}]}


@pytest.mark.parametrize("currency,country", [("USD", "US"), ("DKK", "DK"), ("EUR", "FI")])
def test_english_assembly_keeps_numbers_and_localizes_all_injected_blocks(currency, country):
    report = assemble.assemble(_run(currency, country))
    assert report["meta"]["currency"] == currency
    assert report["meta"]["country_code"] == country
    assert report["meta"]["report_language"] == "en"
    statements = next(b for s in report["sections"] for b in s["blocks"]
                      if b.get("table_id") == "deterministic_income_statement")
    assert statements["rows"][0] == ["Revenue", "1,800", "2,000"]
    assert statements["unit"] == "t" + currency
    text = render.render_html(report)
    assert '<html lang="en">' in text
    assert "Odotusarvo &amp; Sons &lt;Test&gt;" in text
    assert "Forecast origin" in text
    assert "Primary valuation method and reconciliation" in text
    assert "Where discounted FCFF comes from" in text
    assert "Discount rate and credit risk" in text
    assert "Annual EVA calculation" in text
    assert "Scenario comparison" in text
    assert "Employee efficiency" in text
    assert currency + "/employee" in text
    assert "GLOSSARY" in text
    for finnish in ("Skenaarioiden", "Henkilöstö", "Arvonmääritys", "Tuloslaskelma",
                    "Diskontattu", "Liitteet", "Sanasto", "Mistä ennuste", "paras toteutunut"):
        assert finnish not in text
    if country != "FI":
        assert "Finnish tax" not in text
        assert "verottaja_value_teur" not in report["_basics"]
        assert "tEUR" not in text and "M€" not in text and "mrd. €" not in text
    else:
        assert "Finnish tax authority reference" in text


@pytest.mark.parametrize("currency,country", [("USD", "US"), ("DKK", "DK"), ("EUR", "FI")])
def test_currency_scaling_and_english_grouped_cover_numbers(currency, country):
    report = assemble.assemble(_run(currency, country))
    report["cover"]["headline_value"] = "1,500,000 t" + currency
    text = render.render_html(report)
    assert "1.50 bn " + currency in text
    report["cover"]["headline_value"] = "15,000 t" + currency
    assert "15.0 M " + currency in render.render_html(report)


def test_headcount_eur_heuristic_is_not_applied_to_foreign_currency():
    foreign = headcount_efficiency.build_headcount_efficiency_blocks(_input())
    assert foreign[0]["rows"][0] == ["Headcount", "100", "100"]
    assert ["Revenue / employee", "18,000", "20,000"] in foreign[0]["rows"]
    assert len(foreign) == 1
    finnish = headcount_efficiency.build_headcount_efficiency_blocks(_input("EUR", "FI", "fi"))
    assert any(b.get("title") == "Henkilöstömäärä korjattu" for b in finnish)


@pytest.mark.parametrize("conflict", ["meta", "unit", "cover", "table"])
def test_report_currency_conflicts_fail_before_export(conflict):
    report = assemble.assemble(_run())
    if conflict == "meta":
        report["meta"]["currency"] = "EUR"
    elif conflict == "unit":
        report["meta"]["unit"] = "tDKK"
    elif conflict == "cover":
        report["cover"]["headline_value"] = "5 000 tEUR"
    else:
        report["sections"][0]["blocks"].append({"type": "table", "unit": "M€", "columns": [], "rows": []})
    with pytest.raises(ValueError, match="conflicts"):
        render.render_html(report)


def test_assembly_rejects_writer_currency_contradiction():
    run = _run()
    run["results"][-1]["parsed_json"]["meta"]["currency"] = "EUR"
    with pytest.raises(ValueError, match="conflicts"):
        assemble.assemble(run)


def test_nested_source_unit_contradiction_fails_before_relabeling():
    data = _input()
    data["valuation_engine"]["dcf"]["unit"] = "tEUR"
    with pytest.raises(ValueError, match="conflicts"):
        dcf_detail.build_dcf_detail_blocks(data)


def test_english_partial_dcf_notice_and_reporting_level_are_localized():
    data = _input()
    data["valuation_engine"]["dcf"].pop("fcff")
    assert "missing the year, FCFF" in dcf_detail.build_dcf_detail_blocks(data)[0]["text"]
    report = assemble.assemble(_run())
    report["meta"]["level"] = "consolidated"
    report["meta"]["industry"] = "Ohjelmistokehitys"
    text = render.render_html(report)
    assert "Consolidated level (official financial statements)" in text
    assert "Source classification: Ohjelmistokehitys" in text


def test_english_old_weight_and_dcf_tables_are_replaced():
    run = _run()
    sections = run["results"][-1]["parsed_json"]["sections"]
    next(s for s in sections if s["id"] == "8")["blocks"] = [
        {"type": "table", "title": "Old weights", "columns": ["Method", "Weight"], "rows": []}]
    next(s for s in sections if s["id"] == "9")["blocks"] = [
        {"type": "table", "title": "Enterprise value to equity value", "columns": ["Item", "Value"],
         "rows": [["Enterprise value", "1"], ["Equity value", "1"]]}]
    report = assemble.assemble(run)
    assert not any(b.get("title") == "Old weights" for s in report["sections"] for b in s["blocks"])
    bridges = [b for s in report["sections"] for b in s["blocks"]
               if b.get("title") == "Enterprise value to equity value"]
    assert len(bridges) == 1
    assert bridges[0]["table_id"] == "deterministic_dcf_equity_bridge"


def test_foreign_report_removes_stored_finnish_tax_comparator_and_method_summary():
    report = assemble.assemble(_run())
    report["_basics"]["verottaja_value_teur"] = 700
    report["sections"][0]["blocks"].append({"type": "paragraph", "text": "The Finnish tax authority model is used."})
    report["sections"][0]["blocks"].append({"type": "table", "table_id": "deterministic_valuation_crosscheck",
                                              "columns": [], "rows": []})
    text = render.render_html(report)
    assert "Finnish tax" not in text
    assert "verottaja_value_teur" not in report["_basics"]


def test_locale_is_reset_after_errors_and_isolated_between_threads():
    assert current().language == "fi"
    assert render._report_scale_ctx.get() == (1.0, "tEUR", 0)
    with pytest.raises(ValueError):
        with report_context(_input()):
            raise ValueError("test")
    assert current().currency == "EUR"

    def build(pair):
        currency, country, language = pair
        report = assemble.assemble(_run(*pair))
        return render.render_html(report)

    pairs = [("USD", "US", "en"), ("DKK", "DK", "en"), ("EUR", "FI", "fi")] * 4
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(build, pairs))
    for (currency, country, language), text in zip(pairs, results):
        assert f'<html lang="{language}">' in text
        assert "t" + currency in text
        if currency != "EUR":
            assert "tEUR" not in text
    assert current().language == "fi"


def test_optimistic_waterfall_uses_source_currency_without_changing_math():
    run = _run("DKK", "DK")
    wrapper = run["results"][-1]["parsed_json"]
    wrapper["machine_readable"]["optimistic_assumptions"] = {
        "continuing_value_teur": 10148, "net_debt_realization_year_teur": 3023,
        "realization_year": 2030, "wacc_pct": 9.47,
    }
    expected = (10148 - 3023) / 1.0947 ** 5
    report = assemble.assemble(run)
    scenarios = report["machine_readable"]["scenarios"]
    assert scenarios[-1]["value_teur"] == round(expected, 1)
    text = render.render_html(report)
    assert "Optimistic scenario value derivation" in text
    assert "tDKK" in text
    assert "tEUR" not in text


def test_english_source_citations_and_section_references_keep_identifiers():
    report = assemble.assemble(_run())
    report["sections"][0]["blocks"] = [
        {"type": "paragraph", "text": "See section 9 (source: example.com, 2026-10-14)."},
        {"type": "table", "columns": ["Source"], "rows": [["https://example.com/report"]]},
    ]
    text = render.render_html(report)
    assert 'href="https://example.com/report">example.com</a>' in text
    assert 'class="secref">section 5</a>' in text


@pytest.mark.parametrize("currency,country", [("USD", "US"), ("DKK", "DK"), ("EUR", "FI")])
@pytest.mark.parametrize("scale", ["t", "M", "bn", "Mcompact", "bncompact"])
def test_english_restated_value_range_formula_and_scenario_rows_use_canonical_figures(currency, country, scale):
    unit = "t" + currency if scale == "t" else scale.replace("compact", "") + ("" if "compact" in scale else " ") + currency
    divisor = {"t": 1, "M": 1000, "bn": 1_000_000}[scale.replace("compact", "")]
    values = [2000, 5000, 8000] if scale == "t" else [2, 5, 8]
    stale = [5200, 8100] if scale == "t" else [5.2, 8.1]
    report = assemble.assemble(_run(currency, country))
    for scenario, value in zip(report["machine_readable"]["scenarios"], values):
        scenario["value_teur"] = value * divisor
    canonical = {"expected_value": values[1] * divisor,
                 "range_low": values[0] * divisor, "range_high": values[2] * divisor}
    paragraph = {
        "type": "paragraph",
        "text": (f"The expected value is {stale[0]:,} {unit}. "
                 f"The valuation range is {values[0]:,}–{stale[1]:,} {unit}. "
                 f"Revenue was {stale[0]:,} {unit}. "
                 f"Expected value: 25% × {values[0]:,} {unit} + 50% × {values[1]:,} {unit} "
                 f"+ 25% × {stale[1]:,} {unit} = {stale[0]:,} {unit}."),
    }
    explicit = {"type": "table", "columns": ["Scenario", "Equity value"],
                "rows": [["Optimistic equity value", f"{stale[1]:,} {unit}"],
                         ["Optimistic revenue", f"{80 * values[2]:,} {unit}"]]}
    declared = {"type": "table", "columns": ["Scenario", f"Equity value ({unit})"],
                "rows": [["Optimistic", f"{stale[1]:,}"]]}
    report["sections"] = [{"id": "1", "title": "Summary", "blocks": [paragraph, explicit, declared]}]
    with report_context(report):
        output_unit = render._unit_scale(unit)[1]
        expected = render._unit_fmt(canonical["expected_value"], unit)
        high = render._unit_fmt(canonical["range_high"], unit)
        low = render._unit_fmt(canonical["range_low"], unit).removesuffix(" " + output_unit)
        notes = render._fix_restated_figures(report, canonical)
        assert f"The expected value is {expected}." in paragraph["text"]
        assert f"The valuation range is {low}–{high}." in paragraph["text"]
        assert f"Revenue was {stale[0]:,} {unit}." in paragraph["text"]
        assert f"25 % × {render._unit_fmt(values[0] * divisor, unit)}" in paragraph["text"]
        assert f"25 % × {high} = {expected}." in paragraph["text"]
        assert explicit["rows"][0][1] == high
        assert declared["rows"][0][1] == high.removesuffix(" " + output_unit)
        assert explicit["rows"][1][1] == f"{80 * values[2]:,} {unit}"
        assert notes


def test_english_restatement_supports_units_on_both_range_endpoints():
    report = assemble.assemble(_run())
    paragraph = {"type": "paragraph", "text": "The valuation range is 2.0 M USD–8.1 M USD."}
    report["sections"] = [{"id": "1", "blocks": [paragraph]}]
    with report_context(report):
        render._fix_restated_figures(report, {"range_low": 2000, "range_high": 8000})
    assert paragraph["text"] == "The valuation range is 2.0–8.0 M USD."


@pytest.mark.parametrize("has_disclaimer", [True, False])
def test_english_disclaimer_preserves_existing_methodology_and_limitations(has_disclaimer):
    report = assemble.assemble(_run())
    caveat = "Stored forecasts have not been independently verified."
    blocks = [{"type": "paragraph", "text": caveat}]
    if has_disclaimer:
        blocks.insert(0, {"type": "paragraph", "text": "This report is not investment advice."})
    report["sections"].append({"id": "16", "title": "Methodology and limitations", "blocks": blocks})
    text = render.render_html(report)
    assert caveat in text
    assert "METHODOLOGY AND LIMITATIONS" in text
    assert "investment advice" in text
    assert "fairness opinion" not in text if has_disclaimer else "fairness opinion" in text


def test_foreign_stored_forecast_callout_makes_no_unverified_algorithm_claim():
    data = _input("DKK", "DK")
    data["forecast"]["is_system_deterministic"] = False
    callout = financials.build_forecast_origin_block(data)
    assert "stored source forecast" in callout["text"]
    assert "2026–2027" in callout["text"]
    assert "15.0 %" in callout["text"] and "20.0 %" in callout["text"]
    for phrase in ("deterministic base", "approaches", "competition", "terminal", "WACC"):
        assert phrase not in callout["text"]


@pytest.mark.parametrize("unit", ["MUSD", "bnUSD", "MEUR", "bnEUR"])
def test_compact_foreign_currency_labels_fail_strict_validation(unit):
    report = assemble.assemble(_run("DKK", "DK"))
    report["sections"][0]["blocks"].append({"type": "paragraph", "text": "Expected value is 5.2 " + unit})
    with pytest.raises(ValueError, match="conflicts"):
        render.render_html(report)


def test_english_restater_does_not_match_company_name_substrings():
    report = assemble.assemble(_run())
    text = "Orange reported 2,000–8,100 tUSD. Its unexpected value was 5,200 tUSD."
    paragraph = {"type": "paragraph", "text": text}
    report["sections"] = [{"id": "1", "blocks": [paragraph]}]
    with report_context(report):
        assert render._fix_restated_figures(report, {"expected_value": 5000, "range_low": 2000, "range_high": 8000}) == []
    assert paragraph["text"] == text
