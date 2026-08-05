"""Peer resolution: enrichment names them, Valuatum supplies every number.

The REST layer is stubbed here — what matters is the join logic: which name
resolves to which model, which cells survive the sentinel filter, and that
figures land in the report's units (tEUR, %).
"""
import asyncio

import pytest

from app import peers


def _rows(*companies):
    """Stub /company search hits, one row per (company, model)."""
    return [
        {"fid": fid, "company_name": name, "company_code": code,
         "analyst_name": "Profinder"}
        for name, code, fid in companies
    ]


def test_konserni_model_wins_and_parent_is_the_fallback(monkeypatch):
    """A listed peer has both a parent and a K (consolidated) model — group
    figures are the comparable ones, but only if that model carries data."""
    search = lambda q: _rows(("Enento Oyj", "21940077", 158955),
                             ("Enento Oyj", "21940077K", 227278))
    both_populated = _run(
        {"competitors": [{"name": "Enento Group Oyj"}]},
        search,
        {158955: _model("Enento Oyj", "21940077", ns=1.0),
         227278: _model("Enento Oyj", "21940077K", ns=142.5)},
        monkeypatch=monkeypatch,
    )
    assert [p["fid"] for p in both_populated] == [227278]

    empty_konserni = _run(
        {"competitors": [{"name": "Enento Group Oyj"}]},
        search,
        {158955: _model("Enento Oyj", "21940077", ns=1.0),
         227278: _model("Enento Oyj", "21940077K")},
        monkeypatch=monkeypatch,
    )
    assert [p["fid"] for p in empty_konserni] == [158955]


def _model(name, code, **cells):
    return {"companyName": name, "companyCode": code, "currentYear": 2026,
            "dataMap": {"2025": cells, "2024": {}}}


def test_unpriced_model_never_reports_net_debt_as_a_multiple(monkeypatch):
    """Valuatum's enterprise_value IS net debt when the model has no market
    cap, so ev_per_* are net-debt ratios wearing multiple names. Live probe
    2026-08-05: no Finnish model in this environment carries a price."""
    out = _run(
        {"competitors": [{"name": "Innofactor Oyj", "segment": "palvelu"}]},
        lambda q: _rows(("Innofactor Oyj", "16049320", 208280)),
        {208280: _model("Innofactor Oyj", "16049320", ns=77.576,
                        cr_ebitda_xml=6.338, ebit=3.386, ebit_percent=0.043647,
                        net_debt=6.979, enterprise_value=6.979,
                        ev_per_ebitda=1.101136, ev_per_ns=0.089963)},
        monkeypatch=monkeypatch,
    )
    peer = out[0]
    assert peer["listed"] is False
    assert peer["revenue_teur"] == 77576 and peer["ebitda_teur"] == 6338
    assert peer["net_debt_teur"] == 6979          # honest name for the figure
    for field in ("ev_teur", "ev_per_ebitda", "ev_per_sales", "pe"):
        assert field not in peer


def test_implied_multiples_come_off_the_model_value_not_a_price(monkeypatch):
    """Asiakastieto's move: P/E and P/BV from a model value, so unlisted peers
    still get multiples. Guards mirror the writer's reject rules."""
    model = {"companyName": "Gofore Oyj", "companyCode": "17101289",
             "currentYear": 2026,
             "dataMap": {"2025": {"ns": 191.382, "cr_ebitda_xml": 20.335,
                                  "ebit": 11.659, "cr_net_earnings": 9.248,
                                  "cr_shareholders_equity": 110.2,
                                  "net_debt": 10.641},
                         # Engine output sits on the first forecast year.
                         "2026": {"value_of_equity_fcff": 211.672, "wacc": 0.0946}}}
    out = _run(
        {"competitors": [{"name": "Gofore Oyj"}]},
        lambda q: _rows(("Gofore Oyj", "17101289", 292148)),
        {292148: model},
        monkeypatch=monkeypatch,
    )
    peer = out[0]
    assert peer["fiscal_year"] == 2025            # forecast year never wins
    assert peer["model_equity_value_teur"] == 211672
    assert peer["implied_pe"] == 22.89            # 211672 / 9248
    assert peer["implied_pbv"] == 1.92            # 211672 / 110200
    assert peer["implied_ev_sales"] == 1.16       # (211672 + 10641) / 191382
    assert peer["wacc_pct"] == 9.46
    assert "pörssikurssi" in peer["multiples_basis"]

    loss_making = dict(model)
    loss_making["dataMap"] = {**model["dataMap"],
                              "2025": {**model["dataMap"]["2025"],
                                       "cr_net_earnings": -2.0,
                                       "cr_shareholders_equity": -5.0}}
    out = _run(
        {"competitors": [{"name": "Gofore Oyj"}]},
        lambda q: _rows(("Gofore Oyj", "17101289", 292148)),
        {292148: loss_making},
        monkeypatch=monkeypatch,
    )
    assert "implied_pe" not in out[0] and "implied_pbv" not in out[0]


