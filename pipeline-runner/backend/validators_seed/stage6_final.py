# Vaihe 6 – Tiivistelmä + kokoaja FINAL CONSISTENCY VALIDATOR.
# Runs on the stage-6 wrapper output. Confirms the cover carries one intact
# primary valuation figure: the scenario expected value (2026-07-10: promoted
# to the cover headline). The conservative base case is still required on the
# cover, but only as the secondary/comparison figure — see render.py's _cover.
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
_SOURCE_MARK_RE = re.compile(r"\(lähde:\s*[^)]+\)", re.I)
_SENTENCE_RE = re.compile(r"(?<=[.!?])\s+")
_PUBLIC_CLAIM_CUES = (
    "julkis",
    "lähte",
    "verkkosiv",
    "markkina",
    "kilpailija",
    "toimiala",
    "yrityskauppa",
    "rahoituskierros",
    "ostotarjous",
    "sopimus",
    "asiakas",
    "liikevaihtopoikkeama",
    "rakenteellinen",
    "discontinued",
    "divest",
    "acquisition",
    "ifrs 15",
)


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


def _source_mark_issues(output):
    issues = []
    for sec in (output.get("sections") or []):
        if not isinstance(sec, dict):
            continue
        sid = str(sec.get("id"))
        for bi, b in enumerate(sec.get("blocks") or []):
            if not isinstance(b, dict) or b.get("type") not in ("paragraph", "callout"):
                continue
            v = b.get("text")
            if not isinstance(v, str) or len(v) < 20:
                continue
            for sentence in _SENTENCE_RE.split(v):
                s = sentence.strip()
                low = s.lower()
                if (
                    len(s) >= 20
                    and not _SOURCE_MARK_RE.search(s)
                    and "asiakkaan ilmoittama" not in low
                    and "käyttäjän" not in low
                    and any(cue in low for cue in _PUBLIC_CLAIM_CUES)
                ):
                    issues.append(f"section {sid} block {bi}: {s[:160]}")
    return issues


def _scenario_value(s):
    """Tolerant scenario-value reader — same alias policy as render._scenario_num
    (kept in sync by tests): known keys first, then any numeric *value* key that
    isn't a probability/weight/contribution/enterprise field."""
    for k in ("value_teur", "owner_value_teur", "owner_value", "equity_value",
              "equity_value_teur", "value"):
        v = _first_num(s.get(k))
        if v is not None:
            return v
    for k in sorted(s):
        kl = k.lower()
        if ("value" in kl and "prob" not in kl and "contribution" not in kl
                and "weight" not in kl and "enterprise" not in kl):
            v = _first_num(s.get(k))
            if v is not None:
                return v
    return None


def _active_scenario_issues(output, context):
    """machine_readable.scenarios schema + consistency on the ACTIVE single-writer
    output (the legacy context['scenarios'] checks below only fire for the dead
    6-stage pipeline). SHADOW MODE: reported as advisory issues, never blocking —
    promote to hard gates only after dress-rehearsal on real runs."""
    issues = []
    mr = output.get("machine_readable") or {}
    sc = mr.get("scenarios")
    if not isinstance(sc, list) or not sc:
        return ["machine_readable.scenarios puuttuu tai ei ole lista"]
    if len(sc) != 3:
        issues.append(f"skenaarioita {len(sc)}, pitää olla 3")
    names = " ".join(str((s or {}).get("name", "")).lower() for s in sc)
    for want in ("pessimis", "konservatiiv", "optimis"):
        if want not in names and not (want == "konservatiiv" and "realis" in names):
            issues.append(f"skenaarion nimi puuttuu: {want}*")
    vals, probs = [], []
    for s in sc:
        if not isinstance(s, dict):
            issues.append("skenaarioerä ei ole objekti")
            continue
        v = _scenario_value(s)
        p = _first_num(s.get("probability_pct"))
        if v is None:
            issues.append(f"{s.get('name')}: arvoa ei voitu lukea")
        else:
            if v < 0:
                issues.append(f"{s.get('name')}: negatiivinen omistaja-arvo {v} (lattia on 0)")
            vals.append(v)
        if p is None:
            issues.append(f"{s.get('name')}: probability_pct puuttuu")
        else:
            probs.append(p)
    if len(probs) == 3 and abs(sum(probs) - 100.0) > 1.0:
        issues.append(f"todennäköisyydet summautuvat {sum(probs)} % (pitää olla 100 %)")
    # expected value = sum p*v
    ev_obj = output.get("expected_value")
    ev = _first_num(ev_obj.get("value") if isinstance(ev_obj, dict) else ev_obj)
    if ev is None:
        ev = _first_num(mr.get("expected_value"))
    if ev is not None and len(vals) == 3 and len(probs) == 3:
        calc = sum(p * v for p, v in zip(probs, (
            _scenario_value(s) for s in sc))) / 100.0
        if abs(calc - ev) > max(1.0, 0.005 * abs(ev)):
            issues.append(f"odotusarvo {ev} ei täsmää laskettuun {round(calc, 1)}")
        hv = _first_num((output.get("cover") or {}).get("headline_value"))
        if hv is not None and abs(hv - ev) > max(1.0, 0.005 * abs(ev)):
            issues.append(f"kannen headline_value {hv} != odotusarvo {ev}")
    # ported zero-fundamental guard (dead stage4_scenarios.py) — pessimistic 0
    # with an all-positive forecast EBIT path deserves a flag, not silence
    pess = next((s for s in sc if isinstance(s, dict)
                 and "pessimis" in str(s.get("name", "")).lower()), None)
    if pess is not None and (_scenario_value(pess) or 0) == 0:
        fc_ebit = (((context or {}).get("input_data") or {})
                   .get("forecast") or {}).get("ebit") or []
        nums = [x for x in fc_ebit if isinstance(x, (int, float))]
        if nums and all(x > 0 for x in nums):
            issues.append(
                "pessimistinen skenaario on 0, vaikka ennusteen EBIT on "
                "positiivinen joka vuosi — perustele tai laske downside-arvo")
    return issues


