"""Prepare an admin run request offline. Never fetch, start a run or call an LLM.

After separate approval, the JSON can be submitted to the existing admin run
endpoint, or passed to store.create_run/run_stages in an isolated local runner.
APP_TOKEN is not needed for preparation. Paid execution still needs provider
credentials and explicit approval; this script deliberately has no execute flag.
"""
import argparse
import copy
import json
import math
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.report_context import normalize_input
from app.sec_report_input import selected_facts_input
from valuatum_kit.export_modeldata_json import build_payload


def prepare(data, *, input_format, pipeline_id, country_code, source_currency,
            report_language="en", minimum_actual_year=None, source_metadata=None, source_host=None):
    params = {"report_language": report_language, "country_code": country_code,
              "source_currency": source_currency}
    if input_format == "valuatum-model":
        if "dataMap" not in data and len(data) == 1:
            candidate = next(iter(data.values()))
            if isinstance(candidate, dict) and "dataMap" in candidate:
                data = candidate
        if data.get("currency") != source_currency:
            raise ValueError("Raw model.currency must verify source_currency")
        if not data.get("currentYear"):
            raise ValueError("Raw model.currentYear is required to distinguish actuals and forecasts")
        data = copy.deepcopy(data)
        # Direct modeldata responses also contain aggregate/quarter positions.
        # The existing report pipeline consumes annual rows only.
        data["dataMap"] = {str(y): values for y, values in data.get("dataMap", {}).items()
                           if len(str(y)) == 4 and str(y)[:2] in ("19", "20")}
        data = build_payload(data, report_language=report_language,
                             country_code=country_code, source_currency=source_currency)
    elif input_format == "sec-companyfacts":
        if country_code != "US" or source_currency != "USD":
            raise ValueError("SEC snapshot requires country_code US and source_currency USD")
        data = selected_facts_input(data)
    elif input_format != "normalized":
        raise ValueError("Unsupported input format")
    data = normalize_input(data, params)
    if source_metadata is not None:
        if source_metadata.get("currency") not in (None, source_currency):
            raise ValueError("Source snapshot metadata currency conflicts with input")
        data.setdefault("source_provenance", {})["snapshot_metadata"] = copy.deepcopy(source_metadata)
    if source_host:
        data.setdefault("source_provenance", {})["endpoint_host"] = source_host
    years = (data.get("actuals") or {}).get("years") or []
    minimum = minimum_actual_year if minimum_actual_year is not None else date.today().year - 2
    if not years or max(int(y) for y in years) < minimum:
        raise ValueError(f"Stale or missing actuals: latest year must be at least {minimum}")
    return {"pipeline_id": pipeline_id, "input_data": data,
            "params": params, "stop_on_failure": True}


def generation_readiness(data):
    """Source-completeness gate, never an estimate or valuation calculation."""
    blockers = []
    def finite(value):
        return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)
    actuals = data.get("actuals") or {}
    latest_sales = (actuals.get("income_statement") or {}).get("net_sales") or []
    if not latest_sales or not finite(latest_sales[-1]):
        blockers.append("Latest actual-year revenue is missing")
    forecast = data.get("forecast") or {}
    years = forecast.get("years") or []
    if not years:
        blockers.append("No source forecast years supplied")
    for key in ("net_sales", "ebit"):
        values = forecast.get(key) or []
        if not years or len(values) != len(years) or any(
                not finite(v) for v in values):
            blockers.append(f"Complete source forecast {key} is missing")
    engine = data.get("valuation_engine") or {}
    wacc = (engine.get("wacc_parameters") or {}).get("wacc_pct")
    if not finite(wacc) or not 0 < wacc < 100:
        blockers.append("Source WACC assumption is missing")
    dcf_value = (engine.get("dcf") or {}).get("equity_value_before_floor")
    if not finite(dcf_value):
        blockers.append("Source DCF equity value is missing")
    snapshot = (data.get("source_provenance") or {}).get("snapshot_metadata") or {}
    provenance = copy.deepcopy(snapshot.get("provenance") or {})
    if (data.get("source_provenance") or {}).get("endpoint_host"):
        provenance["endpoint_host"] = data["source_provenance"]["endpoint_host"]
    if snapshot.get("request"):
        provenance["modeldata_request"] = snapshot["request"]
    if not provenance:
        provenance = {"filing_url": (data.get("meta") or {}).get("source_url")}
    return {"numerical_input_ready": not blockers, "blockers": blockers,
            "generation_approved": False, "production_verified": False,
            "generation_performed": False, "estimate_generation_performed": False,
            "currency": (data.get("meta") or {}).get("currency"), "unit": data.get("unit"),
            "source_country": (data.get("meta") or {}).get("country_code"),
            "forecast_algorithm_verified": forecast.get("is_system_deterministic") is True,
            "source_provenance": provenance,
            "actual_years": actuals.get("years") or [], "forecast_years": years,
            "execution_requires_explicit_approval": True}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--input-format", required=True,
                        choices=["valuatum-model", "normalized", "sec-companyfacts"])
    parser.add_argument("--pipeline-id", required=True)
    parser.add_argument("--country-code", required=True, choices=["FI", "DK", "US"])
    parser.add_argument("--source-currency", required=True, choices=["EUR", "DKK", "USD"])
    parser.add_argument("--report-language", default="en", choices=["fi", "en"])
    parser.add_argument("--minimum-actual-year", type=int)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--readiness-output", type=Path)
    parser.add_argument("--source-metadata", type=Path)
    parser.add_argument("--source-host", help="Verified source endpoint hostname, for offline provenance")
    args = parser.parse_args()
    request = prepare(json.loads(args.input.read_text(encoding="utf-8")),
                      input_format=args.input_format, pipeline_id=args.pipeline_id,
                      country_code=args.country_code, source_currency=args.source_currency,
                      report_language=args.report_language,
                      minimum_actual_year=args.minimum_actual_year,
                      source_metadata=json.loads(args.source_metadata.read_text(encoding="utf-8"))
                      if args.source_metadata else None, source_host=args.source_host)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(request, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    readiness_path = args.readiness_output or args.output.with_suffix(".readiness.json")
    readiness_path.parent.mkdir(parents=True, exist_ok=True)
    readiness = generation_readiness(request["input_data"])
    readiness_path.write_text(json.dumps(readiness, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    print(f"Prepared {args.output}. No network calls or generation performed.")
    print(f"Numerical input ready: {readiness['numerical_input_ready']}; see {readiness_path}")


if __name__ == "__main__":
    main()
