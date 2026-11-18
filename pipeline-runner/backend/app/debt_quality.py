"""Identify unexplained debt omitted from a valuation-engine bridge."""

import math


def _at(values, index):
    return values[index] if isinstance(values, list) and 0 <= index < len(values) else None


def blockers(data):
    actuals = (data or {}).get("actuals") or {}
    balance = actuals.get("balance_sheet") or {}
    index = len(actuals.get("years") or []) - 1
    total = _at(balance.get("interest_bearing_debt_source_total"), index)
    loans = _at(balance.get("capital_loans"), index)
    bridge = ((((data or {}).get("valuation_engine") or {}).get("dcf") or {}).get("bridge") or {})
    debt = bridge.get("interest_bearing_debt")
    def valid(v):
        return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)

    if not (valid(total) and valid(debt)):
        return []
    if total < 0 or debt > 0 or (valid(loans) and loans < 0):
        return ["korollisen velan etumerkki ei vastaa lähteen tai arvonmäärityssillan esitystapaa"]
    tolerance = max(1.0, 0.001 * abs(total))
    if abs(total - abs(debt)) <= tolerance:
        return []  # The bridge already includes the full source debt total.
    # The engine states bridge debt as a signed deduction; the source total and
    # capital loans are positive. Do not assume the unexplained balance is a
    # debt adjustment until the underlying instruments have been reviewed.
    if not valid(loans):
        return ["lähteen ja arvonmäärityssillan korolliset velat poikkeavat, eikä pääomalainojen määrä ole tiedossa"]
    difference = total - abs(debt) - loans
    if abs(difference) <= tolerance:
        return []
    return [f"lähteen korolliset velat {total:.1f}, arvonmäärityssillan velat {abs(debt):.1f} "
            f"ja pääomalainat {loans:.1f} jättävät {difference:.1f} tEUR täsmäyttämättä"]


def require_review(data):
    problems = blockers(data)
    if problems:
        raise ValueError("Arvonmäärityksen velat vaativat tarkistuksen: " + "; ".join(problems)
                         + ". Selvitä velkojen ehdot ja käsittely ennen raportin tekemistä.")
