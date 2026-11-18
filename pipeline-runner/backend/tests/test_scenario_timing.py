"""Scenario dates must follow evidenced engine discounting, not actual-year gaps."""
from copy import deepcopy

import pytest

from app import render, scenario_compare, scenario_waterfall as sw
from app.report_i18n import report_context


def _data():
    years = list(range(2026, 2036))
    cash = [-1000.0, -1500.0, -2000.0, -500.0, -250.0,
            100.0, 200.0, 500.0, 800.0, 1000.0]
    return {
        "meta": {"report_date": "2026-11-13", "scenario_timing_version": 2},
        "actuals": {"years": [2024, 2025], "fiscal_period_end": "2025-12-31"},
        "forecast": {"years": years[:]},
        "valuation_engine": {
            "wacc_parameters": {"wacc_pct": 9.46},
            "dcf": {"years": years, "fcff": cash,
                    "discounted_fcff": [c / 1.0946 ** (i + 49 / 365)
                                        for i, c in enumerate(cash)]},
        },
    }


def _assumptions(**changes):
    return {"realization_year": 2035, "continuing_value_teur": 100000,
            "net_debt_realization_year_teur": 12000, **changes}


def test_december_2025_actuals_do_not_force_ten_year_discounting_to_2035():
    result = sw.compute(_assumptions(), _data())
    n = 9 + 49 / 365
    assert result["n"] == pytest.approx(n)
    assert result["value_teur"] == pytest.approx(88000 / 1.0946 ** n)
    assert result["timing_verified"] is True
    assert result["timing_basis"] == "engine_fcff_ratio"
    assert result["timing_reason"] is None
    assert "9,134 v" in result["steps"][3][0]


def test_rounded_synthetic_cash_flows_share_the_same_engine_date():
    data = _data()
    data["valuation_engine"]["dcf"] = {
        "years": [2026, 2030, 2035],
        "fcff": [-1250.25, -750.125, 2250.5],
        "discounted_fcff": [-1235.17062, -516.22847, 985.62138],
    }
    timing = sw.discount_periods(data)
    assert timing["verified"] is True
    assert timing["periods"][2035] == pytest.approx(9 + 49 / 365)
    result = sw.compute(_assumptions(realization_year=2030), data)
    assert result["n"] == pytest.approx(4 + 49 / 365)
    assert "4,134 v" in result["steps"][3][0]


def test_scenario_wacc_changes_discount_rate_without_changing_engine_date():
    result = sw.compute(_assumptions(wacc_pct=15, dilution_pct=20), _data())
    n = 9 + 49 / 365
    assert result["n"] == pytest.approx(n)
    assert result["wacc_pct"] == 15
    assert result["value_teur"] == pytest.approx(88000 / 1.15 ** n * 0.8)


def test_negative_debt_is_cash_and_negative_equity_keeps_owner_floor():
    data = _data()
    cash = sw.compute(_assumptions(net_debt_realization_year_teur=-12000), data)
    assert cash["value_teur"] == pytest.approx(112000 / 1.0946 ** cash["n"])
    floor = sw.compute(_assumptions(net_debt_realization_year_teur=120000), data)
    assert floor["value_teur"] == 0
    assert floor["steps"][-2] == ["Omistaja-arvon lattia", "0"]


def test_alternating_missing_zero_and_negative_pairs_keep_year_alignment():
    data = _data()
    dcf = data["valuation_engine"]["dcf"]
    for i in [0, 2, 4, 6, 8]:
        dcf["fcff"][i] = dcf["discounted_fcff"][i] = None
    for i in [1, 3, 5, 7]:
        dcf["fcff"][i] = dcf["discounted_fcff"][i] = 0
    timing = sw.discount_periods(data)
    assert timing["verified"] is True
    assert set(timing["periods"]) == {2035}
    assert sw.compute(_assumptions(), data)["n"] == pytest.approx(9 + 49 / 365)
    assert sw.compute(_assumptions(realization_year=2030), data) is None


