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
import math
import re
from copy import deepcopy

from .report_i18n import t as _t, tf as _tf, localized_input, number, current


def _num(x):
    if isinstance(x, bool):
        return None
    if isinstance(x, (int, float)):
        try:
            value = float(x)
        except OverflowError:
            return None
        return value if math.isfinite(value) else None
    return None


def _fmt(v, decimals=0):
    return number(v, decimals) if v is not None else ""


def _wacc_pct(input_data):
    ve = _obj((input_data or {}).get("valuation_engine"))
    return _num(_obj(ve.get("wacc_parameters")).get("wacc_pct"))


def _obj(value):
    return value if isinstance(value, dict) else {}


def _year(value):
    value = _num(value)
    return int(value) if value is not None and value.is_integer() else None


def discount_periods(input_data):
    """Recover only evidenced same-year DCF horizons, using the engine WACC.

    Missing and zero cash flows cannot evidence timing. Other usable years
    must imply the same valuation date; no calendar schedule is guessed.
    Past rows may have negative horizons; compute() requires a positive exit.
    """
    wacc = _wacc_pct(input_data)
    result = {"periods": {}, "verified": False, "reason": None,
              "engine_wacc_pct": wacc}

    def unavailable(reason):
        return {**result, "periods": {}, "reason": reason}

    if wacc is None or not 1.0 <= wacc <= 40.0:
        return unavailable("Engine WACC is missing or unsupported.")
    dcf = _obj(_obj((input_data or {}).get("valuation_engine")).get("dcf"))
    years = dcf.get("years")
    fcff = dcf.get("fcff")
    discounted = dcf.get("discounted_fcff")
    if not all(isinstance(v, list) for v in (years, fcff, discounted)):
        return unavailable("Same-year engine FCFF and discounted FCFF are unavailable.")
    if not years or len(years) != len(fcff) or len(years) != len(discounted):
        return unavailable("Engine DCF year and cash-flow arrays are not aligned.")
    year_nums = [_year(y) for y in years]
    if None in year_nums or len(set(year_nums)) != len(year_nums):
        return unavailable("Engine DCF years are invalid or duplicated.")
    for year, cash, pv in zip(year_nums, fcff, discounted):
        if cash is None or pv is None:
            continue
        cash, pv = _num(cash), _num(pv)
        if cash is None or pv is None:
            return unavailable("Engine DCF cash flows must be finite numeric values.")
        if cash == pv == 0:
            continue
        if cash == 0 or pv == 0 or (cash > 0) != (pv > 0):
            return unavailable("Engine FCFF and discounted FCFF have invalid signs or zeros.")
        ratio = cash / pv
        if not math.isfinite(ratio) or ratio <= 0:
            return unavailable("Engine DCF discount ratio is not finite and positive.")
        n = math.log(ratio) / math.log1p(wacc / 100.0)
        if not math.isfinite(n) or not -100 <= n <= 100:
            return unavailable("Engine DCF discount period is unsupported.")
        result["periods"][year] = n
    if not result["periods"]:
        return unavailable("No nonzero same-year engine cash-flow pair supports timing.")
    offsets = [n - year for year, n in result["periods"].items()]
    if max(offsets) - min(offsets) > 1e-4:
        return unavailable("Engine DCF discount periods imply inconsistent valuation dates.")
    return {**result, "verified": True}


def _base_year(input_data):
    years = _obj((input_data or {}).get("actuals")).get("years")
    nums = [y for v in years for y in [_year(v)] if y is not None] if isinstance(years, list) else []
    if nums:
        return max(nums)
    fyears = _obj((input_data or {}).get("forecast")).get("years")
    nums = [y for v in fyears for y in [_year(v)] if y is not None] if isinstance(fyears, list) else []
    return nums[0] - 1 if nums else None