def test_target_is_measured_on_the_same_basis_as_the_peers():
    """Both sides off the Valuatum engine, or the comparison means nothing.
    EV − equity value = net debt, matching the DCF bridge."""
    target = peers.target_figures({
        "valuation_engine": {
            "dcf": {"equity_value_before_floor": 316.38765,
                    "cumulative_discounted_fcff": [471.38765, 457.93847]},
            "wacc_parameters": {"wacc_pct": 9.46, "cost_of_equity_pct": 11.8},
        },
        "actuals": {
            "years": [2024, 2025],
            "income_statement": {"net_sales": [380, 421], "ebitda": [12, -29],
                                 "ebit": [8, -46], "net_earnings": [5, -57]},
            "balance_sheet": {"equity_excl_capital_loans": [159, 90]},
        },
    })
    assert target["net_debt_teur"] == 155         # 471.39 − 316.39, = 202 − 47
    assert target["implied_pbv"] == 3.52          # 316.39 / 90
    assert target["implied_ev_sales"] == 1.12     # (316.39 + 155) / 421
    assert "implied_pe" not in target             # net earnings negative
    assert target["fiscal_year"] == 2025


def test_summary_medians_carry_sample_size_and_period(monkeypatch):
    out = _run(
        {"competitors": [{"name": "Gofore Oyj"}, {"name": "Solteq Oyj"}]},
        lambda q: _rows(("Gofore Oyj", "17101289", 1), ("Solteq Oyj", "04904840", 2)),
        {1: _model("Gofore Oyj", "17101289", ns=191.382, ebit_percent=0.061),
         2: _model("Solteq Oyj", "04904840", ns=46.735, ebit_percent=0.016)},
        monkeypatch=monkeypatch,
    )
    summary = peers.summarize(out)
    assert summary["n"] == 2
    assert summary["fiscal_years"] == [2025]
    assert summary["medians"]["ebit_pct"] == 3.85      # (6.1 + 1.6) / 2
    assert summary["revenue_teur_min"] == 46735
    assert summary["revenue_teur_max"] == 191382


def test_stale_peer_is_dropped(monkeypatch):
    """Nixu's model stops at 2022 — delisted after the 2023 acquisition."""
    stale = {"companyName": "Nixu Oyj", "companyCode": "07218117",
             "dataMap": {"2022": {"ns": 60.222}, "2021": {"ns": 51.8}}}
    out = _run(
        {"competitors": [{"name": "Nixu Oyj"}]},
        lambda q: _rows(("Nixu Oyj", "07218117", 209170)),
        {209170: stale},
        monkeypatch=monkeypatch,
    )
    assert out == []


def _run(enrichment, search, models, own_name=None, monkeypatch=None):
    async def fake_search(query):
        return search(query)

    async def fake_modeldata(fids, var_poses):
        return {str(f): models[f] for f in fids if f in models}

    monkeypatch.setattr(peers.valuatum, "search_company", fake_search)
    monkeypatch.setattr(peers.valuatum, "modeldata", fake_modeldata)
    return asyncio.run(peers.resolve(enrichment, own_name))


