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
