"""Deterministic scenario-comparison table.

Scenario figures already exist as structured data — `_scenarios.scenarios`
(6-stage pipeline sidecar) or `machine_readable.scenarios` (single-writer
reports have no stage-4 sidecar, per the cover's own fallback logic in
render.py's _scenario_values). Section 11's own prose/tables describe each
scenario one at a time; this reads the same structured numbers and lays them
side by side so a reader can compare without flipping between paragraphs.
"""

_ORDER = ["pessimistinen", "konservatiivinen", "optimistinen"]
_LABELS = {"pessimistinen": "Pessimistinen", "konservatiivinen": "Konservatiivinen", "optimistinen": "Optimistinen"}


def _is_num(x):
    return isinstance(x, (int, float)) and not isinstance(x, bool)


def _fmt_num(v):
    if not _is_num(v):
        return "–"
    sign = "-" if v < 0 else ""
    return f"{sign}{round(abs(v)):,.0f}".replace(",", " ")


def _classify(name):
    n = str(name or "").strip().lower()
    for key in _ORDER:
        if key in n:
            return key
    return None


def _scenarios_from_report(report):
    scen = ((report.get("_scenarios") or {}).get("scenarios"))
    if not isinstance(scen, list):
        scen = (report.get("machine_readable") or {}).get("scenarios")
    return scen if isinstance(scen, list) else []


def build_scenario_comparison_block(report):
    """report: the assembled wrapper dict. Returns a single `table` block, or []
    if fewer than 2 named scenarios are present."""
    by_name = {}
    for s in _scenarios_from_report(report):
        if not isinstance(s, dict):
            continue
        key = _classify(s.get("name"))
        if key and key not in by_name:
            by_name[key] = s

    present = [k for k in _ORDER if k in by_name]
    if len(present) < 2:
        return []

    columns = ["Tunnusluku"] + [_LABELS[k] for k in present]
    rows = [
        ["Arvo (tEUR)"] + [_fmt_num(by_name[k].get("value_teur", by_name[k].get("owner_value_teur")))
                           for k in present],
        ["Todennäköisyys (%)"] + [_fmt_num(by_name[k].get("probability_pct")) for k in present],
    ]

    return [{
        "type": "table",
        "table_id": "deterministic_scenario_comparison",
        "title": "Skenaariovertailu",
        "columns": columns,
        "rows": rows,
    }]
