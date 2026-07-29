"""Valuatum JSON export: run the vendored kit scripts to produce a complete
company modeldata JSON (the FAKTAT input_data for Stage 0).

Flow (matches valuatum-json-export-kit):
  1. /rest/modeldata by fid  → DCF / WACC / EVA / forecasts  (export script)
  2. Profinder MCP backfill by company_code → historical actuals
Secrets come from env only: VALUATUM_TOKEN, VALUATUM_MCP_URL.
Nulls are preserved; nothing is invented.
"""
import asyncio
import copy
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import httpx

from . import estimate_trigger
from valuatum_kit.config import api_base_url, mcp_url

KIT = Path(__file__).resolve().parent.parent / "valuatum_kit"
FETCH = KIT / "fetch_modeldata.py"
EXPORT = KIT / "export_modeldata_json.py"
BACKFILL = KIT / "backfill_modeldata_from_profinder.py"

REQUIRED_KEYS = [
    "meta", "headcount", "actuals", "forecast", "forecast_parameters",
    "valuation_engine", "key_ratios", "credit_risk", "peers",
    "client_reported_signals", "flags",
]


def company_url() -> str:
    return api_base_url() + "/company"


def _slug(value: str) -> str:
    cleaned = "".join(c.lower() if c.isalnum() else "_" for c in value)
    return "_".join(p for p in cleaned.split("_") if p) or "company"


# Valuatum's /company rejects anything shorter with a 400 ("The search
# parameter name must contain at least 3 characters"), so gate locally rather
# than spend a round trip on a guaranteed error.
MIN_QUERY_LENGTH = 3


def _normalize_query(query: str) -> tuple[str, str]:
    """A Finnish y-tunnus is 7 digits + a check digit (hyphen optional, e.g.
    "1612398-8" or "16123988"); anything else is treated as a name search."""
    clean = re.sub(r"[\s-]", "", query.strip()).upper()
    code = clean[:-1] if clean.endswith("K") else clean
    if code.isdigit() and len(code) == 8:
        return "code", f"{code[:7]}-{code[7]}"
    return "name", query.strip()


def _finnish_industry(company: dict) -> str | None:
    """/rest/company's industryText is English ("47.400 Retail sale of...")
    but its industryTree carries fi names at every level — a Finnish report
    cover should show the Finnish label. Walk to the deepest node (the tree is
    a single path down to the company's own NACE class) and take its fi name.
    ponytail: children[0] assumes single-path; falls back to English if not."""
    node = company.get("industryTree")
    while isinstance(node, dict):
        children = node.get("children")
        if not (isinstance(children, list) and children and isinstance(children[0], dict)):
            break
        node = children[0]
    if isinstance(node, dict):
        name = node.get("name") or {}
        if isinstance(name, dict) and name.get("fi"):
            return str(name["fi"])
    return None


def _company_city(company: dict | None) -> str | None:
    """/rest/company carries no explicit kotipaikka, but its companyData block
    has the postal town — the closest thing available, and the same for the vast
    majority of companies. Postal address first, visiting address as fallback."""
    data = (company or {}).get("companyData")
    if not isinstance(data, dict):
        return None
    for key in ("POSTIOSOITTEEN_POSTITOIMIPAIKKA", "KAYNTIOSOITTEEN_POSTITOIMIPAIKKA"):
        value = data.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _industry_metadata(company: dict | None) -> dict:
    company = company or {}
    return {
        "industry_text": _finnish_industry(company) or company.get("industryText"),
        "industry_code": company.get("industryCode"),
        "industry_id": company.get("industryId"),
        "industry_tree": company.get("industryTree"),
    }


def _apply_company_metadata(data: dict, metadata: dict | None) -> dict:
    """Hydrate the stage-0 FAKTAT meta block with company-search metadata.

    The /modeldata endpoint does not currently include industry details, but
    /rest/company does. Keep this as a small overlay so the numeric export stays
    the source of truth and the report writer can still read meta.industry.
    """
    if not isinstance(data, dict) or not isinstance(metadata, dict):
        return data
    meta = data.setdefault("meta", {})
    if not isinstance(meta, dict):
        data["meta"] = meta = {}

    industry_text = metadata.get("industry_text")
    if industry_text:
        meta["industry"] = industry_text
    industry_code = metadata.get("industry_code")
    if industry_code is not None and industry_code != "":
        meta["industry_code"] = str(industry_code)
    if metadata.get("industry_id") is not None:
        meta["industry_id"] = metadata.get("industry_id")
    if metadata.get("industry_tree") is not None:
        meta["industry_tree"] = metadata.get("industry_tree")
    return data


# One shared client — and therefore one TLS connection pool — for every
# Valuatum REST call. A fresh AsyncClient per request meant a full TCP+TLS
# handshake on every homepage search (~130 ms of the round trip, measured).
_CLIENT: httpx.AsyncClient | None = None

