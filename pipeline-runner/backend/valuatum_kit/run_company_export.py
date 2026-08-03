#!/usr/bin/env python3
"""Run the full Valuatum JSON export flow for one company.

One step: fetch valuation modeldata by fid and map it to the structured JSON
schema. The export covers every income-statement, balance-sheet and credit-risk
row; payment defaults come from GET /rest/creditrisk.

This script uses only the Python standard library.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent
FETCH_SCRIPT = ROOT / "fetch_modeldata.py"
EXPORT_SCRIPT = ROOT / "export_modeldata_json.py"


def slugify(value: str) -> str:
    cleaned = "".join(ch.lower() if ch.isalnum() else "_" for ch in value)
    cleaned = "_".join(part for part in cleaned.split("_") if part)
    return cleaned or "company"


def run(cmd: list[str]) -> None:
    print("+ " + " ".join(cmd))
    subprocess.run(cmd, check=True)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--fid", required=True, type=int)
    parser.add_argument("--company-name", required=True)
    parser.add_argument("--company-code", required=True, help="Finnish business id without hyphen, e.g. 24388345")
    parser.add_argument("--actuals", default=5, type=int)
    parser.add_argument("--estimates", default=10, type=int)
    parser.add_argument("--out-dir", default="../exports", type=Path)
    parser.add_argument("--file-prefix", default=None)
    args = parser.parse_args()

    if not os.environ.get("VALUATUM_TOKEN"):
        raise SystemExit("Set VALUATUM_TOKEN before running.")

    out_dir = args.out_dir.expanduser().resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    prefix = args.file_prefix or f"{slugify(args.company_name)}_{args.fid}"
    final_json = out_dir / f"{prefix}_modeldata_complete.json"

    export_cmd = [
        sys.executable,
        str(EXPORT_SCRIPT),
        "--fetch-script",
        str(FETCH_SCRIPT),
        "--fid",
        str(args.fid),
        "--actuals",
        str(args.actuals),
        "--estimates",
        str(args.estimates),
        "--company-name-override",
        args.company_name,
        "--company-code-override",
        args.company_code,
        "--output",
        str(final_json),
    ]
    run(export_cmd)

    print(f"Wrote {final_json}")


if __name__ == "__main__":
    main()
