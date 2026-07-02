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


def _blocks(output):
    out = []
    for sec in output.get("sections") or []:
        if isinstance(sec, dict):
            out.extend(b for b in (sec.get("blocks") or []) if isinstance(b, dict))
    return out


def _table_row(table, label_keywords):
    """First row in a {columns, rows} table whose first cell matches one of
    the given (lowercase) keywords. Row may be an array (contracted shape) or
    a dict (tolerated, same as the renderer's coercion)."""
    if not isinstance(table, dict):
        return None
    for row in table.get("rows") or []:
        if isinstance(row, list):
            cells = row
        elif isinstance(row, dict):
            cells = list(row.values())
        else:
            continue
        if not cells:
            continue
        label = str(cells[0]).strip().lower()
        if any(kw in label for kw in label_keywords):
            return cells
    return None


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

    # --- 7. realistic base case anchors to stage-3 weighted base case --------
    # The cover/section-11 anchor (stage 4) must not contradict the weighted base
    # case computed in stage 3 (section 8) of the same report.
    scoring = (context or {}).get("scoring", {}) or {}
    wbc = _num(scoring.get("weighted_base_case_teur")) if isinstance(scoring, dict) else None
    if rbc is not None and wbc is not None:
        # The realistic base case is floored at 0 (equity value can't go below
        # zero), so compare against the FLOORED weighted base case — a negative
        # weighted value of -4070 legitimately floors to a realistic 0.
        floored = max(0.0, wbc)
        chk("realistic_base_case_teur == floored stage-3 weighted base case (±1 tEUR)",
            abs(rbc - floored) <= 1.0,
            f"stage4 {rbc} vs floored weighted {floored} (raw {wbc})")
    else:
        chk("realistic_base_case anchors to stage-3 weighted base case", True,
            "skipped: scoring.weighted_base_case_teur not available")

    # --- 8. scenario oma pääoma / omavaraisuusaste sign consistency ----------
    # Reported bug: a scenario's perusluvut showed positive equity while its
    # avainluvut showed a negative equity ratio in the same column — that
    # combination is impossible for a normal (positive-assets) balance sheet.
    sign_viol = []
    for b in _blocks(output):
        if b.get("type") != "scenario_table":
            continue
        name = b.get("scenario", "?")
        equity_row = _table_row(b.get("perusluvut"), ("oma pääoma",))
        ratio_row = (_table_row(b.get("avainluvut"), ("omavaraisuusaste",))
                     or _table_row(b.get("perusluvut"), ("omavaraisuusaste",)))
        if not equity_row or not ratio_row:
            continue
        for i in range(1, min(len(equity_row), len(ratio_row))):  # cell 0 = row label
            eq, ratio = _num(equity_row[i]), _num(ratio_row[i])
            if eq is not None and ratio is not None and eq > 0 and ratio < 0:
                sign_viol.append(
                    f"{name} sarake {i}: oma pääoma {eq} > 0 mutta omavaraisuusaste {ratio} % < 0")
    chk("skenaarion oma pääoma > 0 ei esiinny negatiivisen omavaraisuusasteen kanssa samassa sarakkeessa",
        not sign_viol,
        "; ".join(sign_viol) if sign_viol else "ok (tai kumpaakin taulukkoa ei löytynyt)")

    # --- 9. zero-value scenario must not contradict its own fundamentals -----
    # Reported bug (Supercell): the pessimistic scenario was assigned 0 tEUR
    # equity at 50% probability while its OWN perusluvut showed EBIT ~240 000
    # tEUR positive and flat in every column, and the company held ~638 000
    # tEUR net cash — a perpetually profitable scenario cannot be worth zero.
    # Rough conservative perpetuity: EBIT * (1-tax) / WACC + cash - debt. If
    # that proxy is clearly positive while the scenario claims ~0, fail the
    # stage so the model either values the stated fundamentals or lowers them.
    ve = ((context or {}).get("input_data") or {}).get("valuation_engine") or {}
    wacc = _num((ve.get("wacc_parameters") or {}).get("wacc_pct"))
    wacc = (wacc / 100.0) if wacc and wacc > 1.0 else (wacc or 0.10)
    wacc = max(wacc, 0.08)
    bridge_cash = _num(((ve.get("dcf") or {}).get("bridge") or {}).get("cash")) or 0.0

    zero_viol = []
    by_name = {str(s.get("name", "")).lower(): s for s in scen if isinstance(s, dict)}
    for b in _blocks(output):
        if b.get("type") != "scenario_table":
            continue
        name = str(b.get("scenario", "?"))
        s = by_name.get(name.lower()) or {}
        v = _value(s) if s else _num(b.get("value_teur"))
        if v is None or v > 1.0:
            continue  # only scenarios claiming ~zero equity
        ebit_row = _table_row(b.get("perusluvut"), ("liikevoitto", "liiketulos", "ebit"))
        if not ebit_row:
            continue
        ebits = [_num(c) for c in ebit_row[1:]]
        ebits = [e for e in ebits if e is not None]
        if not ebits or any(e <= 0 for e in ebits):
            continue  # declining/loss-making scenario — zero is coherent
        debt_row = _table_row(b.get("perusluvut"), ("korolliset velat", "korollinen velka"))
        debt = max((_num(c) or 0.0) for c in debt_row[1:]) if debt_row and len(debt_row) > 1 else 0.0
        proxy = ebits[-1] * 0.8 / wacc + bridge_cash - debt
        if proxy > 0.05 * max(abs(rv or 0.0), 1.0):
            zero_viol.append(
                f"{name}: arvo {v} tEUR mutta oman taulukon EBIT pysyy positiivisena "
                f"({round(ebits[-1])} tEUR) — karkea perpetuiteetti EBIT×0,8/WACC + kassa "
                f"− velat ≈ {round(proxy)} tEUR > 0. Nolla-arvo on ristiriidassa skenaarion "
                f"omien lukujen kanssa: joko laske skenaarion fundamenteille arvo tai "
                f"muuta fundamentit vastaamaan nolla-arvoa")
    chk("nolla-arvoinen skenaario ei saa näyttää pysyvästi positiivista EBITiä omassa taulukossaan",
        not zero_viol, "; ".join(zero_viol) if zero_viol else "ok")

    # --- 10. scenario tables must carry row labels ----------------------------
    # Reported bug (Supercell p17-19): perusluvut/avainluvut rendered as bare
    # number grids — the reader could not tell which row was revenue vs equity.
    _yearish = re.compile(r"^(19|20)\d{2}\s*E?$", re.I)

    def _numeric_cell(c):
        return not isinstance(c, bool) and (
            isinstance(c, (int, float)) or (isinstance(c, str) and _num(c) is not None))

    unlabeled = []
    for b in _blocks(output):
        if b.get("type") != "scenario_table":
            continue
        name = b.get("scenario", "?")
        for part in ("perusluvut", "avainluvut"):
            t = b.get(part)
            if not isinstance(t, dict):
                continue
            cols = t.get("columns")
            if not isinstance(cols, list) or sum(
                    1 for c in cols if _yearish.match(str(c).strip())) < 2:
                continue
            rows = [r for r in (t.get("rows") or []) if isinstance(r, list) and r]
            if rows and all(_numeric_cell(r[0]) for r in rows):
                unlabeled.append(
                    f"{name}.{part}: rivit ovat pelkkiä numeroita — lisää jokaisen "
                    f"rivin alkuun rivin nimi (esim. 'Liikevaihto')")
    chk("skenaariotaulukoiden riveillä on nimet (ensimmäinen solu ei ole numero)",
        not unlabeled, "; ".join(unlabeled[:6]) if unlabeled else "ok")

    return {"passed": all(c["passed"] for c in checks), "checks": checks}
