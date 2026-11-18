"""Read-only check of a PDF-service model before its first AI report.

Reads VALUATUM_PDF_API_BASE_URL and VALUATUM_PDF_TOKEN from the environment.
Never generates estimates, imports edits, starts a run or calls an LLM.

    python scripts/pdf_source_probe.py --query "2TAL AS" --country NO
    python scripts/pdf_source_probe.py --fid 123456 --country NO
"""
import argparse
import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import valuatum  # noqa: E402
from scripts.prepare_report_demo import generation_readiness  # noqa: E402
from valuatum_kit import export_modeldata_json as exporter, fetch_modeldata as fetcher  # noqa: E402
from valuatum_kit.config import SOURCE_VERIFIED_COUNTRIES  # noqa: E402


def _var_poses():
    poses = fetcher.build_var_poses(9, 10)
    positions = [f"Y-{i}" for i in range(1, 10)] + [f"Y+{i}" for i in range(10)]
    return poses + [{"varName": var, "relPos": pos} for var in exporter.EXTRA_VARS for pos in positions]


def _years(model):
    return sorted(int(y) for y in (model.get("dataMap") or {}) if str(y).isdigit() and len(str(y)) == 4)


def _empty_years(model):
    """Years whose rows hold only zeros or nulls: the basic report printed such columns."""
    rows = model.get("dataMap") or {}
    return [y for y in _years(model)
            if not any(isinstance(v, (int, float)) and not isinstance(v, bool) and v != 0
                       for v in (rows.get(str(y)) or {}).values())]


async def probe(fid, country):
    model = (await valuatum.modeldata([fid], _var_poses(), country="PDF")).get(str(fid)) or {}
    if not model:
        return {"fid": fid, "error": "No model returned for this fid"}
    currency = model.get("currency")
    out = {
        "fid": fid, "company_name": model.get("companyName"), "company_code": model.get("companyCode"),
        "source_country_code": model.get("countryCode"), "currency": currency,
        "current_year": model.get("currentYear"), "years": _years(model), "empty_years": _empty_years(model),
        "sample_ns_by_year": {y: (model["dataMap"].get(str(y)) or {}).get("ns") for y in _years(model)},
    }
    try:
        data = exporter.build_payload(model, [], report_language="en", country_code=country,
                                      source_currency=currency, verify_cashflows=True)
    except ValueError as exc:
        return {**out, "export_error": str(exc)}
    readiness = generation_readiness(data)
    return {**out, "actual_years": readiness["actual_years"], "forecast_years": readiness["forecast_years"],
            "unit": readiness["unit"], "numerical_input_ready": readiness["numerical_input_ready"],
            "blockers": readiness["blockers"],
            "wacc_pct": ((data.get("valuation_engine") or {}).get("wacc_parameters") or {}).get("wacc_pct"),
            "dcf_equity": ((data.get("valuation_engine") or {}).get("dcf") or {}).get("equity_value_before_floor")}


async def main():
    parser = argparse.ArgumentParser(description=__doc__)
    target = parser.add_mutually_exclusive_group(required=True)
    target.add_argument("--fid", type=int)
    target.add_argument("--query", help="Company name or code as stored in the PDF service")
    parser.add_argument("--country", required=True, choices=SOURCE_VERIFIED_COUNTRIES)
    args = parser.parse_args()
    if args.query:
        candidates = await valuatum.search_company(args.query, country="PDF")
        print(json.dumps({"candidates": candidates}, ensure_ascii=False, indent=2, default=str))
        fids = [c["fid"] for c in candidates]
    else:
        fids = [args.fid]
    for fid in fids:
        print(json.dumps(await probe(fid, args.country), ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    asyncio.run(main())
