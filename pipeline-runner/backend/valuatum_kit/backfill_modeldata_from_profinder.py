#!/usr/bin/env python3
"""Backfill modeldata JSON actual fields from Profinder MCP statements."""

from __future__ import annotations

import argparse
import json
import urllib.request
from pathlib import Path
from typing import Any

try:
    from .config import mcp_url
except ImportError:  # Direct script execution.
    from config import mcp_url


def post_json(url: str, payload: dict[str, Any]) -> dict[str, Any]:
    request = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        method="POST",
        headers={
            "accept": "application/json, text/event-stream",
            "content-type": "application/json",
        },
    )
    with urllib.request.urlopen(request, timeout=60) as response:
        return json.loads(response.read().decode("utf-8"))


def initialize(url: str) -> None:
    post_json(
        url,
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2025-03-26",
                "capabilities": {},
                "clientInfo": {"name": "modeldata-backfill", "version": "1.0.0"},
            },
        },
    )


def call_tool(url: str, tool: str, company_code: str, limit: int) -> dict[str, Any]:
    response = post_json(
        url,
        {
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/call",
            "params": {
                "name": tool,
                "arguments": {
                    "companyCode": company_code,
                    "period_type": "annual",
                    "limit": limit,
                },
            },
        },
    )
    result = response.get("result", {})
    if result.get("isError"):
        content = result.get("content") or []
        detail = content[0].get("text") if content else response
        raise RuntimeError(f"{tool} failed: {detail}")
    content = result.get("content") or []
    text = result.get("structuredContent", {}).get("result") or (content[0].get("text") if content else None)
    if not text:
        return {}
    return json.loads(text)


def rows_to_map(payload: dict[str, list[dict[str, Any]]]) -> dict[int, dict[str, Any]]:
    out: dict[int, dict[str, Any]] = {}
    for year, rows in payload.items():
        mapped: dict[str, Any] = {}
        for row in rows:
            variable = row.get("variable")
            if variable:
                mapped[str(variable).strip()] = row.get("value")
        out[int(year)] = mapped
    return out


def as_num(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def clean(value: float | None) -> float | int | None:
    if value is None:
        return None
    rounded = round(value, 6)
    if rounded.is_integer():
        return int(rounded)
    return rounded


def one(data: dict[int, dict[str, Any]], year: int, names: str | list[str], *, money: bool = False) -> Any:
    wanted = [names] if isinstance(names, str) else names
    for name in wanted:
        value = as_num(data.get(year, {}).get(name))
        if value is not None:
            if money:
                value *= 1000
            return clean(value)
    return None


def sum_values(data: dict[int, dict[str, Any]], year: int, names: list[str], *, money: bool = False) -> Any:
    values = [as_num(data.get(year, {}).get(name)) for name in names]
    values = [value for value in values if value is not None]
    if not values:
        return None
    total = sum(values)
    if money:
        total *= 1000
    return clean(total)


def arr(data: dict[int, dict[str, Any]], years: list[int], names: str | list[str], *, money: bool = False) -> list[Any]:
    return [one(data, year, names, money=money) for year in years]


def arr_sum(data: dict[int, dict[str, Any]], years: list[int], names: list[str], *, money: bool = False) -> list[Any]:
    return [sum_values(data, year, names, money=money) for year in years]


def overwrite_if_any(target: dict[str, Any], key: str, values: list[Any]) -> None:
    if any(value is not None for value in values):
        target[key] = values


def count_nulls(value: Any) -> int:
    if value is None:
        return 1
    if isinstance(value, list):
        return sum(count_nulls(item) for item in value)
    if isinstance(value, dict):
        return sum(count_nulls(item) for item in value.values())
    return 0


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--company-code", required=True)
    parser.add_argument("--limit", type=int, default=10)
    args = parser.parse_args()

    url = mcp_url()
    if not url:
        raise SystemExit("Set VALUATUM_MCP_URL.")

    payload = json.loads(args.input.read_text(encoding="utf-8"))
    years = [int(year) for year in payload["actuals"]["years"]]

    initialize(url)
    income = rows_to_map(call_tool(url, "valu_income_statement", args.company_code, args.limit))
    balance = rows_to_map(call_tool(url, "valu_balance_sheet", args.company_code, args.limit))

    is_target = payload["actuals"]["income_statement"]
    overwrite_if_any(is_target, "other_operating_income", arr(income, years, "Other operating income", money=True))
    overwrite_if_any(is_target, "gross_profit", arr(income, years, "Gross profit", money=True))
    overwrite_if_any(
        is_target,
        "personnel_costs",
        arr_sum(income, years, ["Wages and salaries", "Social security expenses"], money=True),
    )
    overwrite_if_any(is_target, "other_operating_costs", arr(income, years, "Other operating expenses", money=True))
    overwrite_if_any(is_target, "depreciation_total", arr(income, years, "Total depreciation and amortization", money=True))
    overwrite_if_any(is_target, "interest_expenses", arr(income, years, "Interest expenses", money=True))
    overwrite_if_any(is_target, "net_earnings", arr(income, years, "Net earnings", money=True))

    bs_target = payload["actuals"]["balance_sheet"]
    overwrite_if_any(bs_target, "development_costs", arr(balance, years, "Development expenses", money=True))
    overwrite_if_any(
        bs_target,
        "intangibles_total",
        arr(balance, years, ["Intangible assets total", "Intangible assets, total"], money=True),
    )
    overwrite_if_any(bs_target, "tangible_assets", arr(balance, years, "Tangible assets total", money=True))
    overwrite_if_any(bs_target, "inventories", arr(balance, years, ["Inventories total", "Inventories"], money=True))
    overwrite_if_any(bs_target, "trade_receivables", arr(balance, years, "Current accounts receivable", money=True))
    overwrite_if_any(bs_target, "cash_and_equivalents", arr(balance, years, "Cash and bank deposits", money=True))
    overwrite_if_any(
        bs_target,
        "equity_incl_capital_loans",
        arr(balance, years, "Shareholder's equity (incl. Cap loans)", money=True),
    )
    overwrite_if_any(
        bs_target,
        "capital_loans",
        arr(balance, years, ["Capital loans", "Capital loans total", "Current capital loans", "Non-current capital loans"], money=True),
    )
    overwrite_if_any(
        bs_target,
        "loans_from_fin_institutions",
        arr_sum(
            balance,
            years,
            [
                "Non-current loans from credit institutions (Estimate years generated)",
                "Current loans from credit institutions (Estimate years generated)",
            ],
            money=True,
        ),
    )
    overwrite_if_any(bs_target, "advances_received", arr(balance, years, "Current advances received", money=True))
    overwrite_if_any(bs_target, "trade_payables", arr(balance, years, "Current accounts payable", money=True))
    overwrite_if_any(bs_target, "non_interest_bearing_debt", arr(balance, years, "Non-interest bearing liabilities", money=True))

    payload.setdefault("flags", []).append(
        {
            "field": "actuals",
            "issue": "Actual income statement and balance sheet details backfilled from Profinder MCP statement tools",
            "severity": "low",
        }
    )

    args.output.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps({"output": str(args.output), "nulls": count_nulls(payload)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