@localized_input(1)
def compute(assumptions, input_data):
    """Return {value_teur, steps:[...]} or None when the inputs don't support a
    deterministic calculation (missing/implausible fields -> caller keeps the
    writer's value and QA flags it)."""
    if not isinstance(assumptions, dict):
        return None
    cv = _num(assumptions.get("continuing_value_teur"))
    nd = _num(assumptions.get("net_debt_realization_year_teur"))
    year = _year(assumptions.get("realization_year"))
    dilution = 0.0 if assumptions.get("dilution_pct") is None else _num(assumptions["dilution_pct"])
    wacc = (_wacc_pct(input_data) if assumptions.get("wacc_pct") is None
            else _num(assumptions["wacc_pct"]))
    if None in (cv, nd, year, wacc, dilution):
        return None
    version = _obj((input_data or {}).get("meta")).get("scenario_timing_version")
    if version == 2 and not isinstance(version, bool):
        timing = discount_periods(input_data)
        n = timing["periods"].get(year) if timing["verified"] else None
        basis, verified, reason = "engine_fcff_ratio", True, None
    elif version is None:
        base = _base_year(input_data)
        n = year - base if base is not None else None
        basis, verified = "legacy_year_difference", False
        reason = "Legacy year-difference timing retained; engine valuation date is unverified."
    else:
        return None
    if n is None:
        return None
    # guardrails: reject implausible inputs rather than compute garbage
    if not (cv > 0 and 0 < n <= 15 and 1.0 <= wacc <= 40.0 and 0 <= dilution < 100):
        return None
    factor = (1 + wacc / 100.0) ** n
    equity_n = cv - nd
    if not math.isfinite(equity_n):
        return None
    pv = equity_n / factor
    value = pv * (1 - dilution / 100.0)
    floored = max(value, 0.0)
    period_label = _fmt(n, 3) if verified else str(n)
    discount_label = _tf('Diskonttaus nykyhetkeen (WACC {0} %, {1} v, kerroin {2})',
                         _fmt(wacc, 1), period_label, _fmt(factor, 3))
    if not verified:
        discount_label = (f"Legacy discounting from year {base} (WACC {_fmt(wacc, 1)} %, {n} yr, factor {_fmt(factor, 3)}); valuation date unverified"
                          if current().language == "en" else
                          f"Vanha diskonttaus vuodesta {base} (WACC {_fmt(wacc, 1)} %, {n} v, kerroin {_fmt(factor, 3)}); arvostuspäivää ei ole varmennettu")
    steps = [
        [_t('Jatkuvan arvon arvo toteutumisvuonna ') + str(int(year)), _fmt(cv)],
        [_t('− Ennustettu nettovelka vuonna ') + str(int(year)), _fmt(nd)],
        [_t('= Oman pääoman arvo vuonna ') + str(int(year)), _fmt(equity_n)],
        [discount_label, _fmt(pv)],
    ]
    if dilution:
        steps.append([_tf('− Laimennusvaikutus {0} %', f'{_fmt(dilution, 1)}'), _fmt(value)])
    if floored != value:
        steps.append([_t('Omistaja-arvon lattia'), "0"])
    steps.append([_t('Optimistinen omistaja-arvo'), _fmt(floored)])
    return {"value_teur": floored, "steps": steps, "realization_year": int(year),
            "wacc_pct": wacc, "n": n, "timing_basis": basis,
            "timing_verified": verified, "timing_reason": reason}


_VALUE_ALIASES = ("value_teur", "owner_value_teur", "owner_value",
                  "equity_value", "equity_value_teur", "value")


def _find_scenario(scenarios, needle):
    for s in scenarios:
        if isinstance(s, dict) and needle in str(s.get("name", "")).lower():
            return s
    return None


def _scenario_cases(scenarios):
    if not isinstance(scenarios, list) or len(scenarios) != 3:
        return None
    labels = {
        "pessimistic": r"pessimistinen|pessimistic",
        "base": r"konservatiivinen|conservative|realistinen|realistic|perusskenaario|base(?:[ -]case)?",
        "optimistic": r"optimistinen|optimistic",
    }
    cases = {}
    for scenario in scenarios:
        if not isinstance(scenario, dict):
            return None
        matches = [kind for kind, pattern in labels.items()
                   if re.search(rf"\b(?:{pattern})\b", str(scenario.get("name", "")), re.I)]
        if len(matches) != 1 or matches[0] in cases:
            return None
        cases[matches[0]] = scenario
    return cases


