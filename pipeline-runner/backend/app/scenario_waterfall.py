"""Deterministic optimistic-scenario equity waterfall.

The LLM used to compute the optimistic owner value in prose with a broken
bridge: it discounted the continuing value to today, then subtracted the
UNDISCOUNTED realization-year net debt AND the interim funding deficits that
debt already finances (Virnex: 6 458 − 3 023 − 621 = 2 814; SaaShop admitted
its own double counting). This module replaces that arithmetic with one
time-consistent model computed in code:

    equity_n = continuing_value_n − net_debt_n          (both at year n)
    value    = equity_n / (1 + wacc)^n                  (one discounting)
    value   *= (1 − dilution_pct/100)                   (optional, separate)
    value    = max(value, 0)                            (owner-value floor)

The writer supplies the assumptions (machine_readable.optimistic_assumptions,
each one a visible, user-editable input); the value itself is never taken from
the model. Interim deficits are NOT subtracted separately — the realization-year
net debt already embodies how they were financed (single representation).
"""


def _num(x):
    if isinstance(x, bool):
        return None
    if isinstance(x, (int, float)):
        return float(x)
    return None


def _fmt(v, decimals=0):
    if v is None:
        return ""
    sign = "-" if v < 0 else ""
    a = abs(v)
    if decimals:
        s = f"{a:,.{decimals}f}"
        i, f = s.split(".")
        return f"{sign}{i.replace(',', ' ')},{f}"
    return f"{sign}{round(a):,.0f}".replace(",", " ")


def _wacc_pct(input_data):
    ve = (input_data or {}).get("valuation_engine") or {}
    return _num((ve.get("wacc_parameters") or {}).get("wacc_pct"))


def _base_year(input_data):
    years = ((input_data or {}).get("actuals") or {}).get("years") or []
    nums = [int(y) for y in years if isinstance(y, (int, float))]
    if nums:
        return max(nums)
    fyears = ((input_data or {}).get("forecast") or {}).get("years") or []
    nums = [int(y) for y in fyears if isinstance(y, (int, float))]
    return nums[0] - 1 if nums else None


def compute(assumptions, input_data):
    """Return {value_teur, steps:[...]} or None when the inputs don't support a
    deterministic calculation (missing/implausible fields -> caller keeps the
    writer's value and QA flags it)."""
    if not isinstance(assumptions, dict):
        return None
    cv = _num(assumptions.get("continuing_value_teur"))
    nd = _num(assumptions.get("net_debt_realization_year_teur"))
    year = _num(assumptions.get("realization_year"))
    dilution = _num(assumptions.get("dilution_pct")) or 0.0
    wacc = _num(assumptions.get("wacc_pct")) or _wacc_pct(input_data)
    base = _base_year(input_data)
    if None in (cv, nd, year, wacc, base):
        return None
    n = int(year) - int(base)
    # guardrails: reject implausible inputs rather than compute garbage
    if not (cv > 0 and 0 < n <= 15 and 1.0 <= wacc <= 40.0 and 0 <= dilution < 100):
        return None
    factor = (1 + wacc / 100.0) ** n
    equity_n = cv - nd
    pv = equity_n / factor
    value = pv * (1 - dilution / 100.0)
    floored = max(value, 0.0)
    steps = [
        ["Jatkuvan arvon arvo toteutumisvuonna " + str(int(year)), _fmt(cv)],
        ["− Ennustettu nettovelka vuonna " + str(int(year)), _fmt(nd)],
        ["= Oman pääoman arvo vuonna " + str(int(year)), _fmt(equity_n)],
        [f"Diskonttaus nykyhetkeen (WACC {_fmt(wacc, 1)} %, {n} v, kerroin {_fmt(factor, 3)})",
         _fmt(pv)],
    ]
    if dilution:
        steps.append([f"− Laimennusvaikutus {_fmt(dilution, 1)} %", _fmt(value)])
    if floored != value:
        steps.append(["Omistaja-arvon lattia", "0"])
    steps.append(["Optimistinen omistaja-arvo", _fmt(floored)])
    return {"value_teur": floored, "steps": steps, "realization_year": int(year),
            "wacc_pct": wacc, "n": n}


