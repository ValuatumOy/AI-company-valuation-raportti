"""Valuatum JSON export: run the vendored kit scripts to produce a complete
company modeldata JSON (the FAKTAT input_data for Stage 0).

Flow (matches valuatum-json-export-kit):
  1. /rest/modeldata by fid  → DCF / WACC / EVA / forecasts  (export script)
  2. Profinder MCP backfill by company_code → historical actuals
Secrets come from env only: VALUATUM_TOKEN, VALU_MCP_PROFINDER_URL.
Nulls are preserved; nothing is invented.
"""
import asyncio
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import httpx

KIT = Path(__file__).resolve().parent.parent / "valuatum_kit"
FETCH = KIT / "fetch_modeldata.py"
EXPORT = KIT / "export_modeldata_json.py"
BACKFILL = KIT / "backfill_modeldata_from_profinder.py"

COMPANY_URL = "https://profinder.valuatum.com/rest/company"

REQUIRED_KEYS = [
    "meta", "headcount", "actuals", "forecast", "forecast_parameters",
    "valuation_engine", "key_ratios", "credit_risk", "peers",
    "client_reported_signals", "flags",
]


def _slug(value: str) -> str:
    cleaned = "".join(c.lower() if c.isalnum() else "_" for c in value)
    return "_".join(p for p in cleaned.split("_") if p) or "company"


def _normalize_query(query: str) -> tuple[str, str]:
    """A Finnish y-tunnus is 7 digits + a check digit (hyphen optional, e.g.
    "1612398-8" or "16123988"); anything else is treated as a name search."""
    digits = re.sub(r"[\s-]", "", query.strip())
    if digits.isdigit() and len(digits) == 8:
        return "code", f"{digits[:7]}-{digits[7]}"
    return "name", query.strip()


async def search_company(query: str) -> list[dict]:
    """Resolve a company name or y-tunnus to Valuatum FIDs via the profinder
    REST /company endpoint — this is what unblocks self-serve for ANY company,
    not just the operator's pre-fetched ones. Returns one entry per
    (company, model) pair, since a company can have more than one followed
    model (fid); the caller picks when there's more than one candidate."""
    token = os.environ.get("VALUATUM_TOKEN")
    if not token:
        raise RuntimeError("VALUATUM_TOKEN puuttuu backendin ymparistosta.")
    param, value = _normalize_query(query)
    if len(value) < 2:
        return []
    async with httpx.AsyncClient(timeout=20) as client:
        resp = await client.get(
            COMPANY_URL,
            params={param: value},
            headers={"accept": "application/json", "authorization": f"Bearer {token}"},
        )
    resp.raise_for_status()
    out = []
    for c in resp.json() or []:
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
                "industry_text": c.get("industryText"),
                "analyst_name": m.get("analystName"),
            })
    return out


def _run(cmd: list[str]) -> tuple[int, str, str]:
    proc = subprocess.run(
        cmd, capture_output=True, text=True, cwd=str(KIT), env=os.environ.copy(),
        timeout=180,
    )
    return proc.returncode, proc.stdout, proc.stderr


def _derive_company_code(base: dict, override: str | None) -> str | None:
    if override:
        return override.strip()
    yt = (base.get("meta") or {}).get("y_tunnus")
    if not yt:
        return None
    code = yt.replace("-", "").strip()
    return code or None


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
):
    """Async generator yielding {step,...} events, ending with a 'ready' (or
    'error') event that carries the final JSON."""
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
        profinder = os.environ.get("VALU_MCP_PROFINDER_URL")
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
                    "VALU_MCP_PROFINDER_URL ei asetettu — actuals-backfill "
                    "ohitettu (historialliset kentät voivat olla harvoja)."
                )
            elif not company_code:
                warnings.append(
                    "company_code ei johdettavissa modeldatasta — anna "
                    "Advanced-osiossa company_code_override backfilliä varten."
                )

        data = json.loads(complete.read_text(encoding="utf-8"))
        warnings += _analyze(data)
        filename = f"{_slug(company_name)}_{fid}_modeldata_complete.json"
        yield {"step": "ready", "filename": filename, "warnings": warnings,
               "json": data}
    except Exception as e:  # noqa: BLE001
        yield {"step": "error", "message": f"{type(e).__name__}: {e}"}
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