# /company is the slow part of homepage search (~1.4 s at profindertest), and it
# gets asked the same question repeatedly: search-as-you-type repeats the query,
# and the client site looks the SAME company up a second time when the visitor
# clicks a result (name query for the dropdown, then code query for the company
# page). A short TTL removes that duplicate round trip. Bounded and in-process —
# same tradeoff as _RATE_HITS in main.py; move to the DB if this ever runs on
# more than one instance.
_ROWS_TTL = float(os.environ.get("VALUATUM_COMPANY_CACHE_TTL", "60"))
_ROWS_CACHE_MAX = 512
_ROWS_CACHE: dict[tuple[str, str], tuple[float, list[dict]]] = {}


def _client() -> httpx.AsyncClient:
    global _CLIENT
    if _CLIENT is None:
        _CLIENT = httpx.AsyncClient(
            timeout=20,
            limits=httpx.Limits(max_keepalive_connections=10, max_connections=20),
        )
    return _CLIENT


async def aclose_client() -> None:
    """Release the pooled connections on app shutdown (called from _lifespan)."""
    global _CLIENT
    client, _CLIENT = _CLIENT, None
    if client is not None:
        await client.aclose()


async def _company_rows(param: str, value: str) -> list[dict]:
    token = os.environ.get("VALUATUM_TOKEN")
    if not token:
        raise RuntimeError("VALUATUM_TOKEN puuttuu backendin ymparistosta.")
    key = (param, value)
    now = time.monotonic()
    cached = _ROWS_CACHE.get(key)
    if cached and now - cached[0] < _ROWS_TTL:
        # Deep copy both ways: industry_tree is handed straight to the stage-0
        # meta block by _apply_company_metadata, so a caller mutating it would
        # otherwise poison every later hit on this key.
        return copy.deepcopy(cached[1])
    resp = await _client().get(
        company_url(),
        params={param: value},
        headers={"accept": "application/json", "authorization": f"Bearer {token}"},
    )
    resp.raise_for_status()
    rows = resp.json() or []
    if _ROWS_TTL > 0:
        if len(_ROWS_CACHE) >= _ROWS_CACHE_MAX:
            _ROWS_CACHE.clear()  # crude but bounded; entries are cheap to refetch
        _ROWS_CACHE[key] = (now, copy.deepcopy(rows))
    return rows


async def search_company(query: str) -> list[dict]:
    """Resolve a company name or y-tunnus to Valuatum FIDs via the profinder
    REST /company endpoint — this is what unblocks self-serve for ANY company,
    not just the operator's pre-fetched ones. Returns one entry per
    (company, model) pair, since a company can have more than one followed
    model (fid); the caller picks when there's more than one candidate."""
    param, value = _normalize_query(query)
    if len(value) < MIN_QUERY_LENGTH:
        return []
    rows = await _company_rows(param, value)
    out = []
    for c in rows:
        industry = _industry_metadata(c)
        for m in c.get("models") or []:
            fid = m.get("followedModelId")
            if fid is None:
                continue
            try:
                fid = int(fid)
            except (TypeError, ValueError):
                continue
            out.append({
                "fid": fid,
                "company_name": c.get("companyName"),
                "company_code": c.get("companyCode"),
                "city": _company_city(c),
                **industry,
                "analyst_name": m.get("analystName"),
            })
    return out


async def lookup_company_metadata(
    fid: int,
    company_name: str | None = None,
    company_code: str | None = None,
) -> dict:
    """Best-effort Valuatum /company metadata lookup for a known followed model.

    Used by the exporter/admin path too, so meta.industry is not limited to the
    self-serve UI that already called /api/company-search.
    """
    queries = [q for q in (company_code, company_name) if q]
    seen: set[tuple[str, str]] = set()
    for query in queries:
        param, value = _normalize_query(str(query))
        if len(value) < MIN_QUERY_LENGTH or (param, value) in seen:
            continue
        seen.add((param, value))
        try:
            rows = await _company_rows(param, value)
        except Exception:
            continue
        fallback = rows[0] if rows else None
        for c in rows:
            for m in c.get("models") or []:
                try:
                    if int(m.get("followedModelId")) == int(fid):
                        return _industry_metadata(c)
                except (TypeError, ValueError):
                    continue
        if fallback:
            return _industry_metadata(fallback)
    return {}


def _run(cmd: list[str]) -> tuple[int, str, str]:
    proc = subprocess.run(
        cmd, capture_output=True, text=True, cwd=str(KIT), env=os.environ.copy(),
        timeout=180,
    )
    return proc.returncode, proc.stdout, proc.stderr


def _derive_company_code(base: dict, override: str | None) -> str | None:
    if override:
        return override.strip()
    meta = base.get("meta") or {}
    yt = meta.get("y_tunnus")
    if not yt:
        return None
    code = yt.replace("-", "").strip()
    if not code:
        return None
    # Konserni (consolidated) models need the K-suffixed company code so the
    # Profinder backfill returns consolidated statements, not parent-level ones.
    # Verified against live Profinder: "16123988K" returns consolidated data,
    # the dashed form "1612398-8K" errors — so append K to the dash-stripped
    # code. Without this a konserni FID produced konserni forecasts + a
    # "consolidated" level label but parent-level historical actuals.
    if meta.get("level") == "consolidated" and not code.endswith("K"):
        code += "K"
    return code


