"""Fill `input_data.peers` from Valuatum's own model data.

Stage 1 (enrichment) already has to name the company's competitors — it is a
mandatory search there, because the scenarios lean on the competitive picture.
This module takes those names, resolves each to a followed model through
/company, and reads that model's figures from /modeldata. The LLM therefore
contributes only the NAME and the segment; every number, its fiscal year and
its source come from Valuatum. That is exactly what the writer prompt's
"kertoimet vain toimitetusta peers-datasta" rule needs, and why `peers` could
never be filled from the model's own memory.

A peer outside Valuatum's covered universe simply does not resolve and is
dropped. An unlisted peer resolves but has no market value, so it carries
growth and margin only — the writer's rule 2 already knows to use those and
not to invent a multiple for it.

WHAT THIS ENVIRONMENT ACTUALLY HAS (probed live 2026-08-05 against
arvonmaaritys-fi.valuatum.com, Innofactor 208280 + Siili 241299, both listed):
NO price data whatsoever. `market_cap_ye`, `pe`, `p_per_bv`, `p_per_s`,
`share_price`, `book_value_ps`, `dividend_yield` and 16 other spellings all
come back absent. And with no market cap, Valuatum's `enterprise_value`
degenerates to plain net debt — verified numerically identical to `net_debt`
on every peer and year — which makes `ev_per_ns` / `ev_per_ebitda` /
`ev_per_ebit` net-debt ratios wearing multiple names (Innofactor "EV/EBITDA
1.10x"). Publishing those as peer multiples would be worse than an empty peer
table, so market-based fields are emitted ONLY behind a real market cap. Until
Valuatum exposes prices, peers deliver the operational benchmark — size,
growth, margin, leverage — and the writer keeps saying that no market
cross-check was available.
"""
import asyncio
import re
import statistics
from datetime import datetime, timezone

from . import valuatum
from valuatum_kit.export_modeldata_json import roundish, y_tunnus

# Bounds the REST work per run: one /company search per name (cached, ~1.4 s
# cold) plus a single batched /modeldata call. Enrichment routinely names more
# competitors than a peer table can carry.
MAX_PEERS = 8

# Y-1 is the newest actual year; Y-2 is the fallback for a model whose newest
# year has not been populated yet. Y+0 is the first FORECAST year — asked for
# only because Valuatum's engine values (the DCF equity value, WACC) sit there,
# exactly where the stage-0 exporter reads them from. `_actual_years` keeps it
# out of the actual figures.
REL_POSES = ("Y-1", "Y-2", "Y+0")

# (peer field, varnames in priority order, kind). Money comes back in millions
# and percentages as fractions — the same convention the stage-0 exporter
# scales for, so peers land in tEUR/% like every other figure in the report.
# `cr_ebitda_xml` before `ebitda` is not cosmetic: the plain `ebitda` variable
# mirrors EBIT on these models (Innofactor 2024: 3.386 for both).
FIELDS: list[tuple[str, tuple[str, ...], str]] = [
    ("revenue_teur", ("ns",), "money"),
    ("ebitda_teur", ("cr_ebitda_xml", "ebitda"), "money"),
    ("ebit_teur", ("ebit",), "money"),
    ("net_earnings_teur", ("cr_net_earnings", "net_earnings"), "money"),
    ("equity_teur", ("cr_shareholders_equity",), "money"),
    ("net_debt_teur", ("net_debt",), "money"),
    ("revenue_growth_pct", ("ns_growth",), "pct"),
    ("ebit_pct", ("ebit_percent",), "pct"),
    ("ebitda_pct", ("ebitda_percent",), "pct"),
    ("equity_ratio_pct", ("equity_ratio",), "pct"),
    ("roe_pct", ("roe_percent",), "pct"),
    ("roi_pct", ("roi_before_tax_avg_cap",), "pct"),
    ("gearing_pct", ("gearing_percent",), "pct"),
    ("current_ratio", ("cr_current_ratio", "current_ratio"), "ratio"),
    ("quick_ratio", ("quick_ratio",), "ratio"),
    ("employees", ("cr_employees", "employees"), "count"),
    ("revenue_per_employee_teur", ("cr_ns_per_employee",), "money"),
    # Everything below is meaningless without a market price — see the module
    # docstring. MARKET_GATED drops them unless a market cap actually exists.
    ("market_cap_teur", ("market_cap_ye",), "money"),
    ("ev_teur", ("enterprise_value",), "money"),
    ("ev_per_sales", ("ev_per_ns",), "ratio"),
    ("ev_per_ebitda", ("ev_per_ebitda",), "ratio"),
    ("ev_per_ebit", ("ev_per_ebit",), "ratio"),
    ("pe", ("pe",), "ratio"),
    ("p_per_bv", ("p_per_bv",), "ratio"),
    ("p_per_sales", ("p_per_s",), "ratio"),
]

