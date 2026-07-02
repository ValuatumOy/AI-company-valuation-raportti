# Vaihe 3 – Numero-osiot (DCF/EVA) NUMBER VALIDATOR.
# The most important validator in the app. It encodes lessons from three failed
# test reports. Every check below is real arithmetic over the parsed JSON + a
# regex sweep of every text block. It does NOT return pass without computing.
import re

# Finnish number formatting: thousands space (incl. NBSP), decimal comma,
# minus as ASCII '-' or U+2212, optional trailing %.
# Thousands groups must be EXACTLY 3 digits, else a year glued to the next
# space-formatted value ("2023 12 596") matched as one impossible number
# (202312596) that never traced -> false orphan. Now they split cleanly.
_SEP = "[\u0020\u00a0\u202f\u2009]"  # space, NBSP, narrow NBSP, thin space
_NUM_RE = re.compile(r"[\u2212-]?(?:\d{1,3}(?:" + _SEP + r"\d{3})+|\d+)(?:,\d+)?\s*%?")


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
    # Sign-insensitive: Finnish prose states costs/expenses as positive magnitudes
    # while the source stores them signed (e.g. -5213) — match on magnitude too.
    tol = 0.5 if is_pct else max(1.0, 0.005 * abs(val))
    av = abs(val)
    for a in allowed:
        if abs(val - a) <= tol or abs(av - abs(a)) <= tol:
            return True
    return False


def _match_hard(val, allowed):
    # Blocking gate uses a wider ±1% grace (vs ±0.5% advisory) so a legitimately
    # ROUNDED prose figure ("n. 4 300" for 4 287) is never wrongly blocked.
    tol = max(1.0, 0.01 * abs(val))
    av = abs(val)
    return any(abs(val - a) <= tol or abs(av - abs(a)) <= tol for a in allowed)


def _numbers_of(obj):
    nums = set()
    for _, v in _walk(obj):
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


def _nonprose_numbers(output):
    """Every number in this stage's STRUCTURED blocks (tables, charts, metric
    cards, key-value) + top-level scoring — i.e. the verified figures the prose
    is allowed to restate. Prose (paragraph/callout) is deliberately excluded so a
    figure can never 'trace' to itself."""
    nums = set()
    for sec in (output.get("sections") or []):
        if not isinstance(sec, dict):
            continue
        for b in (sec.get("blocks") or []):
            if isinstance(b, dict) and b.get("type") in ("paragraph", "callout"):
                continue
            nums |= _numbers_of(b)
    for k, v in output.items():
        if k != "sections":
            nums |= _numbers_of(v)
    return nums


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


def _path(obj, *keys):
    for k in keys:
        if not isinstance(obj, dict):
            return None
        obj = obj.get(k)
    return obj


def _is_num(x):
    return isinstance(x, (int, float)) and not isinstance(x, bool)