def _analyze(data: dict) -> list[str]:
    warnings: list[str] = []
    missing = [k for k in REQUIRED_KEYS if k not in data]
    if missing:
        warnings.append(f"Skeemasta puuttuu top-level avaimia: {missing}")

    ve = data.get("valuation_engine", {}) or {}
    dcf = ve.get("dcf", {}) or {}
    wacc = ve.get("wacc_parameters", {}) or {}
    has_dcf = any(v is not None for v in (dcf.get("discounted_fcff") or []))
    has_fcff = any(v is not None for v in (dcf.get("fcff") or []))
    has_wacc = wacc.get("wacc_pct") is not None
    if not (has_dcf or has_fcff or has_wacc):
        warnings.append(
            "Forecasts may need to be generated in Valuatum UI first, then "
            "rerun export."
        )
    return warnings


async def export_stream(
    company_name: str,
    fid: int,
    actuals: int = 15,
    estimates: int = 10,
    company_code_override: str | None = None,
    industry_text: str | None = None,
    industry_code: str | None = None,
    industry_id=None,
    industry_tree=None,
    skip_estimate_generation: bool = False,
):
    """Async generator yielding {step,...} events, ending with a 'ready' (or
    'error') event that carries the final JSON.

    `skip_estimate_generation`: bypass the estimate-generation gate. Set ONLY on a
    forecast-edit round (ACE #3048) — the edited fid is a fresh model whose
    values ARE the user's forecasts, and running the gate (updateEstimates, which
    does not apply consensus) would overwrite them."""
    if not os.environ.get("VALUATUM_TOKEN"):
        yield {"step": "error", "message": "VALUATUM_TOKEN puuttuu backendin .env:stä."}
        return
    if not EXPORT.exists():
        yield {"step": "error", "message": f"Kit-skriptejä ei löydy: {KIT}"}
        return

    warnings: list[str] = []
    tmp = Path(tempfile.mkdtemp(prefix="valu_"))
    base = tmp / "base.json"
    complete = tmp / "complete.json"
    try:
        if skip_estimate_generation:
            yield {"step": "estimates",
                   "label": "Skipping estimate generation (edited forecast model)"}
        else:
            yield {"step": "estimates", "label": "Generating estimates"}
            await estimate_trigger.trigger_and_wait(fid)

        # 1. modeldata → base JSON
        yield {"step": "fetch", "label": "Fetching modeldata"}
        cmd = [
            sys.executable, str(EXPORT), "--fetch-script", str(FETCH),
            "--fid", str(fid), "--actuals", str(actuals),
            "--estimates", str(estimates),
            "--company-name-override", company_name,
            "--output", str(base),
        ]
        if company_code_override:
            cmd += ["--company-code-override", company_code_override.strip()]
        rc, out, err = await asyncio.to_thread(_run, cmd)
        if rc != 0 or not base.exists():
            yield {"step": "error",
                   "message": "modeldata-haku epäonnistui:\n" + (err or out)[:1500]}
            return
        base_data = json.loads(base.read_text(encoding="utf-8"))

        # 2. Profinder backfill → complete JSON
        company_code = _derive_company_code(base_data, company_code_override)
        profinder = mcp_url()
        if profinder and company_code:
            yield {"step": "backfill", "label": "Backfilling actuals",
                   "company_code": company_code}
            rc, out, err = await asyncio.to_thread(
                _run,
                [
                    sys.executable, str(BACKFILL), "--input", str(base),
                    "--output", str(complete), "--company-code", company_code,
                    "--limit", str(max(actuals, 10)),
                ],
            )
            if rc != 0 or not complete.exists():
                warnings.append(
                    "Profinder-backfill epäonnistui — historialliset actualsit "
                    "voivat olla puutteelliset. " + (err or out)[:400]
                )
                shutil.copyfile(base, complete)
        else:
            shutil.copyfile(base, complete)
            if not profinder:
                warnings.append(
                    "VALUATUM_MCP_URL ei asetettu — actuals-backfill "
                    "ohitettu (historialliset kentät voivat olla harvoja)."
                )
            elif not company_code:
                warnings.append(
                    "company_code ei johdettavissa modeldatasta — anna "
                    "Advanced-osiossa company_code_override backfilliä varten."
                )

        data = json.loads(complete.read_text(encoding="utf-8"))
        supplied_metadata = {
            "industry_text": industry_text,
            "industry_code": industry_code,
            "industry_id": industry_id,
            "industry_tree": industry_tree,
        }
        if not any(v is not None and v != "" for v in supplied_metadata.values()):
            meta = data.get("meta") or {}
            supplied_metadata = await lookup_company_metadata(
                fid=fid,
                company_name=company_name,
                company_code=company_code or meta.get("y_tunnus"),
            )
        data = _apply_company_metadata(data, supplied_metadata)
        warnings += _analyze(data)
        filename = f"{_slug(company_name)}_{fid}_modeldata_complete.json"
        yield {"step": "ready", "filename": filename, "warnings": warnings,
               "json": data}
    except Exception as e:  # noqa: BLE001
        yield {"step": "error", "message": f"{type(e).__name__}: {e}"}
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
