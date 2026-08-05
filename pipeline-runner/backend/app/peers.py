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
"""
import asyncio
import re
from datetime import datetime, timezone

from . import valuatum
from valuatum_kit.export_modeldata_json import roundish, y_tunnus

# Bounds the REST work per run: one /company search per name (cached, ~1.4 s
# cold) plus a single batched /modeldata call. Enrichment routinely names more
# competitors than a peer table can carry.
MAX_PEERS = 8

# Y-1 is the newest actual year; Y-2 is the fallback for a model whose newest
# year has not been populated yet.
REL_POSES = ("Y-1", "Y-2")

# (peer field, varnames in priority order, kind). Money comes back in millions
# and percentages as fractions — the same convention the stage-0 exporter
# scales for, so peers land in tEUR/% like every other figure in the report.
FIELDS: list[tuple[str, tuple[str, ...], str]] = [
    ("revenue_teur", ("ns",), "money"),
    ("ebitda_teur", ("cr_ebitda_xml", "ebitda"), "money"),
    ("ebit_teur", ("ebit",), "money"),
    ("ev_teur", ("enterprise_value",), "money"),
    ("market_cap_teur", ("market_cap_ye",), "money"),
    ("revenue_growth_pct", ("ns_growth",), "pct"),
    ("ebit_pct", ("ebit_percent",), "pct"),
    ("equity_ratio_pct", ("equity_ratio",), "pct"),
    ("roe_pct", ("roe_percent",), "pct"),
    ("ev_per_sales", ("ev_per_ns",), "ratio"),
    ("ev_per_ebitda", ("ev_per_ebitda",), "ratio"),
    ("ev_per_ebit", ("ev_per_ebit",), "ratio"),
    ("pe", ("pe",), "ratio"),
    ("p_per_bv", ("p_per_bv",), "ratio"),
    ("p_per_sales", ("p_per_s",), "ratio"),
]

# Market-based multiples only exist for a peer with a market value; their
# presence is what makes a peer "listattu" for the writer's rule 2.
MARKET_FIELDS = ("market_cap_teur", "ev_per_sales", "ev_per_ebitda",
                 "ev_per_ebit", "pe", "p_per_bv", "p_per_sales")

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
        return roundish(num)
    return None


def _candidates(enrichment: dict, own_name: str | None) -> list[tuple[str, str]]:
    """(name, segment) for each competitor the enrichment stage named."""
    out: list[tuple[str, str]] = []
    seen: set[str] = set()
    for comp in (enrichment or {}).get("competitors") or []:
        if not isinstance(comp, dict):
            continue
        name = str(comp.get("name") or "").strip()
        key = _norm(name)
        if not key or key in seen:
            continue
        # The target company is not its own peer; enrichment sometimes lists it.
        if own_name and _same_company(name, own_name):
            continue
        seen.add(key)
        out.append((name, str(comp.get("segment") or "").strip()))
        if len(out) >= MAX_PEERS:
            break
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


async def _resolve(name: str) -> list[dict]:
    """Peer name → the Valuatum models that really are this company.

    Searching the name without its legal form casts a wider net (Valuatum
    registers "Enento Oyj", the model writes "Enento Group Oyj"). The search is
    fuzzy enough to return unrelated companies — "Solteq" also returns "Fortum
    Battery Recycling Oy" — so the name check throws away everything it dragged
    in; a near-namesake is dropped rather than silently reported as a peer."""
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


def _best_year(data_map: dict) -> str | None:
    """Newest year in the response that actually carries a revenue figure."""
    for year in sorted(data_map, key=lambda y: int(y), reverse=True):
        if _as_num((data_map.get(year) or {}).get("ns")) is not None:
            return year
    return None


def _entry(model: dict, resolved: dict, segment: str, fetched: str) -> dict | None:
    data_map = model.get("dataMap") or {}
    year = _best_year(data_map)
    if not year:
        return None
    cells = data_map.get(year) or {}
    figures = {}
    for field, varnames, kind in FIELDS:
        value = _figure(cells, varnames, kind)
        if value is not None:
            figures[field] = value
    if not figures:
        return None
    fid = resolved["fid"]
    return {
        "name": model.get("companyName") or resolved.get("company_name"),
        "y_tunnus": y_tunnus(str(model.get("companyCode") or "") or None),
        "fid": fid,
        "segment": segment or None,
        "listed": any(f in figures for f in MARKET_FIELDS),
        "fiscal_year": int(year),
        "source": f"Valuatum /modeldata (fid {fid})",
        "fetched": fetched,
        **figures,
    }


async def resolve(enrichment: dict, own_name: str | None = None) -> list[dict]:
    """Enrichment's named competitors → peer figures for `input_data.peers`.

    Never raises: a peer table is an enhancement, and a REST hiccup must not
    take down a report the customer paid for. Returns [] when nothing resolves,
    which leaves the writer's existing "no peer data" wording in place."""
    try:
        wanted = _candidates(enrichment, own_name)
        if not wanted:
            return []
        resolved = await asyncio.gather(*(_resolve(name) for name, _ in wanted))
        pairs = [(rows, seg) for rows, (_, seg) in zip(resolved, wanted) if rows]
        if not pairs:
            print(f"peers: 0/{len(wanted)} names resolved to a Valuatum model",
                  flush=True)
            return []
        var_poses = [
            {"varName": var, "relPos": rel}
            for _field, varnames, _kind in FIELDS
            for var in varnames
            for rel in REL_POSES
        ]
        fids = [row["fid"] for rows, _ in pairs for row in rows]
        models = await valuatum.modeldata(fids, var_poses)
        fetched = datetime.now(timezone.utc).date().isoformat()
        out = []
        for rows, segment in pairs:
            for row in rows:
                model = models.get(str(row["fid"]))
                entry = _entry(model, row, segment, fetched) if model else None
                if entry:
                    out.append(entry)
                    break
        print(f"peers: {len(out)}/{len(wanted)} named competitors resolved to "
              f"Valuatum figures", flush=True)
        return out
    except Exception as e:
        print(f"peers: resolution failed, continuing without peers: {e}", flush=True)
        return []
