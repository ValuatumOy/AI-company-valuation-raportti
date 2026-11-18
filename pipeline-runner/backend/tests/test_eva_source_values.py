"""Source EVA survives normalization, including reports copied from old runs."""
import copy
import json

import pytest

from app import assemble, valuation_equivalence as veq


def _input(raw=5200.0):
    return {"valuation_engine": {
        "dcf": {"equity_value_before_floor": 6000.0},
        "eva": {"equity_value_before_floor_raw": raw,
                "equity_value_before_floor": 6000.0,
                "equivalence_note": "EVA is normalized to DCF equity value.",
                "invested_capital": 1000, "years": [2026, 2027], "discounted_eva": [100, 200],
                "pv_of_trm_eva": 3900,
                "bridge": {"interest_bearing_debt": 0, "cash": 0}}},
        "actuals": {"income_statement": {"net_earnings": [100, 200, 300]},
                    "balance_sheet": {"equity": [500]}}}


def _report():
    return {"_scoring": {"method_scoring": [
        {"method": "DCF", "status": "hyväksytty", "weight_pct": 40, "value_teur": 6000.0},
        {"method": "EVA", "status": "hyväksytty", "weight_pct": 60, "value_teur": 6000.0}]},
        "sections": [{"id": "8", "blocks": []}, {"id": "10", "blocks": []}]}


def _table(report, table_id):
    return next(b for s in report["sections"] for b in s["blocks"] if b.get("table_id") == table_id)


def test_distinct_source_eva_is_reference_while_dcf_remains_base_case():
    report, data = _report(), _input()
    original = copy.deepcopy(data)
    veq.normalize_report(report, data)
    dcf, eva = report["_scoring"]["method_scoring"]
    assert (dcf["value_teur"], dcf["weight_pct"]) == (6000.0, 100)
    assert (eva["value_teur"], eva["weight_pct"], eva["status"]) == (5200.0, 0, "viite")
    assert report["_scoring"]["weighted_base_case_teur"] == 6000.0
    assert _table(report, "deterministic_valuation_source_methods")["rows"] == [
        ["DCF", "6 000", "Päämenetelmä"],
        ["EVA", "5 200", "Erillinen lähdemallin vertailuarvo"]]
    assert any(b.get("variant") == "warning" for b in report["sections"][0]["blocks"])
    assert "ei ole vahvistettu" in report["sections"][0]["blocks"][0]["text"]
    assert data == original


@pytest.mark.parametrize("raw", [None, 0, 5999.8, 6000.0])
def test_missing_zero_and_close_eva_are_not_replaced_or_claimed_equivalent(raw):
    report, data = _report(), _input(raw)
    veq.normalize_report(report, data)
    assert report["_scoring"]["method_scoring"][1]["value_teur"] == raw
    expected = "ei saatavilla lähdedatassa" if raw is None else veq._fmt_num(raw)
    assert _table(report, "deterministic_valuation_source_methods")["rows"][1][1] == expected
    assert "DCF/EVA ekvivalenssi" not in json.dumps(report, ensure_ascii=False)
    assert "ei ole vahvistettu" in json.dumps(report, ensure_ascii=False)
    if raw is None:
        rows = _table(report, "deterministic_eva_reconciliation")["rows"]
        assert ["EVA-moottorin oma arvo ennen normalisointia", expected] in rows
        assert not any(r[0] == "Komponenttien summa" for r in rows)


@pytest.mark.parametrize("eva", [{}, None])
def test_absent_eva_clears_copied_values_and_waterfall(eva):
    report, data = _report(), _input()
    data["valuation_engine"]["eva"] = eva
    report["_eva_waterfall"] = {"equity": 6000.0}
    veq.normalize_report(report, data)
    assert "_eva_waterfall" not in report
    assert report["_scoring"]["method_scoring"][1]["value_teur"] is None
    assert _table(report, "deterministic_valuation_source_methods")["rows"][1][1] == "ei saatavilla lähdedatassa"


def test_reassembly_refreshes_old_equivalence_and_preserves_unrelated_prose():
    report, data = _report(), _input()
    report["sections"][0]["blocks"] = [
        {"type": "paragraph", "text": "DCF ja EVA ovat sama arvo kahdella eri tavalla laskettuna."},
        {"type": "table", "table_id": "deterministic_dcf_eva_equivalence", "rows": [["EVA", "6 000"]]},
        {"type": "callout", "title": "Menetelmien ekvivalenssi", "text": "Sama ennuste."},
        {"type": "heading", "text": "Arvostusväli"},
        {"type": "paragraph", "text": "DCF ja EVA antavat saman lopputuloksen. Rahoitustarve vaikuttaa skenaarioihin."}]
    veq.normalize_report(report, data)
    first = copy.deepcopy(report)
    veq.normalize_report(report, data)
    assert report == first
    assert "saman lopputuloksen" not in json.dumps(report, ensure_ascii=False)
    assert "Rahoitustarve vaikuttaa skenaarioihin." in json.dumps(report, ensure_ascii=False)
    assert sum(b.get("table_id") == "deterministic_valuation_crosscheck"
               for b in report["sections"][0]["blocks"]) == 1
    data["valuation_engine"]["eva"]["equity_value_before_floor_raw"] = 5000
    veq.normalize_report(report, data)
    assert _table(report, "deterministic_valuation_source_methods")["rows"][1][1] == "5 000"