@pytest.mark.parametrize("cash,pv", [(None, None), (100, None), (None, 100), (0, 0)])
def test_target_year_without_usable_pair_is_not_extrapolated(cash, pv):
    data = _data()
    dcf = data["valuation_engine"]["dcf"]
    dcf["fcff"][-1], dcf["discounted_fcff"][-1] = cash, pv
    assert sw.discount_periods(data)["verified"] is True
    assert 2035 not in sw.discount_periods(data)["periods"]
    assert sw.compute(_assumptions(), data) is None
    assert sw.compute(_assumptions(realization_year=2036), data) is None


def test_inconsistent_other_year_rejects_an_otherwise_usable_target():
    data = _data()
    data["valuation_engine"]["dcf"]["discounted_fcff"][4] *= 1.0946
    timing = sw.discount_periods(data)
    assert timing["verified"] is False
    assert timing["periods"] == {}
    assert "inconsistent" in timing["reason"]
    assert sw.compute(_assumptions(), data) is None


@pytest.mark.parametrize("cash,pv", [
    (1, 0), (0, 1), (-1, 1), (1, -1), (float("nan"), 1),
    (1, float("inf")), (True, 1), ("100", 50), (1e308, 1e-308),
    (1e-308, 1e308), (10 ** 1000, 1),
])
def test_malformed_discount_ratio_is_rejected_with_reason(cash, pv):
    data = _data()
    dcf = data["valuation_engine"]["dcf"]
    dcf["fcff"][-1], dcf["discounted_fcff"][-1] = cash, pv
    timing = sw.discount_periods(data)
    assert not timing["verified"]
    assert timing["reason"]
    assert timing["periods"] == {}
    assert sw.compute(_assumptions(), data) is None


@pytest.mark.parametrize("field,value", [
    ("years", [2026] * 10), ("years", [2026.5] * 10),
    ("years", [True] * 10), ("years", [float("nan")] * 10),
    ("fcff", []), ("discounted_fcff", None), ("fcff", "bad"),
])
def test_invalid_engine_array_alignment_is_rejected(field, value):
    data = _data()
    data["valuation_engine"]["dcf"][field] = value
    assert not sw.discount_periods(data)["verified"]
    assert sw.compute(_assumptions(), data) is None


@pytest.mark.parametrize("wacc", [None, 0, -10, 41, True, "9.46", float("inf")])
def test_engine_wacc_cannot_be_substituted_by_scenario_wacc_for_inference(wacc):
    data = _data()
    data["valuation_engine"]["wacc_parameters"]["wacc_pct"] = wacc
    assert not sw.discount_periods(data)["verified"]
    assert sw.compute(_assumptions(wacc_pct=10), data) is None


@pytest.mark.parametrize("changes", [
    {"realization_year": 2030.5}, {"realization_year": True},
    {"continuing_value_teur": float("nan")},
    {"net_debt_realization_year_teur": float("inf")},
    {"continuing_value_teur": 1e308, "net_debt_realization_year_teur": -1e308},
    {"dilution_pct": True}, {"dilution_pct": float("inf")},
    {"dilution_pct": -1}, {"dilution_pct": 100}, {"wacc_pct": 0},
])
def test_malformed_scenario_inputs_are_rejected(changes):
    assert sw.compute(_assumptions(**changes), _data()) is None


def test_missing_evidence_never_falls_back_for_v2():
    data = _data()
    data["valuation_engine"]["dcf"] = {}
    timing = sw.discount_periods(data)
    assert timing["verified"] is False and timing["reason"]
    assert sw.compute(_assumptions(), data) is None
    data["valuation_engine"]["dcf"] = {
        "years": [2035], "fcff": [0], "discounted_fcff": [0]}
    assert sw.compute(_assumptions(), data) is None