# Fields that are only true with a market price behind them. `ev_teur` is in
# here because Valuatum's enterprise value IS net debt when no market cap
# exists, so an "EV" without one is a mislabelled balance-sheet figure.
MARKET_GATED = ("ev_teur", "ev_per_sales", "ev_per_ebitda", "ev_per_ebit",
                "pe", "p_per_bv", "p_per_sales")

# Valuatum's engine output lives on the first forecast year, not on an actual
# one — read separately from the actual-year figures above.
VALUATION_FIELDS: list[tuple[str, tuple[str, ...], str]] = [
    ("model_equity_value_teur", ("value_of_equity_fcff",), "money"),
    ("wacc_pct", ("wacc",), "pct"),
    ("cost_of_equity_pct", ("cost_of_equity",), "pct"),
]

# Multiples derived from that model value. This is Asiakastieto's move in its
# Arvoraportti: its "P/E 6,2 (toimialan mediaani 7,1)" and "P/B 22,3 (mediaani
# 2,1)" are its own model value over net income and over book equity, never a
# share price — which is how it publishes multiples for unlisted Finnish
# companies at all. We can do the same because a peer's Valuatum model carries
# `value_of_equity_fcff`, and the target's own engine value is already in
# input_data, so both sides of the comparison come off the same engine.
IMPLIED_FIELDS = ("implied_pe", "implied_pbv", "implied_ev_sales",
                  "implied_ev_ebitda", "implied_ev_ebit")

MULTIPLES_BASIS = ("Valuatumin mallin oman pääoman arvo (DCF/FCFF) jaettuna "
                   "verrokin toteutuneella luvulla — mallipohjainen kerroin, "
                   "EI pörssikurssi eikä toteutunut kauppahinta")

# A peer whose newest actual year is older than this is history, not a
# comparison: Nixu's model stops at 2022 (delisted after the 2023 acquisition)
# and its 2022 margins say nothing about the target's market today.
# ponytail: calendar-year cutoff, not fiscal-calendar aware — widen if a
# legitimately slow-reporting peer starts getting dropped.
MAX_PEER_AGE_YEARS = 3

# Valuatum writes a ~1e8 "no data" sentinel (9.99999999999999E7) into
# unpopulated model cells and it leaks through REST verbatim, so an unpriced
# model reads P/S 70244862x rather than nothing. No real ratio, percentage or
# million-denominated figure here comes near 1e7, so treat anything at or above
# it as missing instead of printing garbage into the peer table.
SENTINEL_MIN = 1e7

_LEGAL_WORDS = {
    "oy", "oyj", "ab", "abp", "as", "asa", "ltd", "plc", "inc", "corp",
    "corporation", "group", "gmbh", "se", "nv", "bv", "holding", "holdings",
    "co", "company", "konserni",
}


def _norm(name: str | None) -> str:
    """Company name reduced to its distinguishing words. Legal forms differ
    between how the LLM writes a name and how Valuatum registers it ("Enento
    Group Oyj" vs "Enento Oyj"), and they carry no identity."""
    cleaned = re.sub(r"[^\w\s]", " ", (name or "").lower())
    return " ".join(w for w in cleaned.split() if w not in _LEGAL_WORDS)


def _same_company(query: str, candidate: str | None) -> bool:
    a, b = _norm(query), _norm(candidate)
    if not a or not b:
        return False
    return a == b or a.startswith(b + " ") or b.startswith(a + " ")


def _as_num(value) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        num = float(value)
    except (TypeError, ValueError):
        return None
    return None if abs(num) >= SENTINEL_MIN else num


def _figure(cells: dict, varnames: tuple[str, ...], kind: str):
    for var in varnames:
        num = _as_num(cells.get(var))
        if num is None:
            continue
        if kind == "money":
            num *= 1000
        elif kind == "pct":
            num *= 100
        elif kind == "count":
            return int(round(num))
        return roundish(num)
    return None