def test_missing_explicit_eva_is_not_zero_or_a_reconciliation():
    report, data = _report(), _input(1000)
    data["valuation_engine"]["eva"]["discounted_eva"] = [None]
    data["valuation_engine"]["eva"]["pv_of_trm_eva"] = 0
    veq.normalize_report(report, data)
    rows = _table(report, "deterministic_eva_reconciliation")["rows"]
    assert ["PV, diskontatut EVA:t (ennustejakso)", "ei saatavilla lähdedatassa"] in rows
    assert not any(r[0] == "Komponenttien summa" for r in rows)


def test_assemble_sanitizes_copied_section7_before_surfacing():
    writer = _report()
    writer["report_type"] = "ai_valuation_report"
    writer["sections"] += [{"id": "7", "blocks": [
        {"type": "paragraph", "text": "Ekvivalenssisäännön vuoksi DCF:n paino on 100 % ja EVA:n paino 0 %. Molempien raportoitava oman pääoman arvo on 6 000 tEUR. Rahoitustarve on huomattava."},
        {"type": "table", "table_id": "method_selection", "columns": ["Menetelmä", "Pisteet", "Status", "Paino", "Arvo (tEUR)", "Perustelu"],
         "rows": [["DCF", 65, "Hyväksytty", "100 %", 6000, "Kassavirtamalli"],
                  ["EVA", 50, "Täsmäytys", "0 %", 6000, "Moottori normalisoi arvon DCF:ään"],
                  ["P/E", "–", "Hylätty", "–", "–", "Ei verrokkikerrointa"],
                  ["EV/liikevaihto ja EBIT-% suhteessa P/Sales", "–", "Hylätty", "–", "–", "Ei verrokkikerrointa"]]}]}]
    run = {"results": [{"order": 0, "status": "ok", "parsed_json": _input()},
                       {"order": 2, "status": "ok", "parsed_json": writer}]}
    report = assemble.assemble(copy.deepcopy(run))
    rows = _table(report, "method_selection")["rows"]
    assert rows[1] == ["EVA", 50, "viite", "0 %", "5 200", "Erillinen lähdemallin EVA-arvo; sitä ei painoteta DCF-perusarvoon."]
    assert rows[2] == ["P/E", "–", "Hylätty", "–", "–", "Ei verrokkikerrointa"]
    assert rows[3] == ["EV/liikevaihto ja EBIT-% suhteessa P/Sales", "–", "Hylätty", "–", "–", "Ei verrokkikerrointa"]
    text = json.dumps(report, ensure_ascii=False)
    assert "Molempien raportoitava" not in text
    assert "Ekvivalenssisäännön" not in text
    assert "Rahoitustarve on huomattava." in text
    copied = {"results": [{"order": 0, "status": "ok", "parsed_json": _input()},
                          {"order": 2, "status": "ok", "parsed_json": copy.deepcopy(report)}]}
    again = assemble.assemble(copied)
    assert _table(again, "method_selection")["rows"] == rows
    assert sum(b.get("table_id") == "method_selection" for s in again["sections"] for b in s["blocks"]) == 1


def _complete_eva_input():
    data = _input(1000)
    data["valuation_engine"]["eva"] = {
        "years": [2026, 2027], "discounted_eva": [40, 60],
        "invested_capital": 600, "pv_of_trm_eva": 300,
        "cumulative_discounted_eva": [400, 360],
        "equity_value_before_floor_raw": 1000,
        "bridge": {"interest_bearing_debt": 0, "cash": 0},
    }
    return data


@pytest.mark.parametrize("years,discounted", [
    ([2026, 2027], [100]), (None, [40, 60]), ([], [40, 60]),
    ([2026, None], [40, 60]), ([2026, 2026], [40, 60]),
    ([2026, 2027], [40, None]),
])
def test_partial_or_missing_eva_years_cannot_reconcile(years, discounted):
    data, report = _complete_eva_input(), _report()
    eva = data["valuation_engine"]["eva"]
    eva["years"], eva["discounted_eva"] = years, discounted
    veq.normalize_report(report, data)
    rows = _table(report, "deterministic_eva_reconciliation")["rows"]
    assert not any(row[0] == "Komponenttien summa" for row in rows)
    assert ["PV, diskontatut EVA:t (ennustejakso)", "ei saatavilla lähdedatassa"] in rows
    assert veq.eva_waterfall_figures(data) is None