def test_legacy_snapshot_keeps_old_amount_even_with_usable_engine_evidence():
    data = _data()
    del data["meta"]["scenario_timing_version"]
    result = sw.compute(_assumptions(), data)
    assert result["n"] == 10
    assert result["value_teur"] == pytest.approx(88000 / 1.0946 ** 10)
    assert result["timing_verified"] is False
    assert result["timing_basis"] == "legacy_year_difference"
    assert result["timing_reason"]
    assert "arvostuspäivää ei ole varmennettu" in result["steps"][3][0]
    assert "ei ole varmennettu" in sw.derivation_blocks(result)[1]["text"]


def test_legacy_missing_dcf_and_actuals_keep_forecast_year_fallback():
    data = {"forecast": {"years": [2026]},
            "valuation_engine": {"wacc_parameters": {"wacc_pct": 9.46}}}
    result = sw.compute(_assumptions(realization_year=2030), data)
    assert result["n"] == 5 and result["timing_verified"] is False
    assert result["value_teur"] == pytest.approx(88000 / 1.0946 ** 5)


def test_unknown_version_does_not_silently_use_legacy():
    data = _data()
    data["meta"]["scenario_timing_version"] = 3
    assert sw.compute(_assumptions(), data) is None


def test_negative_historical_period_preserves_consistent_future_exit_timing():
    data = _data()
    dcf = data["valuation_engine"]["dcf"]
    dcf["years"].insert(0, 2025)
    dcf["fcff"].insert(0, 100)
    dcf["discounted_fcff"].insert(0, 100 / 1.0946 ** (-1 + 49 / 365))
    timing = sw.discount_periods(data)
    assert timing["verified"] is True
    assert timing["periods"][2025] == pytest.approx(-1 + 49 / 365)
    result = sw.compute(_assumptions(), data)
    assert result["n"] == pytest.approx(9 + 49 / 365)
    assert sw.review_writer_values(_aligned_writer(data), data) == []


@pytest.mark.parametrize("first_n", [-0.5, 0])
def test_nonpositive_requested_exit_horizon_blocks_compute_and_writer_review(first_n):
    data = _data()
    dcf = data["valuation_engine"]["dcf"]
    dcf["discounted_fcff"] = [cash / 1.0946 ** (first_n + i)
                              for i, cash in enumerate(dcf["fcff"])]
    timing = sw.discount_periods(data)
    assert timing["verified"] is True
    assert timing["periods"][2026] == pytest.approx(first_n)
    assert sw.compute(_assumptions(realization_year=2026), data) is None
    wrapper = _aligned_writer(data)
    wrapper["machine_readable"]["optimistic_assumptions"]["realization_year"] = 2026
    assert any("ei voida varmentaa" in b for b in sw.review_writer_values(wrapper, data))


def test_timing_and_compute_do_not_mutate_sources_or_assumptions():
    data, assumptions = _data(), _assumptions(wacc_pct=12, dilution_pct=5)
    original_data, original_assumptions = deepcopy(data), deepcopy(assumptions)
    sw.discount_periods(data)
    sw.compute(assumptions, data)
    assert data == original_data
    assert assumptions == original_assumptions


def _wrapper():
    return {
        "cover": {"headline_value": "old"},
        "expected_value": {"value": 123},
        "machine_readable": {
            "scenarios": [
                {"name": "Konservatiivinen", "value_teur": 5000, "probability_pct": 60},
                {"name": "Optimistinen", "value_teur": 20000, "probability_pct": 20},
                {"name": "Pessimistinen", "value_teur": 1000, "probability_pct": 20},
            ],
            "optimistic_assumptions": _assumptions(),
        },
    }