def _candidates(enrichment: dict, own_name: str | None) -> list[tuple[str, str, str]]:
    """(name, y-tunnus, segment) for each peer candidate the enrichment named.

    `finnish_peer_candidates` leads and `competitors` only fills the rest,
    because Valuatum's data is Finnish and a foreign competitor can never
    resolve: Singa Oy's real rivals are KaraFun (FR), Smule and StarMaker (US),
    which is why its first run produced "peers: 0/3" and a report saying no
    industry comparison could be made. The comparison set has to be built from
    the industry — the same call Asiakastieto makes when it prints a
    "vertailutoimiala" on its cover rather than a competitor list."""
    out: list[tuple[str, str, str]] = []
    seen: set[str] = set()
    enrichment = enrichment or {}
    for key_name in ("finnish_peer_candidates", "competitors"):
        for row in enrichment.get(key_name) or []:
            if not isinstance(row, dict):
                continue
            name = str(row.get("name") or "").strip()
            key = _norm(name)
            if not key or key in seen:
                continue
            # The target is not its own peer; enrichment sometimes lists it.
            if own_name and _same_company(name, own_name):
                continue
            seen.add(key)
            out.append((name, str(row.get("y_tunnus") or "").strip(),
                        str(row.get("segment") or "").strip()))
            if len(out) >= MAX_PEERS:
                return out
    return out


def _rank(query: str, rows: list[dict]) -> list[dict]:
    """Pick the models to try for one peer name.

    Exact name matches win outright: searching "Solteq" also returns "Solteq
    Finance Oy" and "Solteq Management Oy", and a subsidiary's figures are not
    the peer anyone means. Only when nothing matches exactly do the wider
    prefix matches count, shortest (i.e. closest to the bare company) first.

    Within the chosen bucket the consolidated model leads and the parent
    follows — a listed peer is comparable at group level, and the parent row of
    a group can be a near-empty holding company. Both are kept because the K
    model is not always the populated one; the caller takes the first that
    actually has figures."""
    exact = [r for r in rows if _norm(r.get("company_name")) == _norm(query)]
    bucket = exact or sorted(rows, key=lambda r: len(_norm(r.get("company_name"))))
    ranked = sorted(
        bucket, key=lambda r: not str(r.get("company_code") or "").upper().endswith("K")
    )
    return ranked[:2]


async def _resolve(name: str, y_tunnus: str = "") -> list[dict]:
    """Peer name → the Valuatum models that really are this company.

    A y-tunnus, when the enrichment knows one, is exact: /company resolves it
    without any name guessing, so it is tried first and the name check is
    skipped for its hits. Otherwise search the name without its legal form,
    which casts a wider net (Valuatum registers "Enento Oyj", the model writes
    "Enento Group Oyj"). That search is fuzzy enough to return unrelated
    companies — "Solteq" also returns "Fortum Battery Recycling Oy" — so the
    name check throws away everything it dragged in; a near-namesake is dropped
    rather than silently reported as a peer."""
    if y_tunnus:
        try:
            rows = await valuatum.search_company(y_tunnus)
        except Exception as e:
            print(f"peers: y-tunnus lookup '{y_tunnus}' failed: {e}", flush=True)
            rows = []
        if rows:
            return _rank(rows[0].get("company_name") or name, rows)
    query = _norm(name) or name
    if len(query) < valuatum.MIN_QUERY_LENGTH:
        return []
    try:
        rows = await valuatum.search_company(query)
    except Exception as e:
        print(f"peers: search '{query}' failed: {e}", flush=True)
        return []
    return _rank(name, [r for r in rows
                        if _same_company(name, r.get("company_name"))])


def _actual_years(model: dict) -> list[str]:
    """Years that are realized, newest first. Anything from `currentYear` on is
    an estimate — the same boundary the stage-0 exporter uses — and a forecast
    must never be compared against the target's realized figures."""
    data_map = model.get("dataMap") or {}
    years = sorted(data_map, key=lambda y: int(y), reverse=True)
    current = model.get("currentYear")
    if current is None:
        return years
    return [y for y in years if int(y) < int(current)]


def _best_year(model: dict) -> str | None:
    """Newest realized year that actually carries a revenue figure."""
    data_map = model.get("dataMap") or {}
    for year in _actual_years(model):
        if _as_num((data_map.get(year) or {}).get("ns")) is not None:
            return year
    return None