def _sidecar_matches(sidecar, values, probabilities, expected):
    if not isinstance(sidecar, dict):
        return False
    if "scenarios" in sidecar:
        cases = _scenario_cases(sidecar["scenarios"])
        if cases is None:
            return False
        for kind, scenario in cases.items():
            supplied = [_num(scenario[k]) for k in _VALUE_ALIASES if k in scenario]
            probability = _num(scenario.get("probability_pct"))
            if (not supplied or None in supplied
                    or any(abs(v - values[kind]) > _ROUNDING_TOLERANCE for v in supplied)
                    or probability is None or abs(probability - probabilities[kind]) > 0.01):
                return False
    for key, target in (("expected_value_teur", expected),
                        ("realistic_base_case_teur", values["base"])):
        if key in sidecar:
            value = _num(sidecar[key])
            if value is None or abs(value - target) > _ROUNDING_TOLERANCE:
                return False
    return True


_ROUNDING_TOLERANCE = 0.55  # The writer may display whole source-currency thousands.
_WRITTEN_NUMBER = r"[−-]?(?:\d{1,3}(?:[ ,\u00a0\u202f]\d{3})+|\d+)(?:[.,]\d+)?"
_OUTPUT_LABELS = {
    "expected": (r"(?:skenaarioiden\s+)?odotusarvo|"
                 r"(?:scenario(?:-weighted)?\s+)?expected (?:equity )?value"),
    "optimistic": (r"optimistisen skenaarion (?:diskontattu )?(?:omistaja-arvo|oman pääoman arvo|arvo)|"
                   r"optimistinen (?:omistaja-arvo|arvo)|"
                   r"optimistic (?:scenario(?:'s)? )?(?:(?:owner|equity) )?value"),
}


def _written_value(value, unit=None):
    if _num(value) is not None:
        return _num(value)
    if not isinstance(value, str):
        return None
    suffix = re.escape(current().unit)
    match = re.fullmatch(rf"\s*({_WRITTEN_NUMBER})\s*(?:{suffix})?\s*", value)
    if not match or (current().unit not in value and unit != current().unit):
        return None
    raw = re.sub(r"[ \u00a0\u202f]", "", match[1]).replace("−", "-")
    raw = raw.replace(",", "") if current().language == "en" else raw.replace(",", ".")
    try:
        return _num(float(raw))
    except ValueError:
        return None