def test_apply_uses_fractional_horizon_for_scenario_and_expected_value():
    wrapper, data = _wrapper(), _data()
    original_assumptions = deepcopy(wrapper["machine_readable"]["optimistic_assumptions"])
    original_data = deepcopy(data)
    result = sw.apply(wrapper, data)
    opt = wrapper["machine_readable"]["scenarios"][1]["value_teur"]
    assert opt == round(88000 / 1.0946 ** (9 + 49 / 365), 1)
    assert wrapper["expected_value"]["value"] == round(5000 * 0.6 + 1000 * 0.2 + opt * 0.2, 1)
    assert result["timing_verified"] is True
    assert wrapper["machine_readable"]["optimistic_assumptions"] == original_assumptions
    assert data == original_data


def test_apply_does_not_replace_writer_values_when_v2_timing_is_unavailable():
    wrapper, data = _wrapper(), _data()
    original_wrapper = deepcopy(wrapper)
    data["valuation_engine"]["dcf"]["fcff"][-1] = None
    assert sw.apply(wrapper, data) is None
    assert wrapper == original_wrapper


def test_english_legacy_explanation_does_not_claim_verified_present_value():
    data = _data()
    data["meta"] = {"report_language": "en", "country_code": "FI", "currency": "EUR"}
    result = sw.compute(_assumptions(), data)
    assert "valuation date unverified" in result["steps"][3][0]
    with report_context(data):
        assert "has not been verified" in sw.derivation_blocks(result)[1]["text"]


def _aligned_writer(data=None):
    data = _data() if data is None else data
    wrapper = _wrapper()
    opt = round(sw.compute(_assumptions(), data)["value_teur"], 1)
    expected = round(5000 * 0.6 + 1000 * 0.2 + opt * 0.2, 1)
    wrapper["machine_readable"]["scenarios"][1]["value_teur"] = opt
    wrapper["expected_value"]["value"] = expected
    return wrapper


def test_writer_review_accepts_correct_values_and_whole_number_rounding():
    wrapper = _aligned_writer()
    assert sw.review_writer_values(wrapper, _data()) == []
    wrapper["machine_readable"]["scenarios"][1]["value_teur"] = round(wrapper["machine_readable"]["scenarios"][1]["value_teur"])
    wrapper["expected_value"]["value"] = round(wrapper["expected_value"]["value"])
    assert sw.review_writer_values(wrapper, _data()) == []


def test_writer_review_blocks_stale_optimistic_and_expected_values():
    wrapper = _aligned_writer()
    wrapper["machine_readable"]["scenarios"][1]["value_teur"] = 12345
    wrapper["expected_value"]["value"] = 5669
    before = deepcopy(wrapper)
    blockers = sw.review_writer_values(wrapper, _data())
    assert any("optimistinen omistaja-arvo" in b for b in blockers)
    assert any("odotusarvo" in b for b in blockers)
    assert wrapper == before


def test_writer_review_blocks_missing_realization_year_timing_before_apply():
    wrapper, data = _aligned_writer(), _data()
    data["valuation_engine"]["dcf"]["fcff"][-1] = None
    assert any("ei voida varmentaa" in b for b in sw.review_writer_values(wrapper, data))


@pytest.mark.parametrize("field", ["optimistic_assumptions", "scenarios"])
def test_writer_review_blocks_missing_essential_scenario_inputs(field):
    wrapper = _aligned_writer()
    del wrapper["machine_readable"][field]
    assert sw.review_writer_values(wrapper, _data())


@pytest.mark.parametrize("field", ["value_teur", "probability_pct"])
def test_writer_review_blocks_missing_scenario_values_or_probabilities(field):
    wrapper = _aligned_writer()
    del wrapper["machine_readable"]["scenarios"][0][field]
    assert sw.review_writer_values(wrapper, _data())


def test_writer_review_blocks_missing_expected_value_and_invalid_probability_total():
    wrapper = _aligned_writer()
    del wrapper["expected_value"]
    assert any("odotusarvo puuttuu" in b for b in sw.review_writer_values(wrapper, _data()))
    wrapper = _aligned_writer()
    wrapper["machine_readable"]["scenarios"][0]["probability_pct"] = 79
    assert any("100 %" in b for b in sw.review_writer_values(wrapper, _data()))