def _valuation_figures(model: dict) -> dict:
    """Engine output, taken from the newest year that has it (the first
    forecast year) rather than from the peer's chosen actual year."""
    data_map = model.get("dataMap") or {}
    out = {}
    for field, varnames, kind in VALUATION_FIELDS:
        for year in sorted(data_map, key=lambda y: int(y), reverse=True):
            value = _figure(data_map.get(year) or {}, varnames, kind)
            if value is not None:
                out[field] = value
                break
    return out


def _implied_multiples(figures: dict) -> dict:
    """Model-value multiples — see IMPLIED_FIELDS for why these are legitimate
    without a share price. Each guard mirrors the writer's own reject rules: no
    P/E on negative earnings, no P/BV on negative equity, no EV multiple on a
    negative denominator."""
    value = figures.get("model_equity_value_teur")
    if not isinstance(value, (int, float)) or value <= 0:
        return {}
    out = {}
    net_earnings = figures.get("net_earnings_teur")
    if isinstance(net_earnings, (int, float)) and net_earnings > 0:
        out["implied_pe"] = round(value / net_earnings, 2)
    equity = figures.get("equity_teur")
    if isinstance(equity, (int, float)) and equity > 0:
        out["implied_pbv"] = round(value / equity, 2)
    net_debt = figures.get("net_debt_teur")
    if isinstance(net_debt, (int, float)):
        ev = value + net_debt
        for field, denominator in (("implied_ev_sales", "revenue_teur"),
                                   ("implied_ev_ebitda", "ebitda_teur"),
                                   ("implied_ev_ebit", "ebit_teur")):
            base = figures.get(denominator)
            if isinstance(base, (int, float)) and base > 0:
                out[field] = round(ev / base, 2)
    return out


def _entry(model: dict, resolved: dict, segment: str, fetched: str,
           min_year: int) -> dict | None:
    data_map = model.get("dataMap") or {}
    year = _best_year(model)
    if not year or int(year) < min_year:
        return None
    cells = data_map.get(year) or {}
    figures = {}
    for field, varnames, kind in FIELDS:
        value = _figure(cells, varnames, kind)
        if value is not None:
            figures[field] = value
    priced = "market_cap_teur" in figures
    if not priced:
        for field in MARKET_GATED:
            figures.pop(field, None)
    figures.update(_valuation_figures(model))
    implied = _implied_multiples(figures)
    figures.update(implied)
    if implied:
        figures["multiples_basis"] = MULTIPLES_BASIS
    if not figures:
        return None
    fid = resolved["fid"]
    return {
        "name": model.get("companyName") or resolved.get("company_name"),
        "y_tunnus": y_tunnus(str(model.get("companyCode") or "") or None),
        "fid": fid,
        "segment": segment or None,
        "listed": priced,
        "fiscal_year": int(year),
        "source": f"Valuatum /modeldata (fid {fid})",
        "fetched": fetched,
        **figures,
    }


def _last_num(values) -> float | None:
    for value in reversed(values or []):
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            return value
    return None


def target_figures(input_data: dict) -> dict:
    """The target's own figures on exactly the peers' basis, so the comparison
    is engine-to-engine.

    `cumulative_discounted_fcff[0]` is the DCF enterprise value and
    `equity_value_before_floor` the equity value below the bridge, so their
    difference is net debt — verified against the bridge on the Valuatum
    fixture (471.39 EV − 316.39 equity = 202 debt − 47 cash)."""
    engine = input_data.get("valuation_engine") or {}
    dcf = engine.get("dcf") or {}
    wacc = engine.get("wacc_parameters") or {}
    actuals = input_data.get("actuals") or {}
    income = actuals.get("income_statement") or {}
    balance = actuals.get("balance_sheet") or {}
    equity_value = dcf.get("equity_value_before_floor")
    ev = next((v for v in (dcf.get("cumulative_discounted_fcff") or [])
               if isinstance(v, (int, float)) and not isinstance(v, bool)), None)
    figures = {
        "model_equity_value_teur": equity_value,
        "revenue_teur": _last_num(income.get("net_sales")),
        "ebitda_teur": _last_num(income.get("ebitda")),
        "ebit_teur": _last_num(income.get("ebit")),
        "net_earnings_teur": _last_num(income.get("net_earnings")),
        "equity_teur": _last_num(balance.get("equity_excl_capital_loans")),
        "wacc_pct": wacc.get("wacc_pct"),
        "cost_of_equity_pct": wacc.get("cost_of_equity_pct"),
    }
    if isinstance(ev, (int, float)) and isinstance(equity_value, (int, float)):
        figures["net_debt_teur"] = roundish(ev - equity_value)
    figures = {k: v for k, v in figures.items() if v is not None}
    figures.update(_implied_multiples(figures))
    years = (actuals.get("years") or [])
    if years:
        figures["fiscal_year"] = years[-1]
    return figures