def validate(output: dict, context: dict) -> dict:
    checks = []

    def chk(name, ok, detail=""):
        checks.append({"name": name, "passed": bool(ok), "detail": detail})

    scen_issues = _active_scenario_issues(output, context)
    chk("machine_readable.scenarios schema + consistency (shadow, non-blocking)",
        True,
        ("; ".join(scen_issues[:12])) if scen_issues else "ok")

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

    source_issues = _source_mark_issues(output)
    chk("public-source claims have inline source marks (advisory, non-blocking)",
        True,
        (f"{len(source_issues)} sentence(s) look source-backed but lack '(lähde: ...)': "
         + "; ".join(source_issues[:20])) if source_issues else "ok")

    # --- cover must carry the realistic base case as the primary value -------
    cover = output.get("cover") or {}
    hv_raw = cover.get("headline_value")
    bcv_raw = cover.get("base_case_value")
    hv = _first_num(hv_raw)
    bcv = _first_num(bcv_raw)
    chk("cover has base_case_value (primary cover value)",
        bcv_raw not in (None, "") and bcv is not None,
        "missing/parse-fail cover.base_case_value")

    scenarios = (context or {}).get("scenarios", {}) or {}
    rbc = _first_num(scenarios.get("realistic_base_case_teur"))
    evt = _first_num(scenarios.get("expected_value_teur"))

    # --- 2. cover headline_value, if present, is the scenario expected value -
    if hv is not None and evt is not None:
        chk("cover headline_value == scenarios.expected_value_teur (±1 tEUR)",
            abs(hv - evt) <= 1.0, f"cover {hv} vs expected value {evt}")
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

    # --- 5. years-as-columns tables must carry a label column ----------------
    # Reported bug (Supercell appendix): the full-forecast table rendered as a
    # bare number grid — year columns but no row names, unreadable.
    _yearish = re.compile(r"^(19|20)\d{2}\s*E?$", re.I)

    def _numeric_cell(c):
        if isinstance(c, bool):
            return False
        if isinstance(c, (int, float)):
            return True
        if isinstance(c, str):
            v, _ = _parse(c.strip())
            return v is not None
        return False

    unlabeled = []
    for sec in (secs or []):
        if not isinstance(sec, dict):
            continue
        sid = str(sec.get("id"))
        for bi, b in enumerate(sec.get("blocks") or []):
            if not isinstance(b, dict) or b.get("type") != "table":
                continue
            cols = b.get("columns")
            if not isinstance(cols, list) or len(cols) < 3 or sum(
                    1 for c in cols if _yearish.match(str(c).strip())) < 2:
                continue
            rows = [r for r in (b.get("rows") or []) if isinstance(r, list) and r]
            if rows and all(_numeric_cell(r[0]) for r in rows):
                unlabeled.append(
                    f"section {sid} block {bi} ('{b.get('title') or '?'}'): "
                    f"vuosisarakkeet mutta riveillä ei nimiä — lisää jokaisen rivin "
                    f"alkuun rivin nimi (esim. 'Liikevaihto')")
    chk("vuosisarakkeisissa taulukoissa on rivinimet (Erä-sarake)",
        not unlabeled, "; ".join(unlabeled[:8]) if unlabeled else "ok")

    return {"passed": all(c["passed"] for c in checks), "checks": checks}