def test_writer_review_blocks_conflicting_optimistic_value_aliases():
    wrapper = _aligned_writer()
    wrapper["machine_readable"]["scenarios"][1]["owner_value"] = 12345
    assert any("rinnakkaislukuja" in b for b in sw.review_writer_values(wrapper, _data()))


@pytest.mark.parametrize("text", [
    "Skenaarioiden odotusarvo on 5 669 tEUR.",
    "Odotusarvo on 20 % × 1 000 tEUR + 60 % × 5 000 tEUR + 20 % × 12 345 tEUR = 5 669 tEUR.",
    "Optimistisen skenaarion omistaja-arvo on 12 345 tEUR.",
    "Optimistinen arvo: 12 345 tEUR.",
    "Päivitetty skenaarioiden odotusarvo on 5 669 tEUR.",
    "Skenaarioiden omistaja-arvot ovat pessimistisessä 25 tEUR, konservatiivisessa 6 200 tEUR ja optimistisessa 12 345 tEUR.",
])
def test_writer_review_blocks_direct_stale_summary_or_scenario_assertions(text):
    wrapper = _aligned_writer()
    wrapper["sections"] = [{"id": "1", "blocks": [{"type": "paragraph", "text": text}]}]
    blockers = sw.review_writer_values(wrapper, _data())
    assert len(blockers) == 1 and "Osion 1" in blockers[0]


@pytest.mark.parametrize("text", [
    "Optimistisen skenaarion marginaali on 30 % ja liikevaihto 10 000 tEUR.",
    "Optimistisessa skenaariossa jatkuva arvo on 31 250 tEUR.",
    "Optimistinen arvo ylittää konservatiivisen perusskenaarion 6 200 tEUR.",
    "Kilpailija Oy:n skenaarioiden odotusarvo on 5 669 tEUR.",
    "Edellisen raportin odotusarvo on 5 669 tEUR.",
    "Skenaarioiden odotusarvo vuonna 2025 oli 5 669 tEUR.",
    "Optimistinen omistaja-arvo muuttuu aiemman raportin 24 680 tEUR:sta 12 345 tEUR:oon.",
    "Skenaarioiden odotusarvo riippuu 12 345 tEUR:n rahoituksesta.",
    "Odotusarvo on todennäköisyyden ja skenaarioarvon tulojen summa.",
    "Odotusarvo on 25 % suurempi ja investointi on 5 669 tEUR.",
    "Optimistinen omistaja-arvo vuonna 2035 on 29 750 tEUR ennen diskonttausta.",
    "Optimistinen skenaario on 10 000 tEUR liikevaihtoa.",
])
def test_writer_review_ignores_unrelated_historical_intermediate_and_percentage_numbers(text):
    wrapper = _aligned_writer()
    wrapper["sections"] = [{"id": "11", "blocks": [{"type": "paragraph", "text": text}]}]
    assert sw.review_writer_values(wrapper, _data()) == []


def test_writer_review_checks_labelled_cards_and_specific_scenario_table_outputs():
    wrapper = _aligned_writer()
    wrapper["sections"] = [{"id": "1", "blocks": [
        {"type": "metric_cards", "cards": [{"label": "Skenaarioiden odotusarvo", "value": "5 669 tEUR"}]},
        {"type": "table", "table_id": "scenarios", "unit": "tEUR",
         "columns": ["Skenaario", "Liikevaihto", "Omistaja-arvo (tEUR)", "Todennäköisyys"],
         "rows": [["Optimistinen", 10000, 12345, "25 %"]]},
        {"type": "table", "table_id": "optimistic_assumptions", "unit": "tEUR",
         "rows": [["Jatkuva arvo", 31250], ["Diskontattu omistaja-arvo", 12345]]},
        {"type": "scenario_table", "scenario": "Optimistinen", "value_teur": 12345},
    ]}]
    blockers = sw.review_writer_values(wrapper, _data())
    assert len(blockers) == 2  # Duplicate claims collapse to the same useful blocker.
    assert any("odotusarvo" in b for b in blockers)
    assert any("optimistinen omistaja-arvo" in b for b in blockers)