@pytest.mark.parametrize("bridge", [
    {}, {"interest_bearing_debt": None, "cash": None},
    {"interest_bearing_debt": 0}, {"cash": 0},
    {"interest_bearing_debt": 0, "cash": None},
    {"interest_bearing_debt": 0, "cash": 0, "prev_year_dividends": None},
])
def test_missing_bridge_components_without_source_aggregate_cannot_reconcile(bridge):
    data, report = _complete_eva_input(), _report()
    data["valuation_engine"]["eva"]["bridge"] = bridge
    report["_eva_waterfall"] = {"equity": 1000}
    veq.normalize_report(report, data)
    rows = _table(report, "deterministic_eva_reconciliation")["rows"]
    assert not any(row[0] == "Komponenttien summa" for row in rows)
    assert "_eva_waterfall" not in report
    assert veq.eva_waterfall_figures(data) is None


@pytest.mark.parametrize("aggregate", [0, -50, 50])
def test_witnessed_source_aggregate_reconciles_without_inventing_bridge_components(aggregate):
    data, report = _complete_eva_input(), _report()
    eva = data["valuation_engine"]["eva"]
    eva["additional"] = aggregate
    eva["equity_value_before_floor_raw"] = 1000 + aggregate
    eva["bridge"] = {"interest_bearing_debt": None, "cash": None}
    veq.normalize_report(report, data)
    rows = _table(report, "deterministic_eva_reconciliation")["rows"]
    assert ["Lähteen rahoitus- ja muut oikaisut", veq._fmt_num(aggregate)] in rows
    assert ["Komponenttien summa", veq._fmt_num(1000 + aggregate)] in rows
    assert not any(row[0] in ("− Korolliset velat", "+ Kassa") for row in rows)
    waterfall = veq.eva_waterfall_figures(data)
    assert waterfall["debt"] is None and waterfall["cash"] is None
    assert waterfall["other"] == aggregate


def test_cumulative_eva_uses_the_first_year_even_when_it_is_missing():
    data, report = _complete_eva_input(), _report()
    eva = data["valuation_engine"]["eva"]
    eva["pv_of_trm_eva"] = None
    eva["cumulative_discounted_eva"] = [None, 400]
    eva.update(noplat=[0, 0], cost_of_capital=[0, 0], eva=[0, 0])
    veq.normalize_report(report, data)
    rows = _table(report, "deterministic_eva_reconciliation")["rows"]
    assert ["Jatkuvan arvon (terminaali-EVA) nykyarvo", "ei saatavilla lähdedatassa"] in rows
    assert not any(row[0] == "Komponenttien summa" for row in rows)
    assert "TRM" not in _table(report, "deterministic_eva_years")["columns"]
    assert veq.eva_waterfall_figures(data) is None


def test_true_zero_eva_components_and_first_cumulative_value_remain_valid():
    data, report = _complete_eva_input(), _report()
    eva = data["valuation_engine"]["eva"]
    eva.update(discounted_eva=[0, 0], invested_capital=0, pv_of_trm_eva=None,
               cumulative_discounted_eva=[0, None], equity_value_before_floor_raw=0)
    veq.normalize_report(report, data)
    rows = _table(report, "deterministic_eva_reconciliation")["rows"]
    assert ["PV, diskontatut EVA:t (ennustejakso)", "0"] in rows
    assert ["Jatkuvan arvon (terminaali-EVA) nykyarvo", "0"] in rows
    assert ["− Korolliset velat", "0"] in rows and ["+ Kassa", "0"] in rows
    assert ["Komponenttien summa", "0"] in rows
    waterfall = veq.eva_waterfall_figures(data)
    assert (waterfall["equity"], waterfall["terminal"], waterfall["debt"], waterfall["cash"]) == (0, 0, 0, 0)


@pytest.mark.parametrize("text", [
    "DCF ja EVA poikkeavat toisistaan. Samat velat sisältyvät sekä ennusteen rahoitukseen että velkavähennykseen.",
    "DCF ja EVA poikkeavat toisistaan. Samat velat näkyvät DCF:n rahoituslaskelmassa.",
    "DCF ja EVA eivät ole sama arvo. Samat velat on huomioitu molemmissa.",
    "DCF and EVA are not equivalent. The same debt may appear in financing and debt deductions.",
])
def test_equality_cleanup_preserves_unrelated_risk_commentary_and_negative_assertions(text):
    block = {"type": "paragraph", "text": text}
    assert veq._strip_equivalence_claims(block) == block
    report = _report()
    report["sections"][0]["blocks"] = [block]
    veq.normalize_report(report, _input())
    assert block in report["sections"][0]["blocks"]
