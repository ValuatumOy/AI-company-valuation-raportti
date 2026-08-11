"""Deterministic QA pass over the FINAL assembled report.

The per-stage validators (validators_seed/*) run on each stage's raw output
*before* assemble.assemble() injects the deterministic DCF-detail / sensitivity /
headcount tables, so nothing today reconciles the assembled report the client
actually receives. This module fills that gap.

It is ADVISORY: `warnings()` returns strings surfaced in report_readiness but
never blocks a delivery — mirroring the existing advisory prose checks
(stage3_numbers / stage6_final), which were dress-rehearsed against real reports
before any of them was allowed to block. Promote a check to a hard gate only
once it is proven false-positive-free.

Scope note — checks deliberately NOT implemented here:
  * cumulative-monotonic: a cumulative sum of signed cash flows is legitimately
    non-monotonic (FCFF turns positive in later years), so a monotonicity test
    would false-flag the corrected row. The old mislabeled row is fixed at the
    source (dcf_detail.py), not guarded here.
  * corrupted-header dictionary: the "Oma pääoma ilmaKnop…" collision is a render
    artifact (CSS overlap) — the block's `columns` strings are clean in the data,
    so a data-layer check can't see it. Fixed at the source (render.py CSS).
"""
import hashlib
import json
import re

_SENSITIVITY_CHART_IDS = ("wacc_growth_sensitivity", "revenue_ebit_sensitivity")
_SEP = "[    ]"
_NUM_RE = re.compile(r"[−-]?(?:\d{1,3}(?:" + _SEP + r"\d{3})+|\d+)(?:,\d+)?")


def _num(x):
    if isinstance(x, bool):
        return None
    if isinstance(x, (int, float)):
        return float(x)
    if isinstance(x, str):
        m = _NUM_RE.search(x)
        if m:
            t = re.sub(_SEP, "", m.group(0)).replace("−", "-").replace(",", ".")
            try:
                return float(t)
            except ValueError:
                return None
    return None


def _iter_blocks(rep):
    for sec in (rep or {}).get("sections") or []:
        if isinstance(sec, dict):
            for b in sec.get("blocks") or []:
                if isinstance(b, dict):
                    yield sec, b


def _headline_base_case(rep):
    cover = (rep or {}).get("cover") or {}
    for k in ("base_case_value", "headline_value"):
        v = _num(cover.get(k))
        if v is not None:
            return v
    return _num(((rep or {}).get("_scenarios") or {}).get("realistic_base_case_teur"))


def _duplicate_blocks(rep):
    """Exact-duplicate substantial blocks (a duplicated table or long paragraph is
    unambiguous corruption — the class the sensitivity-matrix double-print fell in)."""
    seen, out = {}, []
    for sec, b in _iter_blocks(rep):
        t = b.get("type")
        if t == "table" and b.get("rows"):
            key = json.dumps({"c": b.get("columns"), "r": b.get("rows")},
                             sort_keys=True, ensure_ascii=False)
        elif t in ("paragraph", "callout") and len(str(b.get("text") or "")) >= 120:
            key = "text:" + str(b.get("text"))
        else:
            continue
        h = hashlib.sha1(key.encode("utf-8")).hexdigest()
        if h in seen:
            out.append(f"duplikaattilohko ({t}) osiossa {sec.get('id')} — sama kuin osiossa {seen[h]}")
        else:
            seen[h] = sec.get("id")
    return out