def _writer_output_claims(wrapper):
    """Narrow, direct output assertions only; this is not a general prose audit."""
    sections = wrapper.get("sections")
    if not isinstance(sections, list):
        return
    money = rf"(?P<amount>{_WRITTEN_NUMBER})\s*{re.escape(current().unit)}\b"
    for section in sections:
        if not isinstance(section, dict) or str(section.get("id")) not in ("1", "11"):
            continue
        blocks = section.get("blocks")
        if not isinstance(blocks, list):
            continue
        for block in blocks:
            if not isinstance(block, dict):
                continue
            if block.get("type") == "scenario_table" and "optimis" in str(block.get("scenario", "")).lower():
                for key in _VALUE_ALIASES:
                    value = _num(block.get(key))
                    if value is not None:
                        yield "optimistic", value, section["id"]
            if block.get("type") == "metric_cards":
                cards = block.get("cards")
                for card in cards if isinstance(cards, list) else []:
                    if not isinstance(card, dict):
                        continue
                    for kind, label in _OUTPUT_LABELS.items():
                        if re.fullmatch(label, str(card.get("label", "")).strip(), re.I):
                            value = _written_value(card.get("value"), block.get("unit"))
                            if value is not None:
                                yield kind, value, section["id"]
            if block.get("type") in ("paragraph", "callout"):
                text = block.get("text")
                if not isinstance(text, str):
                    continue
                for sentence in re.split(r"(?<=[.!?;])\s+", text):
                    sentence = sentence.strip().replace("**", "")
                    if re.search(r"aiemp|edelli|historial|vertail|previous|historical|prior|vuonna 20\d{2}|in 20\d{2}", sentence, re.I):
                        continue
                    if re.match(r"^skenaarioiden omistaja-arvot ovat\b", sentence, re.I):
                        listed = re.search(rf"\boptimistisessa\s+{money}(?=\s|[.,;:]|$)", sentence, re.I)
                        if listed:
                            yield "optimistic", _written_value(listed["amount"], current().unit), section["id"]
                    for kind, label in _OUTPUT_LABELS.items():
                        subject = re.match(rf"^(?:(?:tämän raportin|raportin|päivitetty|updated)\s+)?(?:{label})\s*(?:(?:on|is|equals)\s*|[:=]\s*)", sentence, re.I)
                        if not subject:
                            continue
                        rest = sentence[subject.end():]
                        direct = re.match(rf"(?:noin\s+|approximately\s+)?{money}(?=\s|[.,;:]|$)", rest, re.I)
                        if direct:
                            value = _written_value(direct[0])
                            if value is not None:
                                yield kind, value, section["id"]
                        elif kind == "expected" and "=" in rest:
                            arithmetic, final = rest.rsplit("=", 1)
                            arithmetic = re.sub(re.escape(current().unit), "", arithmetic, flags=re.I)
                            if re.fullmatch(r"[\d\s.,%×*/()+−\-]+", arithmetic):
                                final = re.fullmatch(rf"\s*{money}[.!]?\s*", final, re.I)
                                if final:
                                    yield kind, _written_value(final[0].rstrip(".! ")), section["id"]
            if block.get("type") == "table" and block.get("table_id") in ("scenarios", "optimistic_assumptions"):
                rows = block.get("rows")
                if not isinstance(rows, list):
                    continue
                if block["table_id"] == "scenarios":
                    columns = block.get("columns")
                    columns = columns if isinstance(columns, list) else []
                    indices = [i for i, col in enumerate(columns)
                               if re.match(r"^(?:omistaja-arvo|equity value|owner value)\b", str(col), re.I)]
                    for row in rows:
                        if isinstance(row, list) and row and str(row[0]).lower() in ("optimistinen", "optimistic"):
                            for i in indices:
                                value = _written_value(row[i], block.get("unit")) if i < len(row) else None
                                if value is not None:
                                    yield "optimistic", value, section["id"]
                else:
                    for row in rows:
                        if isinstance(row, list) and len(row) > 1 and str(row[0]).lower() in ("diskontattu omistaja-arvo", "discounted equity value", "discounted owner value"):
                            value = _written_value(row[1], block.get("unit"))
                            if value is not None:
                                yield "optimistic", value, section["id"]


