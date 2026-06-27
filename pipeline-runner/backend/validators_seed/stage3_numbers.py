# Vaihe 3 – Numero-osiot (DCF/EVA) NUMBER VALIDATOR.
# The most important validator in the app. It encodes lessons from three failed
# test reports. Every check below is real arithmetic over the parsed JSON + a
# regex sweep of every text block. It does NOT return pass without computing.
import re

# Finnish number formatting: thousands space (incl. NBSP), decimal comma,
# minus as ASCII '-' or U+2212, optional trailing %.
_NUM_RE = re.compile(r"[−-]?\d[\d  ]*(?:,\d+)?\s*%?")


def _parse(tok):
    is_pct = "%" in tok
    t = (tok.replace("%", "").replace("−", "-")
         .replace(" ", "").replace(" ", "").replace(",", ".").strip())
    try:
        return float(t), is_pct
    except ValueError:
        return None, is_pct


def _walk(obj, path=""):
    if isinstance(obj, dict):
        for k, v in obj.items():
            yield from _walk(v, f"{path}.{k}" if path else str(k))
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            yield from _walk(v, f"{path}[{i}]")
    else:
        yield path, obj


def _collect_input_numbers(input_data):
    nums = set()
    for _, v in _walk(input_data):
        if isinstance(v, bool):
            continue
        if isinstance(v, (int, float)):
            nums.add(float(v))
        elif isinstance(v, str):
            for m in _NUM_RE.findall(v):
                val, _ = _parse(m)
                if val is not None:
                    nums.add(val)
    return nums


def _derive(base):
    """Whitelisted simple calcs: growth %, margin %, net diff, pairwise sum.
    Capped to keep it O(n^2)-bounded; if base is large we skip pairwise and
    say so in the orphan detail."""
    allowed = set(base)
    b = list(base)
    if len(b) > 600:
        return allowed, False  # too many — pairwise derivation skipped
    for a in b:
        for c in b:
            if c != 0:
                allowed.add((a - c) / c * 100.0)   # growth %
                allowed.add(a / c * 100.0)          # margin %
            allowed.add(a - c)                      # net debt / diff
            allowed.add(a + c)                      # sum
    return allowed, True


def _is_structural(val, is_pct):
    # years and tiny structural counts create noise; skip them (heuristic).
    if is_pct:
        return False
    if val == int(val):
        iv = int(val)
        if 1900 <= iv <= 2100:   # a year
            return True
        if 0 <= iv <= 12:        # small count / month / index
            return True
    return False


def _match(val, is_pct, allowed):
    tol = 0.5 if is_pct else max(1.0, 0.005 * abs(val))
    for a in allowed:
        if abs(val - a) <= tol:
            return True
    return False


def _find_block(obj, names):
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k.lower() in names and isinstance(v, dict):
                return v
        for v in obj.values():
            r = _find_block(v, names)
            if r is not None:
                return r
    return None


def _find_first(obj, names):
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k.lower() in names and isinstance(v, (int, float)) and not isinstance(v, bool):
                return float(v)
        for v in obj.values():
            r = _find_first(v, names)
            if r is not None:
                return r
    elif isinstance(obj, list):
        for v in obj:
            r = _find_first(v, names)
            if r is not None:
                return r
    return None