def test_listed_peer_lands_in_report_units(monkeypatch):
    out = _run(
        {"competitors": [{"name": "Enento Group Oyj", "segment": "ohjelmisto"}]},
        lambda q: _rows(("Enento Oyj", "19273988", 555)),
        {555: _model("Enento Oyj", "19273988", ns=142.5, ebit=21.3,
                     ebit_percent=0.149, market_cap_ye=430.0, ev_per_ebitda=9.4)},
        monkeypatch=monkeypatch,
    )
    assert len(out) == 1
    peer = out[0]
    assert peer["revenue_teur"] == 142500      # millions → tEUR
    assert peer["ebit_pct"] == 14.9            # fraction → %
    assert peer["ev_per_ebitda"] == 9.4        # ratios pass through
    assert peer["fiscal_year"] == 2025
    assert peer["listed"] is True
    assert peer["segment"] == "ohjelmisto"
    assert peer["y_tunnus"] == "1927398-8"
    assert "fid 555" in peer["source"] and peer["fetched"]


def test_sentinel_cells_never_reach_the_peer_table(monkeypatch):
    out = _run(
        {"competitors": [{"name": "Testi Oy"}]},
        lambda q: _rows(("Testi Oy", "16123988", 7)),
        {7: _model("Testi Oy", "16123988", ns=4.0, ebit_percent=0.08,
                   p_per_s=70244862.0, market_cap_ye=1e8)},
        monkeypatch=monkeypatch,
    )
    assert out[0]["revenue_teur"] == 4000
    # An unpriced model: the sentinel is dropped, so the peer is unlisted and
    # contributes growth/margin only rather than a garbage multiple.
    assert "market_cap_teur" not in out[0]
    assert out[0]["listed"] is False


def test_subsidiary_never_stands_in_for_the_named_peer(monkeypatch):
    """Real /company hits for "Solteq": the parent plus two subsidiaries (and
    an unrelated company the fuzzy search dragged in)."""
    out = _run(
        {"competitors": [{"name": "Solteq Oyj"}]},
        lambda q: _rows(("Fortum Battery Recycling Oy", "20000198", 92945),
                        ("Solteq Finance Oy", "06916271", 294612),
                        ("Solteq Management Oy", "23772150", 190155),
                        ("Solteq Oyj", "04904840", 157749)),
        {294612: _model("Solteq Finance Oy", "06916271", ns=0.2),
         190155: _model("Solteq Management Oy", "23772150", ns=0.1),
         157749: _model("Solteq Oyj", "04904840", ns=88.0)},
        monkeypatch=monkeypatch,
    )
    assert [p["fid"] for p in out] == [157749]


def test_near_namesake_is_dropped_not_reported_as_a_peer(monkeypatch):
    out = _run(
        {"competitors": [{"name": "Valuatum Oy"}]},
        lambda q: _rows(("Valu Steel Oy", "11111111", 9)),
        {9: _model("Valu Steel Oy", "11111111", ns=10.0)},
        monkeypatch=monkeypatch,
    )
    assert out == []


def test_target_company_is_not_its_own_peer(monkeypatch):
    out = _run(
        {"competitors": [{"name": "Valuatum Oy"}, {"name": "Enento Oyj"}]},
        lambda q: _rows(("Enento Oyj", "19273988", 555)),
        {555: _model("Enento Oyj", "19273988", ns=142.5)},
        own_name="Valuatum Oy",
        monkeypatch=monkeypatch,
    )
    assert [p["name"] for p in out] == ["Enento Oyj"]


def test_rest_failure_leaves_the_report_without_peers(monkeypatch):
    async def boom(query):
        raise RuntimeError("VALUATUM_TOKEN puuttuu")

    monkeypatch.setattr(peers.valuatum, "search_company", boom)
    out = asyncio.run(peers.resolve({"competitors": [{"name": "Enento Oyj"}]}))
    assert out == []


@pytest.mark.parametrize("year_cells,expected", [
    ({"2025": {}, "2024": {"ns": 3.0}}, 2024),          # newest year unpopulated
    ({"2025": {"ns": 5.0}, "2024": {"ns": 3.0}}, 2025),
])
def test_year_is_the_newest_one_with_revenue(monkeypatch, year_cells, expected):
    model = {"companyName": "Testi Oy", "companyCode": "16123988",
             "dataMap": year_cells}
    out = _run(
        {"competitors": [{"name": "Testi Oy"}]},
        lambda q: _rows(("Testi Oy", "16123988", 7)),
        {7: model},
        monkeypatch=monkeypatch,
    )
    assert out[0]["fiscal_year"] == expected