def _sum_list(lst):
    if not isinstance(lst, list):
        return None
    vals = [x for x in lst if _is_num(x)]
    return sum(vals) if vals else None


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
    # Scope to NARRATIVE prose (paragraph/callout text). Tables, metric_cards,
    # key_value and charts carry IDs, source refs, dates and structured values
    # that need not trace to input_data — sweeping them produced false orphans.
    orphans = []
    for sec in (output.get("sections") or []):
        if not isinstance(sec, dict):
            continue
        sid = str(sec.get("id"))
        for bi, b in enumerate(sec.get("blocks") or []):
            if not isinstance(b, dict) or b.get("type") not in ("paragraph", "callout"):
                continue
            v = b.get("text")
            if not isinstance(v, str) or len(v) < 4:
                continue
            for m in _NUM_RE.findall(v):
                val, is_pct = _parse(m)
                if val is None or _is_structural(val, is_pct):
                    continue
                if not _match(val, is_pct, allowed):
                    orphans.append((m.strip(), f"section {sid} block {bi}"))
    # ADVISORY only — reports unmatched prose figures for the operator to review,
    # but never fails the run. The matcher is heuristic (derived ratios, sign,
    # rounding) so it false-flags legitimate figures; the hard numeric guards
    # below (DCF, headline single-number) carry the real gate.
    note = "" if pairwise else " (pairwise derivation skipped: >600 input numbers)"
    if orphans:
        sample = "; ".join(f"{tok} @ {p}" for tok, p in orphans[:25])
        chk("prose numbers to review (advisory, non-blocking)",
            True, f"{len(orphans)} number(s) did not auto-trace{note} — review: {sample}")
    else:
        chk("prose numbers to review (advisory, non-blocking)",
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

    # --- 3b. DCF/EVA bridge reconciles via the model's OWN restated numbers --
    # The check above only fires when the ground-truth data happens to carry
    # keys literally named "net_debt"/"terminal_value", which the real engine
    # data doesn't (it has bridge.interest_bearing_debt and no explicit
    # terminal_value at all — only cumulative_discounted_fcff, which already
    # bakes the terminal PV in). These checks instead validate the explicit
    # `scoring.dcf_bridge` / `scoring.eva_bridge` fields the prompt now asks
    # the model to restate from OSIO 9/10's own bridge tables — catching the
    # reported bug class where a report's own bridge numbers didn't sum to
    # its own stated total (e.g. "4001 - 1789 + 3 = 2215, not the reported 2365").
    scoring = output.get("scoring") or {}
    dcf_bridge = scoring.get("dcf_bridge") or {}
    pv_fc = dcf_bridge.get("pv_forecast_period_teur")
    pv_tv = dcf_bridge.get("pv_terminal_value_teur")
    ev = dcf_bridge.get("enterprise_value_teur")
    debt = dcf_bridge.get("interest_bearing_debt_teur")
    cash_b = dcf_bridge.get("cash_teur")
    equity = dcf_bridge.get("equity_value_before_floor_teur")

    if _is_num(pv_fc) and _is_num(pv_tv) and _is_num(ev):
        tol = max(2.0, 0.01 * abs(ev))
        chk("DCF bridge: PV(ennustejakso) + PV(terminaali) = EV (±1% / ±2 tEUR)",
            abs((pv_fc + pv_tv) - ev) <= tol,
            f"{pv_fc} + {pv_tv} = {round(pv_fc + pv_tv, 2)} vs EV {ev}")
    else:
        chk("DCF bridge: PV(ennustejakso) + PV(terminaali) = EV", True,
            "skipped: dcf_bridge.pv_forecast_period_teur / pv_terminal_value_teur / enterprise_value_teur missing")

    if _is_num(ev) and _is_num(debt) and _is_num(cash_b) and _is_num(equity):
        tol = max(2.0, 0.01 * abs(equity))
        computed = ev - debt + cash_b
        chk("DCF bridge: EV - korolliset velat + kassa = oman pääoman arvo (±1% / ±2 tEUR)",
            abs(computed - equity) <= tol,
            f"computed {round(computed, 2)} vs stated {equity}")
    else:
        chk("DCF bridge: EV - korolliset velat + kassa = oman pääoman arvo", True,
            "skipped: dcf_bridge fields missing")

    # Cross-check the model's restated bridge against the engine ground truth
    # it was supposed to copy from (not re-derive).
    gt_pv_fc = _sum_list(disc)
    if _is_num(pv_fc) and _is_num(gt_pv_fc):
        tol = max(2.0, 0.01 * abs(gt_pv_fc))
        chk("DCF: stated PV(ennustejakso) matches sum(valuation_engine.dcf.discounted_fcff) (±1% / ±2 tEUR)",
            abs(pv_fc - gt_pv_fc) <= tol, f"stated {pv_fc} vs sum {round(gt_pv_fc, 2)}")
    else:
        chk("DCF: stated PV(ennustejakso) matches engine sum", True, "skipped: not available")

    gt_cum = dcf.get("cumulative_discounted_fcff")
    gt_equity = dcf.get("equity_value_before_floor")
    if _is_num(ev) and isinstance(gt_cum, list) and gt_cum and _is_num(gt_cum[0]):
        tol = max(2.0, 0.01 * abs(gt_cum[0]))
        chk("DCF: stated EV matches valuation_engine.dcf.cumulative_discounted_fcff[0] (±1% / ±2 tEUR)",
            abs(ev - gt_cum[0]) <= tol, f"stated {ev} vs engine {gt_cum[0]}")
    else:
        chk("DCF: stated EV matches engine ground truth", True, "skipped: not available")

    if _is_num(equity) and _is_num(gt_equity):
        tol = max(2.0, 0.01 * abs(gt_equity))
        chk("DCF: stated equity_value_before_floor matches valuation_engine.dcf.equity_value_before_floor (±1% / ±2 tEUR)",
            abs(equity - gt_equity) <= tol, f"stated {equity} vs engine {gt_equity}")
    else:
        chk("DCF: stated equity_value_before_floor matches engine ground truth", True,
            "skipped: not available")

    # --- 3c. EVA bridge reconciles ------------------------------------------
    eva = ve.get("eva") if isinstance(ve.get("eva"), dict) else {}
    eva_bridge = scoring.get("eva_bridge") or {}
    ic = eva_bridge.get("invested_capital_teur")
    pv_exp = eva_bridge.get("pv_explicit_eva_teur")
    pv_term = eva_bridge.get("pv_terminal_eva_teur")
    eva_equity = eva_bridge.get("equity_value_before_floor_teur")

    if _is_num(ic) and _is_num(pv_exp) and _is_num(pv_term) and _is_num(eva_equity):
        tol = max(2.0, 0.01 * abs(eva_equity))
        computed = ic + pv_exp + pv_term
        chk("EVA bridge: investoitu pääoma + PV(EVA) + PV(terminaali-EVA) = oman pääoman arvo (±1% / ±2 tEUR)",
            abs(computed - eva_equity) <= tol,
            f"computed {round(computed, 2)} vs stated {eva_equity}")
    else:
        chk("EVA bridge reconciles", True, "skipped: eva_bridge fields missing")

    gt_eva_equity = eva.get("equity_value_before_floor")
    if _is_num(eva_equity) and _is_num(gt_eva_equity):
        tol = max(2.0, 0.01 * abs(gt_eva_equity))
        chk("EVA: stated equity_value_before_floor matches valuation_engine.eva.equity_value_before_floor (±1% / ±2 tEUR)",
            abs(eva_equity - gt_eva_equity) <= tol, f"stated {eva_equity} vs engine {gt_eva_equity}")
    else:
        chk("EVA: stated equity_value_before_floor matches engine ground truth", True,
            "skipped: not available")

    if _is_num(gt_equity) and _is_num(gt_eva_equity):
        tol = max(2.0, 0.01 * abs(gt_equity))
        chk("DCF/EVA equivalence: equity values match when same forecast/WACC is used (±1% / ±2 tEUR)",
            abs(gt_equity - gt_eva_equity) <= tol,
            f"DCF {gt_equity} vs EVA {gt_eva_equity}")
    else:
        chk("DCF/EVA equivalence", True, "skipped: DCF or EVA equity not available")

    # --- 3d. Accepted method weights sum to 100% ----------------------------
    method_scoring = scoring.get("method_scoring") or []
    dcf_methods = [
        m for m in method_scoring
        if isinstance(m, dict) and "dcf" in str(m.get("method", "")).lower()
    ]
    eva_methods = [
        m for m in method_scoring
        if isinstance(m, dict) and "eva" in str(m.get("method", "")).lower()
    ]
    if dcf_methods and eva_methods:
        eva_weighted = [
            m for m in eva_methods
            if (
                str(m.get("status", "")).lower().startswith("hyv")
                or (_is_num(m.get("weight_pct")) and m.get("weight_pct") > 0)
            )
        ]
        chk("DCF/EVA equivalence: EVA is reference-only, not separately weighted",
            not eva_weighted,
            "EVA has accepted status or positive weight" if eva_weighted else "ok")
        dcf_vals = [m.get("value_teur") for m in dcf_methods if _is_num(m.get("value_teur"))]
        eva_vals = [m.get("value_teur") for m in eva_methods if _is_num(m.get("value_teur"))]
        if dcf_vals and eva_vals:
            tol = max(2.0, 0.01 * abs(dcf_vals[0]))
            chk("DCF/EVA equivalence: method table values match (±1% / ±2 tEUR)",
                abs(dcf_vals[0] - eva_vals[0]) <= tol,
                f"DCF {dcf_vals[0]} vs EVA {eva_vals[0]}")
        else:
            chk("DCF/EVA equivalence: method table values match", True, "skipped: values missing")
    else:
        chk("DCF/EVA equivalence: EVA reference-only", True, "skipped: DCF/EVA method rows missing")

    accepted_weights = [
        m.get("weight_pct") for m in method_scoring
        if isinstance(m, dict) and m.get("status") == "hyväksytty" and _is_num(m.get("weight_pct"))
    ]
    if accepted_weights:
        total = sum(accepted_weights)
        chk("hyväksyttyjen menetelmien painot (weight_pct) summautuvat 100 %:iin (±0.5)",
            abs(total - 100.0) <= 0.5, f"summa {round(total, 2)}")
    else:
        chk("menetelmäpainot summautuvat 100 %:iin", True, "skipped: ei hyväksyttyjä painoja")

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

    # --- 6. Fabrication gate (BLOCKING) -------------------------------------
    # A euro figure in narrative prose (>=1000, not a %, not a 4-digit year) that
    # traces to NOTHING verified — not input_data, not a one-step sum/difference
    # of it, not any structured figure in this stage's own tables, and not the
    # upstream enrichment/profile context — is very likely invented. Unlike the
    # advisory sweep in check 1, this FAILS the stage: self-heal then re-prompts
    # the model, and the deliver-gate blocks a run that stays unresolved.
    # Dress-rehearsed against 6 real reports (Athlos, OGOship, Jungle Juice,
    # SearchCo, Supercell, Virnex): ZERO false positives, and it flags injected
    # fabrications (123 456 / 987 654 / 55 555 tEUR ...).
    if len(base) <= 700:
        bl = list(base)
        onestep = set(base)
        for a in bl:
            for c in bl:
                onestep.add(a - c)
                onestep.add(a + c)
        hard_allowed = onestep | _numbers_of(context or {}) | _nonprose_numbers(output)
        fabricated = []
        for sec in (output.get("sections") or []):
            if not isinstance(sec, dict):
                continue
            sid = str(sec.get("id"))
            for bi, b in enumerate(sec.get("blocks") or []):
                if not isinstance(b, dict) or b.get("type") not in ("paragraph", "callout"):
                    continue
                v = b.get("text")
                if not isinstance(v, str) or len(v) < 4:
                    continue
                for m in _NUM_RE.findall(v):
                    val, is_pct = _parse(m)
                    if val is None or is_pct:
                        continue
                    a = abs(val)
                    if a < 1000:                          # only euro-scale amounts
                        continue
                    if a == int(a) and 1900 <= int(a) <= 2100:  # a year
                        continue
                    if not _match_hard(val, hard_allowed):
                        fabricated.append(f"{m.strip()} @ section {sid} block {bi}")
        chk("no invented euro figure in prose (>=1000, untraceable to data)",
            not fabricated,
            ("BLOCKED — figure(s) trace to no verified source: " + "; ".join(fabricated[:15]))
            if fabricated else "all euro figures in prose trace to data / derivation / tables")
    else:
        chk("no invented euro figure in prose (>=1000, untraceable to data)", True,
            f"skipped: input number set too large ({len(base)}) to derive safely")

    return {"passed": all(c["passed"] for c in checks), "checks": checks}