@localized_input(1)
def review_writer_values(wrapper, input_data):
    """Return v2 release blockers before apply() overwrites the writer's values.

    Historical snapshots are unchanged. Prose checking intentionally covers
    only direct summary/scenario assertions and labelled outputs, not arbitrary
    linguistic formulations or historical comparisons. Never rewrites text.
    """
    if _obj((input_data or {}).get("meta")).get("scenario_timing_version") != 2:
        return []
    messages = []

    def block(fi, en):
        message = en if current().language == "en" else fi
        if message not in messages:
            messages.append(message)

    mr = _obj(_obj(wrapper).get("machine_readable"))
    result = compute(mr.get("optimistic_assumptions"), input_data)
    if result is None:
        block("Optimistisen skenaarion arvoa ei voida varmentaa: tarkista oletukset sekä toteutumisvuoden moottorin kassavirrat ja diskonttausajoitus.",
              "The optimistic scenario value cannot be verified: check its assumptions and the engine cash flows and discount timing for its realization year.")
    scenarios = mr.get("scenarios")
    if not isinstance(scenarios, list) or not scenarios or not all(isinstance(s, dict) for s in scenarios):
        block("Skenaarioiden rakenteiset arvot tai todennäköisyydet puuttuvat.",
              "Structured scenario values or probabilities are missing.")
        return messages
    cases = _scenario_cases(scenarios)
    if cases is None:
        block("Raportissa on oltava täsmälleen kolme eri skenaariota: yksi pessimistinen, yksi konservatiivinen perusskenaario ja yksi optimistinen.",
              "The report must contain exactly three distinct scenarios: one pessimistic, one conservative base case, and one optimistic.")
        return messages
    values, probabilities = [], []
    for scenario in scenarios:
        supplied = [scenario[k] for k in _VALUE_ALIASES if k in scenario]
        nums = [_num(v) for v in supplied]
        probability = _num(scenario.get("probability_pct"))
        if not nums or None in nums or max(nums) - min(nums) > _ROUNDING_TOLERANCE or probability is None or not 0 <= probability <= 100:
            block("Skenaarion arvo tai todennäköisyys puuttuu, on virheellinen tai sisältää ristiriitaisia rinnakkaislukuja.",
                  "A scenario value or probability is missing, invalid, or conflicts with another value field.")
            return messages
        value = nums[0]
        if result is not None and scenario is cases["optimistic"]:
            value = round(result["value_teur"], 1)
            if any(abs(n - value) > _ROUNDING_TOLERANCE for n in nums):
                block(f"Kirjoittajan optimistinen omistaja-arvo poikkeaa varmennetusta arvosta {_fmt(value)} {current().unit}; korjaa myös raporttiteksti ennen hyväksymistä.",
                      f"The writer's optimistic equity value differs from the verified {_fmt(value)} {current().unit}; correct the report text before approval.")
        values.append(value)
        probabilities.append(probability)
    if abs(sum(probabilities) - 100) > 0.01:
        block("Skenaarioiden todennäköisyyksien summan on oltava 100 %.",
              "Scenario probabilities must total 100%.")
        return messages
    expected = sum(p * v / 100 for p, v in zip(probabilities, values))
    if "_scenarios" in wrapper:
        case_values = {kind: values[next(i for i, s in enumerate(scenarios) if s is case)]
                       for kind, case in cases.items()}
        case_probabilities = {kind: _num(case.get("probability_pct")) for kind, case in cases.items()}
        if not _sidecar_matches(wrapper["_scenarios"], case_values, case_probabilities, expected):
            block("Raportin rinnakkainen skenaariodata poikkeaa varmennetuista skenaarioista tai odotusarvosta; korjaa rinnakkaisdata ennen hyväksymistä.",
                  "The report's scenario sidecar differs from the verified scenarios or expected value; correct the sidecar before approval.")
    written_expected = _obj(wrapper).get("expected_value")
    written_expected = _num(written_expected.get("value")) if isinstance(written_expected, dict) else _num(written_expected)
    if written_expected is None or not math.isfinite(expected):
        block("Raportin rakenteinen odotusarvo puuttuu tai sitä ei voida laskea.",
              "The report's structured expected value is missing or cannot be calculated.")
    elif abs(written_expected - expected) > _ROUNDING_TOLERANCE:
        block(f"Kirjoittajan odotusarvo poikkeaa skenaarioiden varmennetusta painotetusta summasta {_fmt(expected)} {current().unit}; korjaa raportin yhteenveto ja laskenta.",
              f"The writer's expected value differs from the verified weighted scenario sum of {_fmt(expected)} {current().unit}; correct the summary and calculation.")
    if result is not None and math.isfinite(expected):
        for kind, value, section in _writer_output_claims(wrapper):
            target = result["value_teur"] if kind == "optimistic" else expected
            if value is not None and abs(value - target) > _ROUNDING_TOLERANCE:
                label = "optimistinen omistaja-arvo" if kind == "optimistic" else "odotusarvo"
                english = "optimistic equity value" if kind == "optimistic" else "expected value"
                block(f"Osion {section} {label} ({_fmt(value)} {current().unit}) poikkeaa varmennetusta arvosta {_fmt(target)} {current().unit}; korjaa teksti tai taulukko.",
                      f"Section {section}'s {english} ({_fmt(value)} {current().unit}) differs from the verified {_fmt(target)} {current().unit}; correct the text or table.")
    return messages