def test_writer_review_accepts_aligned_prose_and_metric_cards_without_mutation():
    wrapper, data = _aligned_writer(), _data()
    opt = round(wrapper["machine_readable"]["scenarios"][1]["value_teur"])
    expected = round(wrapper["expected_value"]["value"])
    wrapper["sections"] = [{"id": "11", "blocks": [
        {"type": "paragraph", "text": f"Optimistinen omistaja-arvo on {opt} tEUR. Skenaarioiden odotusarvo on {expected} tEUR."},
        {"type": "metric_cards", "cards": [{"label": "Skenaarioiden odotusarvo", "value": f"{expected} tEUR"}]},
    ]}]
    before, before_data = deepcopy(wrapper), deepcopy(data)
    assert sw.review_writer_values(wrapper, data) == []
    assert wrapper == before and data == before_data


def test_writer_review_supports_english_currency_format_and_legacy_is_unaffected():
    data = _data()
    data["meta"].update(report_language="en", country_code="US", currency="USD")
    wrapper = _aligned_writer(data)
    wrapper["sections"] = [{"id": 1, "blocks": [{"type": "paragraph", "text": "Expected value is 5,669 tUSD."}]}]
    assert any("Section 1's expected value" in b for b in sw.review_writer_values(wrapper, data))
    del data["meta"]["scenario_timing_version"]
    assert sw.review_writer_values(wrapper, data) == []


@pytest.mark.parametrize("names", [
    ["Optimistinen"],
    ["Pessimistinen", "Optimistinen"],
    ["Pessimistinen", "Konservatiivinen", "Tuntematon"],
    ["Konservatiivinen", "Konservatiivinen", "Optimistinen"],
    ["Pessimistinen", "Optimistinen", "Optimistinen"],
    ["Pessimistinen", "Optimistinen", "Optimistinen konservatiivinen"],
    ["Pessimistinen", "Konservatiivinen", "Optimistinen", "Muu"],
])
def test_v2_requires_exactly_one_pessimistic_base_and_optimistic_case(names):
    wrapper, data = _aligned_writer(), _data()
    opt = wrapper["machine_readable"]["scenarios"][1]["value_teur"]
    wrapper["machine_readable"]["scenarios"] = [
        {"name": name, "value_teur": opt if "optimis" in name.lower() else 1000,
         "probability_pct": 100 / len(names)} for name in names]
    wrapper["expected_value"]["value"] = sum(
        s["value_teur"] * s["probability_pct"] / 100
        for s in wrapper["machine_readable"]["scenarios"])
    before = deepcopy(wrapper)
    assert any("täsmälleen kolme" in b for b in sw.review_writer_values(wrapper, data))
    assert sw.apply(wrapper, data) is None
    assert wrapper == before


@pytest.mark.parametrize("base_name", ["Konservatiivinen perusskenaario", "Perusskenaario",
                                       "Conservative (base case)", "Base case", "Base-case",
                                       "Base", "Base scenario", "Realistic"])
def test_v2_accepts_correct_three_case_report_with_supported_base_labels(base_name):
    wrapper = _aligned_writer()
    wrapper["machine_readable"]["scenarios"][0]["name"] = base_name
    assert sw.review_writer_values(wrapper, _data()) == []
    assert len(scenario_compare.build_scenario_comparison_block(wrapper)[0]["columns"]) == 4


def _aligned_sidecar(wrapper):
    return {
        "scenarios": deepcopy(wrapper["machine_readable"]["scenarios"]),
        "expected_value_teur": wrapper["expected_value"]["value"],
        "realistic_base_case_teur": 5000,
        "confidence": "Preserve this metadata",
    }