_VALUE_ALIASES = ("value_teur", "owner_value_teur", "owner_value",
                  "equity_value", "equity_value_teur", "value")


def _find_scenario(scenarios, needle):
    for s in scenarios:
        if isinstance(s, dict) and needle in str(s.get("name", "")).lower():
            return s
    return None


def apply(wrapper, input_data):
    """Override the optimistic scenario value + expected value + cover headline
    with the deterministic waterfall when the writer supplied usable
    assumptions. No assumptions -> report unchanged (old runs keep working)."""
    mr = wrapper.get("machine_readable") or {}
    scenarios = mr.get("scenarios")
    if not isinstance(scenarios, list):
        return None
    result = compute(mr.get("optimistic_assumptions"), input_data)
    if result is None:
        return None
    opt = _find_scenario(scenarios, "optimis")
    if opt is None:
        return None

    for k in _VALUE_ALIASES:
        opt.pop(k, None)
    opt["value_teur"] = round(result["value_teur"], 1)

    # recompute contributions + expected value with the deterministic figure
    probs, vals = [], []
    for s in scenarios:
        p = _num(s.get("probability_pct"))
        v = next((_num(s.get(k)) for k in _VALUE_ALIASES
                  if _num(s.get(k)) is not None), None)
        if p is None or v is None:
            return result  # partial data: scenario value fixed, EV left alone
        probs.append(p)
        vals.append(v)
        if "contribution" in s:
            s["contribution"] = round(p * v / 100.0, 1)
    if abs(sum(probs) - 100.0) <= 1.0:
        ev = sum(p * v for p, v in zip(probs, vals)) / 100.0
        calc = " + ".join(f"{_fmt(p)} % × {_fmt(v)} tEUR"
                          for p, v in zip(probs, vals)) + f" = {_fmt(ev)} tEUR"
        evf = wrapper.get("expected_value")
        if isinstance(evf, dict):
            evf["value"] = round(ev, 1)
            evf["calculation"] = calc
        else:
            wrapper["expected_value"] = {"value": round(ev, 1), "unit": "tEUR",
                                         "calculation": calc}
        cover = wrapper.get("cover")
        if isinstance(cover, dict) and cover.get("headline_value") not in (None, ""):
            cover["headline_value"] = f"{_fmt(ev)} tEUR"
    return result


def derivation_blocks(result):
    return [{
        "type": "table",
        "table_id": "deterministic_optimistic_waterfall",
        "title": "Optimistisen skenaarion arvon johto (deterministinen)",
        "unit": "tEUR",
        "columns": ["Erä", "Arvo"],
        "rows": result["steps"],
    }, {
        "type": "paragraph",
        "text": (
            "Optimistisen skenaarion arvo on laskettu deterministisesti yllä "
            "olevista oletuksista: oman pääoman arvo muodostetaan "
            "toteutumisvuonna (jatkuva arvo miinus saman vuoden ennustettu "
            "nettovelka) ja diskontataan kokonaisuutena nykyhetkeen. "
            "Välivuosien rahoitustarvetta ei vähennetä erikseen, koska "
            "toteutumisvuoden nettovelka sisältää jo sen rahoituksen. "
            "Oletukset ovat AI:n muodostamia ja käyttäjän muokattavissa."
        ),
    }]


if __name__ == "__main__":
    # Virnex-shaped self-check: CV 10 148, net debt 3 023, 2025 -> 2030 @ 9.47 %
    data = {"actuals": {"years": [2024, 2025]},
            "valuation_engine": {"wacc_parameters": {"wacc_pct": 9.47}}}
    r = compute({"continuing_value_teur": 10148.0,
                 "net_debt_realization_year_teur": 3023.0,
                 "realization_year": 2030}, data)
    assert r is not None and abs(r["value_teur"] - (10148 - 3023) / 1.0947 ** 5) < 0.1, r
    assert compute({"continuing_value_teur": -1, "net_debt_realization_year_teur": 0,
                    "realization_year": 2030}, data) is None  # implausible -> skip
    print("scenario_waterfall self-check ok:", round(r["value_teur"], 1), "tEUR")