def summarize(peer_list: list[dict], input_data: dict | None = None) -> dict:
    """Peer-set medians, sample size and period — the frame Asiakastieto puts
    around every ratio in its Arvoraportti ("60 yritystä toimialaluokasta
    82910, tilikaudelta 2019, mediaani 12,7 %").

    Computed here rather than left to the writer on purpose: a median the model
    works out in prose is a number nobody can check, and the report presents
    peer figures as sourced facts."""
    if not peer_list:
        return {}
    medians = {}
    fields = [(f, v, k) for f, v, k in FIELDS + VALUATION_FIELDS]
    fields += [(f, (), "ratio") for f in IMPLIED_FIELDS]
    for field, _varnames, _kind in fields:
        values = sorted(p[field] for p in peer_list
                        if isinstance(p.get(field), (int, float))
                        and not isinstance(p.get(field), bool))
        if values:
            medians[field] = roundish(statistics.median(values))
    revenues = [p["revenue_teur"] for p in peer_list if p.get("revenue_teur")]
    out = {
        "n": len(peer_list),
        "companies": [p["name"] for p in peer_list],
        "fiscal_years": sorted({p["fiscal_year"] for p in peer_list}),
        "listed_count": sum(1 for p in peer_list if p.get("listed")),
        "revenue_teur_min": min(revenues) if revenues else None,
        "revenue_teur_max": max(revenues) if revenues else None,
        "medians": medians,
        "multiples_basis": MULTIPLES_BASIS,
        "source": "Valuatum /modeldata, mediaani lasketaan koodissa verrokkien "
                  "toteutuneista luvuista",
    }
    if isinstance(input_data, dict):
        target = target_figures(input_data)
        if target:
            # Same engine, same arithmetic, both sides — the only way a
            # multiple comparison means anything when neither side is priced.
            out["target"] = target
    return out


async def resolve(enrichment: dict, own_name: str | None = None) -> list[dict]:
    """Enrichment's named competitors → peer figures for `input_data.peers`.

    Never raises: a peer table is an enhancement, and a REST hiccup must not
    take down a report the customer paid for. Returns [] when nothing resolves,
    which leaves the writer's existing "no peer data" wording in place."""
    try:
        wanted = _candidates(enrichment, own_name)
        if not wanted:
            return []
        resolved = await asyncio.gather(
            *(_resolve(name, y_tunnus) for name, y_tunnus, _ in wanted))
        pairs = [(rows, seg) for rows, (_, _y, seg) in zip(resolved, wanted) if rows]
        if not pairs:
            print(f"peers: 0/{len(wanted)} names resolved to a Valuatum model",
                  flush=True)
            return []
        var_poses = [
            {"varName": var, "relPos": rel}
            for _field, varnames, _kind in FIELDS + VALUATION_FIELDS
            for var in varnames
            for rel in REL_POSES
        ]
        fids = [row["fid"] for rows, _ in pairs for row in rows]
        models = await valuatum.modeldata(fids, var_poses)
        today = datetime.now(timezone.utc).date()
        fetched = today.isoformat()
        min_year = today.year - MAX_PEER_AGE_YEARS
        out = []
        for rows, segment in pairs:
            # Valuatum keeps stale duplicate models alongside the live one for
            # the same company (Nixu: one stopping at 2022, one current), so
            # take the freshest — ties keep the rank order, i.e. konserni.
            entries = [
                e for e in (
                    _entry(models.get(str(row["fid"])), row, segment, fetched,
                           min_year)
                    for row in rows if models.get(str(row["fid"]))
                ) if e
            ]
            if entries:
                out.append(max(entries, key=lambda e: e["fiscal_year"]))
        print(f"peers: {len(out)}/{len(wanted)} named competitors resolved to "
              f"Valuatum figures", flush=True)
        return out
    except Exception as e:
        print(f"peers: resolution failed, continuing without peers: {e}", flush=True)
        return []