@localized_input(1)
def apply(wrapper, input_data):
    """Override the optimistic scenario value + expected value + cover headline
    with the deterministic waterfall when the writer supplied usable
    assumptions. No assumptions -> report unchanged (old runs keep working)."""
    mr = wrapper.get("machine_readable") or {}
    scenarios = mr.get("scenarios")
    if not isinstance(scenarios, list):
        return None
    v2 = _obj((input_data or {}).get("meta")).get("scenario_timing_version") == 2
    cases = _scenario_cases(scenarios) if v2 else None
    if v2 and cases is None:
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
        calc = " + ".join(_tf('{0} % × {1} tEUR', f'{_fmt(p)}', f'{_fmt(v)}')
                          for p, v in zip(probs, vals)) + _tf(' = {0} tEUR', f'{_fmt(ev)}')
        evf = wrapper.get("expected_value")
        if isinstance(evf, dict):
            evf["value"] = round(ev, 1)
            evf["calculation"] = calc
        else:
            wrapper["expected_value"] = {"value": round(ev, 1), "unit": _t('tEUR'),
                                         "calculation": calc}
        cover = wrapper.get("cover")
        if isinstance(cover, dict) and cover.get("headline_value") not in (None, ""):
            cover["headline_value"] = _tf('{0} tEUR', f'{_fmt(ev)}')
        if v2 and "_scenarios" in wrapper:
            base = next(_num(cases["base"][k]) for k in _VALUE_ALIASES
                        if _num(cases["base"].get(k)) is not None)
            wrapper["_scenarios"] = {
                **_obj(wrapper["_scenarios"]), "scenarios": deepcopy(scenarios),
                "expected_value_teur": round(ev, 1), "realistic_base_case_teur": base,
            }
    return result


def derivation_blocks(result):
    explanation = _t('Optimistisen skenaarion arvo on laskettu deterministisesti yllä olevista oletuksista: oman pääoman arvo muodostetaan toteutumisvuonna (jatkuva arvo miinus saman vuoden ennustettu nettovelka) ja diskontataan kokonaisuutena nykyhetkeen. Välivuosien rahoitustarvetta ei vähennetä erikseen, koska toteutumisvuoden nettovelka sisältää jo sen rahoituksen. Oletukset ovat AI:n muodostamia ja käyttäjän muokattavissa.')
    if not result.get("timing_verified"):
        explanation = (
            "Legacy scenario values retain discounting by the difference between the realization year and the last actual year (or the year before the first forecast). This timing has not been verified against the engine valuation date. Interim funding deficits are not subtracted separately; realization-year net debt already represents their financing."
            if current().language == "en" else
            "Vanhan skenaariolaskennan arvot säilyttävät diskonttauksen toteutumisvuoden ja viimeisen toteumavuoden (tai ensimmäistä ennustevuotta edeltävän vuoden) erotuksella. Tätä ajoitusta ei ole varmennettu moottorin arvostuspäivään. Välivuosien rahoitustarvetta ei vähennetä erikseen, koska toteutumisvuoden nettovelka sisältää jo sen rahoituksen."
        )
    return [{
        "type": "table",
        "table_id": "deterministic_optimistic_waterfall",
        "title": _t('Optimistisen skenaarion arvon johto (deterministinen)'),
        "unit": _t('tEUR'),
        "columns": [_t('Erä'), _t('Arvo')],
        "rows": result["steps"],
    }, {
        "type": "paragraph",
        "text": explanation,
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