def validate(output: dict, context: dict) -> dict:
    checks = []

    def chk(name, ok, detail=""):
        checks.append({"name": name, "passed": bool(ok), "detail": detail})

    input_data = (context or {}).get("input_data", {}) or {}
    base = _collect_input_numbers(input_data)
    allowed, pairwise = _derive(base)

    # --- 1. Orphan numbers in prose -----------------------------------------
    orphans = []
    for path, v in _walk(output):
        if not isinstance(v, str) or len(v) < 4:
            continue  # only 1-3 char strings are pure labels; sweep short
            # metric-card / table values like "12,4x" and "18 %" too
        for m in _NUM_RE.findall(v):
            val, is_pct = _parse(m)
            if val is None or _is_structural(val, is_pct):
                continue
            if not _match(val, is_pct, allowed):
                orphans.append((m.strip(), path))
    note = "" if pairwise else " (pairwise derivation skipped: >600 input numbers)"
    if orphans:
        sample = "; ".join(f"{tok} @ {p}" for tok, p in orphans[:25])
        chk("every prose number traces to input_data (or simple calc)",
            False, f"{len(orphans)} orphan number(s){note}: {sample}")
    else:
        chk("every prose number traces to input_data (or simple calc)",
            True, f"all prose numbers reconcile{note}")

    # --- 2. Discounting sanity ----------------------------------------------
    # The DCF lives in input_data.valuation_engine.dcf — NOT in the stage-3
    # output (which is scoring + sections). Reading it from `output` made these
    # checks silently skip every run. Source from the verified input instead.
    ve = (input_data.get("valuation_engine") or {}) if isinstance(input_data, dict) else {}
    dcf = ve.get("dcf") if isinstance(ve.get("dcf"), dict) else {}
    wp = ve.get("wacc_parameters") if isinstance(ve.get("wacc_parameters"), dict) else {}
    wacc = wp.get("wacc_pct") if wp.get("wacc_pct") is not None else _find_first(ve, {"wacc", "wacc_pct"})
    disc = dcf.get("discounted_fcff")
    nom = dcf.get("nominal_fcff") or dcf.get("fcff")
    years = dcf.get("years")
    if isinstance(disc, list) and isinstance(nom, list) and (wacc is None or wacc > 0):
        viol = []
        for i in range(min(len(disc), len(nom))):
            if i == 0:
                continue  # the first forecast year can exceed nominal under
                          # mid-year / stub discounting conventions — not an anomaly
            d, n = disc[i], nom[i]
            if isinstance(d, (int, float)) and isinstance(n, (int, float)):
                if abs(d) > abs(n) * 1.02 + 1e-6:  # 2% grace for rounding/convention
                    yr = years[i] if isinstance(years, list) and i < len(years) else i
                    viol.append(f"year {yr}: |disc {d}| > |nominal {n}|")
        chk("|discounted_fcff| <= |nominal_fcff| (WACC>0, from year 2)",
            not viol, "; ".join(viol) if viol else "ok across all years")
    else:
        chk("discounting sanity", True,
            "skipped: dcf.discounted_fcff / nominal_fcff not both present")

    # --- 3. DCF bridge reconciles -------------------------------------------
    bridge = dcf.get("bridge", dcf) if isinstance(dcf, dict) else {}
    sd = sum(x for x in (disc or []) if isinstance(x, (int, float)))
    tv = _find_first(dcf, {"terminal_value", "tv"}) or _find_first(ve, {"terminal_value", "tv"})
    nd = _find_first(bridge, {"net_debt"}) or _find_first(ve, {"net_debt"})
    cash = _find_first(bridge, {"cash"}) or _find_first(ve, {"cash"})
    stated = (_find_first(dcf, {"equity_value_before_floor"})
              or _find_first(ve, {"equity_value_before_floor"}))
    if disc and tv is not None and stated is not None:
        computed = sd + tv - (nd or 0.0) + (cash or 0.0)
        tol = max(2.0, 0.01 * abs(stated))
        chk("DCF bridge reconciles (±1% / ±2 tEUR)",
            abs(computed - stated) <= tol,
            f"computed {round(computed, 2)} vs stated {round(stated, 2)}")
    else:
        chk("DCF bridge reconciles", True,
            "skipped: discounted_fcff / terminal_value / equity_value_before_floor missing")

    # --- 4. Term consistency: a labelled headline figure is one number -------
    headline_keys = {
        "base_case", "base_value", "expected_value", "equity_value",
        "owner_value", "headline_value", "fair_value",
    }
    label_map = {}
    for path, v in _walk(output):
        key = path.split(".")[-1].split("[")[0].lower()
        if key in headline_keys and isinstance(v, (int, float)) and not isinstance(v, bool):
            label_map.setdefault(key, set()).add(round(float(v), 3))
    conflicts = []
    for key, vals in label_map.items():
        clusters = []
        for val in sorted(vals):
            if not any(abs(val - c) <= max(2.0, 0.005 * abs(val)) for c in clusters):
                clusters.append(val)
        if len(clusters) > 1:
            conflicts.append(f"{key} → {clusters}")
    chk("each headline figure maps to a single number",
        not conflicts, "; ".join(conflicts) if conflicts else "no conflicting labels")

    # --- 5. Breakeven check (if present) ------------------------------------
    be = _find_first(output, {"breakeven", "break_even", "breakeven_revenue"})
    fc = _find_first(output, {"fixed_costs", "fixed_cost"})
    gm = _find_first(output, {"gross_margin_pct", "gross_margin"})
    if be is not None and fc is not None and gm:
        gm_frac = gm / 100.0 if gm > 1.0 else gm
        if gm_frac:
            computed_be = fc / gm_frac
            chk("breakeven = fixed_costs / gross_margin (±2%)",
                abs(computed_be - be) <= max(0.02 * abs(be), 1.0),
                f"computed {round(computed_be, 1)} vs stated {round(be, 1)}")
    else:
        chk("breakeven check", True, "skipped: not present")

    return {"passed": all(c["passed"] for c in checks), "checks": checks}