def _sensitivity_calibration(rep):
    """The sensitivity matrices are calibrated so their center cell reproduces the
    base-case equity value. Flag drift > 5 % — the exact stale-PDF failure (526 vs
    669). Construction-guaranteed for the code path, so it only fires on regression."""
    base = _headline_base_case(rep)
    if not base:
        return []
    out = []
    for sec, b in _iter_blocks(rep):
        if b.get("chart_id") not in _SENSITIVITY_CHART_IDS:
            continue
        series = [s for s in (b.get("series") or []) if isinstance(s, dict)]
        if not series:
            continue
        vals = series[len(series) // 2].get("values") or []
        if not vals:
            continue
        center = _num(vals[len(vals) // 2])
        if center is None:
            continue
        if abs(center - base) > 0.05 * abs(base):
            out.append(f"herkkyysmatriisin ({b.get('chart_id')}) keskisolu {round(center)} "
                       f"poikkeaa yli 5 % perusarvosta {round(base)}")
    return out


def _prose_number_reconciliation(rep, limit=12):
    """Advisory: prose figures that match no number in any table/chart/machine_readable.
    Heuristic (rounded/derived figures false-flag), so a review aid, never a gate."""
    allowed = set()

    def _collect(obj):
        if isinstance(obj, bool):
            return
        if isinstance(obj, (int, float)):
            allowed.add(float(obj))
        elif isinstance(obj, str):
            for m in _NUM_RE.findall(obj):
                v = _num(m)
                if v is not None:
                    allowed.add(v)
        elif isinstance(obj, dict):
            for v in obj.values():
                _collect(v)
        elif isinstance(obj, list):
            for v in obj:
                _collect(v)

    _collect((rep or {}).get("machine_readable"))
    for _sec, b in _iter_blocks(rep):
        if b.get("type") not in ("paragraph", "callout"):
            _collect(b)  # tables/charts/key_value contribute the allowed set

    def _match(v):
        tol = max(1.0, 0.005 * abs(v))
        return any(abs(v - a) <= tol or abs(abs(v) - abs(a)) <= tol for a in allowed)

    out = []
    for sec, b in _iter_blocks(rep):
        if b.get("type") not in ("paragraph", "callout"):
            continue
        text = b.get("text")
        if not isinstance(text, str):
            continue
        for m in _NUM_RE.findall(text):
            v = _num(m)
            if v is None or (v == int(v) and 1900 <= int(v) <= 2100):
                continue  # year
            if abs(v) < 100:
                continue  # percentages / counts / small assumptions — check euro-magnitude figures only
            if not _match(v):
                out.append(f"osio {sec.get('id')}: prosaluku {m.strip()} ei täsmää mihinkään taulukko-/lähdearvoon")
                if len(out) >= limit:
                    return out
    return out


def _scenario_value(s):
    """Same alias policy as render._scenario_num (kept in sync by tests)."""
    for k in ("value_teur", "owner_value_teur", "owner_value", "equity_value",
              "equity_value_teur", "value"):
        v = _num(s.get(k))
        if v is not None:
            return v
    for k in sorted(s):
        kl = k.lower()
        if ("value" in kl and "prob" not in kl and "contribution" not in kl
                and "weight" not in kl and "enterprise" not in kl):
            v = _num(s.get(k))
            if v is not None:
                return v
    return None


def _scenario_and_anchor_consistency(rep):
    """Post-assemble consistency on what the client actually receives: scenario
    math, cover vs expected value, and cover vs the deterministic DCF/EVA anchor
    stamped by valuation_equivalence (the AWAKE.AI 762-vs-1144 class)."""
    out = []
    mr = (rep or {}).get("machine_readable") or {}
    sc = mr.get("scenarios")
    cover = (rep or {}).get("cover") or {}
    if isinstance(sc, list) and len(sc) == 3 and all(isinstance(s, dict) for s in sc):
        vals = [_scenario_value(s) for s in sc]
        probs = [_num(s.get("probability_pct")) for s in sc]
        if None not in vals and None not in probs:
            if abs(sum(probs) - 100.0) > 1.0:
                out.append(f"skenaarioiden todennäköisyydet summautuvat {sum(probs)} %")
            calc = sum(p * v for p, v in zip(probs, vals)) / 100.0
            ev_obj = (rep or {}).get("expected_value")
            ev = _num(ev_obj.get("value") if isinstance(ev_obj, dict) else ev_obj)
            if ev is not None and abs(calc - ev) > max(1.0, 0.005 * abs(ev)):
                out.append(f"odotusarvo {ev} ei täsmää skenaarioista laskettuun {round(calc, 1)}")
            hv = _num(cover.get("headline_value"))
            if ev is not None and hv is not None and abs(hv - ev) > max(1.0, 0.005 * abs(ev)):
                out.append(f"kannen headline_value {round(hv, 1)} != odotusarvo {round(ev, 1)}")
    elif sc is not None:
        out.append("machine_readable.scenarios ei ole 3 objektin lista")
    anchor = _num((rep or {}).get("_valuation_anchor_teur"))
    bcv = _num(cover.get("base_case_value"))
    if anchor is not None and bcv is not None:
        floored = max(anchor, 0.0)  # cover shows the owner-value floor, not raw negative
        if abs(bcv - floored) > max(1.0, 0.01 * abs(floored)):
            out.append(
                f"kannen perusarvo {round(bcv, 1)} tEUR poikkeaa kokoonpanon "
                f"DCF/EVA-ankkurista {round(floored, 1)} tEUR — luvut ovat ristiriidassa")
    return out


def _restated_derived_figures(rep):
    """Scenario values restated in a table must match `machine_readable.scenarios`.

    High-precision counterpart to _prose_number_reconciliation, which cannot
    catch this class: the model's own wrong table cell enters that check's
    allowed set. 2026-08-11 Smartly run — one section-11 table gave the
    optimistic scenario as 89 009 while machine_readable and the section's own
    derivation both said 81 317, and the summary then weighted its expected
    value off the wrong figure (65 109 instead of 63 186). render.py corrects
    these at render time; this warning is how the drift stays visible.
    """
    out = []
    mr = (rep or {}).get("machine_readable") or {}
    sc = mr.get("scenarios")
    if not (isinstance(sc, list) and sc):
        return out
    canon = {}
    for s in sc:
        if isinstance(s, dict):
            v = _scenario_value(s)
            name = str(s.get("name") or "").strip().lower()
            if v is not None and name:
                canon[name] = v
    if not canon:
        return out
    for sec, b in _iter_blocks(rep):
        if b.get("type") != "table":
            continue
        for row in b.get("rows") or []:
            if not isinstance(row, list) or len(row) < 2:
                continue
            target = canon.get(str(row[0]).strip().lower())
            if target is None:
                continue
            for cell in row[1:]:
                v = _num(cell)
                if v is None or abs(v) < 100:
                    continue
                if abs(v - target) <= max(1.0, 0.005 * abs(target)):
                    break  # the scenario's own value, stated correctly
                if abs(v - target) <= 0.30 * abs(target):
                    out.append(
                        f"osio {sec.get('id')}: skenaario {row[0]} on taulukossa "
                        f"{v:.0f} mutta machine_readable sanoo {target:.0f}")
                    break
    return list(dict.fromkeys(out))


def _degenerate_optimistic_scenario(rep):
    """An optimistic scenario worth barely more than the base case is broken.

    2026-08-11 Smartly run: optimistic 81 317 vs a 78 869 base — 3 % apart —
    because the scenario paired 250 M€ revenue and a 10,1 % EBIT margin with
    the BASE forecast's 97 370 tEUR net debt, which exists only to fund the
    base case's negative free cash flow. Success-case operations plus
    base-case leverage cancel the upside mechanically. Prompt guardrails 6-7
    tell the model to use the scenario's own net debt; this is how a
    regression stays visible.
    """
    mr = (rep or {}).get("machine_readable") or {}
    sc = mr.get("scenarios")
    if not (isinstance(sc, list) and sc):
        return []
    by_name = {}
    for s in sc:
        if isinstance(s, dict):
            v = _scenario_value(s)
            if v is not None:
                by_name[str(s.get("name") or "").strip().lower()] = v
    opt = next((v for n, v in by_name.items() if n.startswith("optimis")), None)
    base = next((v for n, v in by_name.items() if n.startswith("konservat")), None)
    if opt is None or base is None or base <= 0:
        return []
    if opt < base:
        return [f"optimistinen skenaario {opt:.0f} on konservatiivista "
                f"perusskenaariota {base:.0f} MATALAMPI"]
    if opt < base * 1.15:
        return [f"optimistinen skenaario {opt:.0f} on vain "
                f"{100 * (opt / base - 1):.1f} % perusskenaariota {base:.0f} korkeampi "
                f"— tarkista, siirtyikö perusennusteen nettovelka skenaarioon"]
    return []


def warnings(rep):
    """Non-blocking QA warnings over the assembled report. Never raises."""
    try:
        return (_duplicate_blocks(rep)
                + _sensitivity_calibration(rep)
                + _scenario_and_anchor_consistency(rep)
                + _restated_derived_figures(rep)
                + _degenerate_optimistic_scenario(rep)
                + _prose_number_reconciliation(rep))
    except Exception as e:  # QA must never break report delivery
        return [f"report_qa-tarkistus epäonnistui: {e}"]


if __name__ == "__main__":
    # Self-check: duplicate table caught, calibrated matrix passes, drift caught.
    dup_tbl = {"type": "table", "columns": ["a"], "rows": [["x", "1"]]}
    rep = {"cover": {"base_case_value": "669 tEUR"}, "sections": [
        {"id": "9", "blocks": [dup_tbl]},
        {"id": "11", "blocks": [dict(dup_tbl), {
            "type": "chart", "chart_id": "wacc_growth_sensitivity",
            "series": [{"values": [0, 0, 0]}, {"values": [0, 669, 0]}, {"values": [0, 0, 0]}]}]},
    ]}
    w = warnings(rep)
    assert any("duplikaattilohko" in x for x in w), w
    assert not any("herkkyysmatriisin" in x for x in w), w  # center 669 == base 669, no drift
    rep["sections"][1]["blocks"][1]["series"][1]["values"][1] = 526  # break the center cell
    assert any("herkkyysmatriisin" in x for x in warnings(rep)), "drift not caught"
    assert warnings({}) == [] and warnings(None) == []  # never crashes on sparse input
    print("report_qa self-check ok")
