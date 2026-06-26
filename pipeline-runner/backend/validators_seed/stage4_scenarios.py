# Vaihe 4 – Skenaariot SCENARIO VALIDATOR.
# Reads the stage-4 output shape: scenarios[] + expected_value_teur +
# realistic_base_case_teur. Real arithmetic, recomputes the expected value.
import re

# Strip every kind of space the model may use as a thousands separator
# (ASCII, NBSP U+00A0, narrow NBSP U+202F, thin space U+2009).
_WS = re.compile(r"[\s   ]")


def _num(x):
    if isinstance(x, bool):
        return None
    if isinstance(x, (int, float)):
        return float(x)
    if isinstance(x, str):
        t = _WS.sub("", x.replace("tEUR", "").replace("%", "")
                    .replace("−", "-")).replace(",", ".")
        try:
            return float(t)
        except ValueError:
            return None
    return None


def _scenarios(output):
    s = output.get("scenarios")
    return s if isinstance(s, list) else []


def _prob(s):
    # *_pct keys are always percentages; bare fraction keys are fractions only
    # when <= 1 (so a 1% probability is not misread as 100%).
    for k in ("probability_pct", "probability", "weight", "p"):
        if k in s:
            v = _num(s[k])
            if v is None:
                continue
            if k.endswith("_pct"):
                return v / 100.0
            return v / 100.0 if v > 1.0 else v
    return None


def _value(s):
    for k in ("value_teur", "owner_value_teur", "value", "equity_value_teur"):
        if k in s:
            v = _num(s[k])
            if v is not None:
                return v
    return None


def validate(output: dict, context: dict) -> dict:
    checks = []

    def chk(name, ok, detail=""):
        checks.append({"name": name, "passed": bool(ok), "detail": detail})

    scen = _scenarios(output)
    chk("scenarios present (3 expected)", len(scen) >= 1,
        f"{len(scen)} scenarios")

    # --- 1. every scenario carries a parseable value_teur --------------------
    missing_val = [s.get("name", "?") for s in scen if isinstance(s, dict)
                   and _value(s) is None]
    chk("every scenario has a parseable value_teur", not missing_val,
        f"missing value: {', '.join(missing_val)}" if missing_val else "all present")

    # --- 2. every scenario value floored at >= 0 -----------------------------
    floor_viol = []
    for s in scen:
        v = _value(s) if isinstance(s, dict) else None
        if v is not None and v < 0:
            floor_viol.append(f"{s.get('name', '?')} = {v}")
    chk("every scenario value_teur >= 0 (floor)", not floor_viol,
        "; ".join(floor_viol) if floor_viol else "all floored at 0")

    # --- 3. probabilities sum to 100% ----------------------------------------
    probs = [p for p in (_prob(s) for s in scen if isinstance(s, dict))
             if p is not None]
    if probs:
        ps = sum(probs)
        chk("probabilities sum to 100%", abs(ps - 1.0) <= 0.005,
            f"sum = {round(ps * 100, 2)}%")
    else:
        chk("probabilities present", False, "no probability_pct found")

    # --- 4. expected_value == Σ(prob × floored value) ±1 tEUR ----------------
    recomputed = 0.0
    for s in scen:
        if not isinstance(s, dict):
            continue
        p, v = _prob(s), _value(s)
        if p is not None and v is not None:
            recomputed += p * max(0.0, v)
    ev = _num(output.get("expected_value_teur"))
    if ev is None and isinstance(output.get("expected_value"), dict):
        ev = _num(output["expected_value"].get("value"))
    if ev is not None:
        chk("expected_value_teur == Σ(prob × value) (±1 tEUR)",
            abs(ev - recomputed) <= 1.0,
            f"stated {round(ev, 2)} vs recomputed {round(recomputed, 2)}")
    else:
        chk("expected_value_teur present", False, "missing expected_value_teur")

    # --- 5. realistic_base_case present and == realistic scenario value ------
    rbc = _num(output.get("realistic_base_case_teur"))
    realistic = next(
        (s for s in scen if isinstance(s, dict)
         and str(s.get("name", "")).lower().startswith("realist")), None)
    rv = _value(realistic) if realistic else None
    if rbc is None:
        chk("realistic_base_case_teur present", False, "missing")
    elif rv is None:
        chk("realistic scenario present", False,
            "no scenario named 'realistinen' to reconcile against")
    else:
        chk("realistic_base_case_teur == realistic scenario value (±1 tEUR)",
            abs(rbc - rv) <= 1.0, f"base case {rbc} vs realistic {rv}")

    # --- 6. every scenario has a non-empty probability_rationale --------------
    missing_rat = [s.get("name", "?") for s in scen if isinstance(s, dict)
                   and not str(s.get("probability_rationale", "")).strip()]
    chk("every scenario has a non-empty probability_rationale", not missing_rat,
        f"missing: {', '.join(missing_rat)}" if missing_rat else "all justified")

    return {"passed": all(c["passed"] for c in checks), "checks": checks}