@pytest.mark.parametrize("field,value", [
    ("expected_value_teur", 1000), ("realistic_base_case_teur", 1000),
    ("scenarios", []), ("scenarios", None),
])
def test_v2_review_blocks_stale_or_malformed_sidecar_fields(field, value):
    wrapper = _aligned_writer()
    wrapper["_scenarios"] = _aligned_sidecar(wrapper)
    wrapper["_scenarios"][field] = value
    before = deepcopy(wrapper)
    assert any("rinnakkainen skenaariodata" in b for b in sw.review_writer_values(wrapper, _data()))
    assert wrapper == before


@pytest.mark.parametrize("field,value", [("value_teur", 2000), ("probability_pct", 25)])
def test_v2_review_blocks_sidecar_scenario_values_and_probability_drift(field, value):
    wrapper = _aligned_writer()
    wrapper["_scenarios"] = _aligned_sidecar(wrapper)
    wrapper["_scenarios"]["scenarios"][1][field] = value
    assert any("rinnakkainen skenaariodata" in b for b in sw.review_writer_values(wrapper, _data()))


def test_v2_review_accepts_matching_sidecar_and_safe_absent_optional_fields():
    wrapper = _aligned_writer()
    wrapper["_scenarios"] = _aligned_sidecar(wrapper)
    assert sw.review_writer_values(wrapper, _data()) == []
    wrapper["_scenarios"] = {}
    assert sw.review_writer_values(wrapper, _data()) == []


def test_v2_apply_synchronizes_sidecar_used_by_renderer_and_comparison():
    wrapper, data = _aligned_writer(), _data()
    stale = {
        "expected_value_teur": 1000, "realistic_base_case_teur": 1000,
        "confidence": "Keep this",
        "scenarios": [
            {"name": "Pessimistinen", "value_teur": 0, "probability_pct": 25},
            {"name": "Konservatiivinen", "value_teur": 1000, "probability_pct": 50},
            {"name": "Optimistinen", "value_teur": 2000, "probability_pct": 25},
        ],
    }
    wrapper["_scenarios"] = stale
    before_stale = deepcopy(stale)
    blockers = sw.review_writer_values(wrapper, data)
    assert any("rinnakkainen skenaariodata" in b for b in blockers)
    result = sw.apply(wrapper, data)
    sidecar = wrapper["_scenarios"]
    assert sidecar["scenarios"] == wrapper["machine_readable"]["scenarios"]
    assert sidecar["scenarios"] is not wrapper["machine_readable"]["scenarios"]
    assert sidecar["expected_value_teur"] == wrapper["expected_value"]["value"]
    assert sidecar["realistic_base_case_teur"] == 5000
    assert sidecar["confidence"] == "Keep this"
    assert stale == before_stale
    assert sw.review_writer_values(wrapper, data) == []
    figures = render._canonical_figures(wrapper, render._derive(wrapper))
    assert figures["expected_value"] == pytest.approx(wrapper["expected_value"]["value"], abs=0.05)
    assert figures["range_high"] == round(result["value_teur"], 1)
    comparison = scenario_compare.build_scenario_comparison_block(wrapper)[0]
    assert comparison["rows"][0][1:] == ["1 000", "5 000", sw._fmt(result["value_teur"])]


def test_legacy_sidecar_and_lone_scenario_keep_existing_behavior():
    data, wrapper = _data(), _aligned_writer()
    del data["meta"]["scenario_timing_version"]
    wrapper["machine_readable"]["scenarios"] = [wrapper["machine_readable"]["scenarios"][1]]
    wrapper["machine_readable"]["scenarios"][0]["probability_pct"] = 100
    wrapper["_scenarios"] = {"scenarios": [], "expected_value_teur": 1000}
    before = deepcopy(wrapper["_scenarios"])
    assert sw.review_writer_values(wrapper, data) == []
    assert sw.apply(wrapper, data) is not None
    assert wrapper["_scenarios"] == before
