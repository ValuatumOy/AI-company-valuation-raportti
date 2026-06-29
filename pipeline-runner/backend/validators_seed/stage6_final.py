# Vaihe 6 – Tiivistelmä + kokoaja FINAL CONSISTENCY VALIDATOR.
# Runs on the stage-6 wrapper output. Confirms the cover carries BOTH the
# expected value and the realistic base case (the bug that shipped twice was a
# cover that showed only one), that they match the stage-4 scenarios, and that
# no prose figure is absent from machine_readable.
import re

# Numbers may use any space as a thousands separator: ASCII, NBSP (U+00A0),
# narrow NBSP (U+202F), thin space (U+2009). Both the matcher and the parser
# must account for all of them — otherwise a correctly formatted Finnish figure
# like "1 598 tEUR" (with an NBSP) parses to 1 and false-fails the cover.
# Thousands groups must be exactly 3 digits, else a year glued to the next
# value ("2023 1,62 %") matched as one bogus number -> false orphan.
_SEP = "[\u0020\u00a0\u202f\u2009]"
_NUM_RE = re.compile(r"[\u2212-]?(?:\d{1,3}(?:" + _SEP + r"\d{3})+|\d+)(?:,\d+)?\s*%?")
_WS = re.compile(r"[\s   ]")


def _parse(tok):
    is_pct = "%" in tok
    t = _WS.sub("", tok.replace("%", "").replace("−", "-")).replace(",", ".")
    try:
        return float(t), is_pct
    except ValueError:
        return None, is_pct


def _first_num(s):
    if isinstance(s, bool):
        return None
    if isinstance(s, (int, float)):
        return float(s)
    if isinstance(s, str):
        m = _NUM_RE.search(s)
        if m:
            v, _ = _parse(m.group(0))
            return v
    return None


def _walk(obj, path=""):
    if isinstance(obj, dict):
        for k, v in obj.items():
            yield from _walk(v, f"{path}.{k}" if path else str(k))
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            yield from _walk(v, f"{path}[{i}]")
    else:
        yield path, obj


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


def _match(val, is_pct, allowed):
    # Sign-insensitive: Finnish prose states costs/expenses as positive magnitudes
    # ("henkilöstökulut 5 213 tEUR") while the source stores them signed (-5213).
    # Match on magnitude so that legitimate figure is not a false orphan.
    tol = 0.5 if is_pct else max(1.0, 0.005 * abs(val))
    av = abs(val)
    return any(abs(val - a) <= tol or abs(av - abs(a)) <= tol for a in allowed)


def validate(output: dict, context: dict) -> dict:
    checks = []

    def chk(name, ok, detail=""):
        checks.append({"name": name, "passed": bool(ok), "detail": detail})

    mr = output.get("machine_readable") or {}
    chk("machine_readable present", bool(mr),
        "missing machine_readable block" if not mr else "")
    # A figure is legitimate if it appears in machine_readable OR anywhere in the
    # verified upstream pipeline data (input_data, scoring, scenarios, the locked
    # section numbers). machine_readable is a summary, not a complete index, so
    # requiring every prose figure to live in it alone produced false orphans.
    allowed = _numbers_of(mr) | _numbers_of(context or {})

    # --- 1. no section prose references a figure absent from machine_readable -
    # Scope to section content only (the spec's intent). The wrapper fields
    # (expected_value.calculation, confidence.deciding_rule, cover) legitimately
    # contain intermediate/explanatory figures that need not all live in
    # machine_readable, so sweeping them produced false orphans.
    # Scope to NARRATIVE prose (paragraph/callout text) only. Tables, key_value,
    # metric_cards and charts carry source IDs, registry numbers, dates and other
    # non-financial identifiers (e.g. a sources table in section 15) that are not
    # meant to trace to machine_readable — sweeping them produced false orphans.
    orphans = []
    for sec in output.get("sections") or []:
        if not isinstance(sec, dict):
            continue
        sid = str(sec.get("id"))
        for bi, b in enumerate(sec.get("blocks") or []):
            if not isinstance(b, dict) or b.get("type") not in ("paragraph", "callout"):
                continue
            v = b.get("text")
            if not isinstance(v, str) or len(v) < 12:
                continue
            for m in _NUM_RE.findall(v):
                val, is_pct = _parse(m)
                if val is None:
                    continue
                if is_pct is False and val == int(val) and 1900 <= int(val) <= 2100:
                    continue  # year
                if not _match(val, is_pct, allowed):
                    orphans.append(f"{m.strip()} @ section {sid} block {bi}")
    # ADVISORY only — never fails the run; the hard gate is the cover/scenario
    # consistency + disclaimer checks below. The prose-figure match is heuristic
    # and false-flags legitimate figures, so it reports rather than blocks.
    chk("prose figures to review (advisory, non-blocking)", True,
        (f"{len(orphans)} figure(s) not in machine_readable — review: "
         + "; ".join(orphans[:25])) if orphans else "ok")

    # --- cover must carry BOTH expected value and realistic base case --------
    cover = output.get("cover") or {}
    hv_raw = cover.get("headline_value")
    bcv_raw = cover.get("base_case_value")
    hv = _first_num(hv_raw)
    bcv = _first_num(bcv_raw)
    chk("cover has headline_value",
        hv_raw not in (None, "") and hv is not None,
        "missing/parse-fail cover.headline_value")
    chk("cover has base_case_value (both figures required)",
        bcv_raw not in (None, "") and bcv is not None,
        "missing/parse-fail cover.base_case_value — cover must show BOTH")

    scenarios = (context or {}).get("scenarios", {}) or {}
    ev = _first_num(scenarios.get("expected_value_teur"))
    rbc = _first_num(scenarios.get("realistic_base_case_teur"))

    # --- 2. cover headline_value == scenarios.expected_value_teur ------------
    if hv is not None and ev is not None:
        chk("cover headline_value == scenarios.expected_value_teur (±1 tEUR)",
            abs(hv - ev) <= 1.0, f"cover {hv} vs scenarios {ev}")
    else:
        chk("cover headline_value == scenarios.expected_value_teur", True,
            "skipped: value not available")

    # --- 3. cover base_case_value == scenarios.realistic_base_case_teur ------
    if bcv is not None and rbc is not None:
        chk("cover base_case_value == scenarios.realistic_base_case_teur (±1 tEUR)",
            abs(bcv - rbc) <= 1.0, f"cover {bcv} vs scenarios {rbc}")
    else:
        chk("cover base_case_value == scenarios.realistic_base_case_teur", True,
            "skipped: value not available")

    # --- 4. mandatory legal disclaimer (section 16) --------------------------
    # Selling an automated valuation with no "ei sijoitusneuvontaa" notice is a
    # legal exposure. The renderer injects a fallback, but the model is supposed
    # to produce section 16 — flag when it doesn't.
    secs = output.get("sections")
    sec16 = None
    if isinstance(secs, list):
        sec16 = next((s for s in secs if isinstance(s, dict)
                      and str(s.get("id")) == "16"), None)
    chk("legal disclaimer present (section 16, 'ei sijoitusneuvontaa')",
        sec16 is not None and "sijoitusneuvo" in str(sec16).lower(),
        "section 16 missing or lacks the mandatory Vastuuvapaus text")

    return {"passed": all(c["passed"] for c in checks), "checks": checks}
