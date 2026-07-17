"""DCF/EVA equivalence normalization.

EVA/residual-income valuation is algebraically equivalent to DCF when the same
forecast, WACC, invested-capital roll-forward and terminal assumptions are used.
The report should not double-count DCF and EVA as two independent weighted
methods in that case.
"""


def _is_num(x):
    return isinstance(x, (int, float)) and not isinstance(x, bool)


def _fmt_num(v):
    if not _is_num(v):
        return ""
    return f"{round(v):,.0f}".replace(",", " ")


def _sum_nums(xs):
    return sum(x for x in (xs or []) if _is_num(x))


def _method_key(name):
    s = str(name or "").strip().lower()
    if "dcf" in s:
        return "dcf"
    if "eva" in s:
        return "eva"
    return s


def _dcf_equity(input_data):
    ve = (input_data or {}).get("valuation_engine") or {}
    dcf = ve.get("dcf") or {}
    v = dcf.get("equity_value_before_floor")
    return v if _is_num(v) else None


def _has_eva(input_data):
    ve = (input_data or {}).get("valuation_engine") or {}
    return isinstance(ve.get("eva"), dict) and bool(ve.get("eva"))


# Verohallinto's unlisted-company earnings-capitalization rate (yritysvarallisuuden
# arvostaminen perintö- ja lahjaverotuksessa). A rough public cross-check, not the
# report's primary method.
_VEROTTAJA_RATE = 0.15


def _last_num(xs):
    for x in reversed(xs or []):
        if _is_num(x):
            return x
    return None


def _avg_last(xs, k=3):
    vals = [x for x in (xs or []) if _is_num(x)][-k:]
    return (sum(vals) / len(vals)) if vals else None


def _verottaja_blocks(input_data, value):
    """DCF/EVA vs. the Finnish tax authority's substance+income valuation, as a
    reader-facing cross-check (mirrors Asiakastieto's substanssiarvo/tuottoarvo)."""
    actuals = (input_data or {}).get("actuals") or {}
    inc = actuals.get("income_statement") or {}
    bs = actuals.get("balance_sheet") or {}
    # Real Valuatum model-data keys are net_earnings / equity_excl_capital_loans;
    # the legacy net_income / equity fallbacks keep older payloads working. Without
    # this the whole cross-check silently no-opped on every production report.
    avg_ni = _avg_last(inc.get("net_earnings") or inc.get("net_income"), 3)
    equity = _last_num(bs.get("equity_excl_capital_loans") or bs.get("equity"))
    if avg_ni is None or equity is None:
        return []
    tuottoarvo = max(0.0, avg_ni) / _VEROTTAJA_RATE
    # Verohallinto counts negative net assets as 0; also keeps the cross-check from
    # printing a negative reference value for distressed companies.
    substanssiarvo = max(0.0, equity)
    kaypa = (tuottoarvo + substanssiarvo) / 2 if tuottoarvo > substanssiarvo else substanssiarvo
    # Explain floored zeros explicitly — a bare 0 next to the report's positive
    # book equity (which includes capital loans) reads as a contradiction.
    floor_notes = []
    if avg_ni < 0:
        floor_notes.append(
            "Tuottoarvo on 0, koska kolmen viime vuoden keskimääräinen "
            "nettotulos on negatiivinen."
        )
    if equity < 0:
        floor_notes.append(
            "Substanssiarvo on 0, koska oma pääoma ilman pääomalainoja on "
            f"negatiivinen ({_fmt_num(equity)} tEUR) — verottajan mallissa "
            "pääomalainat luetaan velkaan, minkä vuoksi luku poikkeaa raportin "
            "tasearvosta, jossa pääomalainat sisältyvät omaan pääomaan."
        )
    return [
        {
            "type": "table",
            "table_id": "deterministic_valuation_crosscheck",
            "title": "Arvon ristiintarkistus: DCF/EVA vs. verottajan malli",
            "unit": "tEUR",
            "columns": ["Menetelmä", "Arvo"],
            "rows": [
                ["Raportin arvo (DCF/EVA)", _fmt_num(value)],
                ["Tuottoarvo (3 v:n keskitulos / 15 %)", _fmt_num(tuottoarvo)],
                ["Substanssiarvo (oma pääoma ilman pääomalainoja)",
                 _fmt_num(substanssiarvo)],
                ["Verottajan käypä arvo", _fmt_num(kaypa)],
            ],
        },
        {
            "type": "paragraph",
            "text": (
                "Verottajan mallissa (Verohallinnon arvostusohje) tuottoarvo on "
                "kolmen viime vuoden keskimääräinen nettotulos pääomitettuna 15 %:n "
                "tuottovaatimuksella ja substanssiarvo on yhtiön oma pääoma ilman "
                "pääomalainoja. Käypä "
                "arvo on näiden keskiarvo, kun tuottoarvo ylittää substanssiarvon, "
                "muutoin substanssiarvo. Malli on karkea julkinen vertailukohta "
                "(perintö- ja lahjaverotus), ei tämän raportin ensisijainen "
                "menetelmä: DCF/EVA on tarkempi, koska se käyttää yhtiön omia "
                "ennusteita ja WACCia yksittäisen keskituloksen sijaan."
                + ((" " + " ".join(floor_notes)) if floor_notes else "")
            ),
        },
    ]


def _accepted(row):
    status = str(row.get("status") or "").lower()
    return status.startswith("hyv") or ((_num(row.get("weight_pct")) or 0) > 0)


def _num(x):
    return x if _is_num(x) else None


def _normalize_scoring(scoring, value):
    if not isinstance(scoring, dict) or not _is_num(value):
        return scoring
    methods = [m for m in scoring.get("method_scoring") or [] if isinstance(m, dict)]
    if not methods:
        return scoring

    dcf_rows = [m for m in methods if _method_key(m.get("method")) == "dcf"]
    eva_rows = [m for m in methods if _method_key(m.get("method")) == "eva"]
    if not (dcf_rows and eva_rows):
        return scoring

    for m in dcf_rows:
        m["method"] = "DCF"
        m["status"] = "hyväksytty"
        m["value_teur"] = value
        m["rationale"] = (
            "Päämenetelmä: vapaat kassavirrat ja terminaaliarvo Valuatumin "
            "DCF-moottorista."
        )

    for m in eva_rows:
        m["method"] = "EVA"
        m["status"] = "viite"
        m["weight_pct"] = 0
        m["value_teur"] = value
        m["rationale"] = (
            "Täsmäytysnäkökulma samaan ennusteeseen ja WACCiin; ei erillinen "
            "painotettava menetelmä."
        )

    weight_bearing = [m for m in methods if _method_key(m.get("method")) != "eva" and _accepted(m)]
    if not weight_bearing:
        return scoring
    raw_weights = [_num(m.get("weight_pct")) for m in weight_bearing]
    total = sum(w for w in raw_weights if _is_num(w))
    if total <= 0:
        for m in weight_bearing:
            m["weight_pct"] = 100.0 / len(weight_bearing)
    else:
        for m in weight_bearing:
            m["weight_pct"] = round(((_num(m.get("weight_pct")) or 0) / total) * 100, 6)

    weighted = 0.0
    for m in weight_bearing:
        mv = _num(m.get("value_teur"))
        mw = _num(m.get("weight_pct"))
        if mv is not None and mw is not None:
            weighted += mv * mw / 100.0
    scoring["weighted_base_case_teur"] = round(weighted, 2)
    scoring["weights_source"] = "DCF/EVA ekvivalenssi"
    return scoring


def _method_rows(value):
    val = _fmt_num(value)
    return [
        ["DCF", val, "Päämenetelmä"],
        ["EVA", val, "Täsmäytys samaan ennusteeseen ja WACCiin, ei erillinen paino"],
    ]


def _is_old_weight_table(block):
    if not isinstance(block, dict) or block.get("type") != "table":
        return False
    if block.get("_from_section7"):  # surfaced scoring table must survive re-assemble
        return False
    cols = [str(c).lower() for c in block.get("columns") or []]
    return "menetelmä" in " ".join(cols) and ("paino" in " ".join(cols) or "kontribuutio" in " ".join(cols))


def _is_old_method_chart(block):
    if not isinstance(block, dict) or block.get("type") != "chart":
        return False
    if block.get("_from_section7"):
        return False
    txt = (str(block.get("title") or "") + " " + str(block.get("chart_id") or "")).lower()
    return "menetelm" in txt or "method" in txt


def _is_old_method_paragraph(block):
    if not isinstance(block, dict) or block.get("type") != "paragraph":
        return False
    if block.get("_from_section7"):
        return False
    txt = str(block.get("text") or "").lower()
    mentions_methods = "dcf" in txt and "eva" in txt
    mentions_weighting = any(s in txt for s in ("paino", "painot", "vahvem", "korrelaatio", "menetelm"))
    return mentions_methods and mentions_weighting


def _normalize_section8(sections, input_data, value):
    if not _is_num(value):
        return
    new_blocks = [
        {
            "type": "paragraph",
            "text": (
                "DCF ja EVA ovat sama arvo kahdella eri tavalla laskettuna. DCF "
                "diskonttaa yhtiön tuottaman vapaan kassavirran pääoman "
                "tuottovaatimuksella (WACC). EVA mittaa saman asian toisin: "
                "paljonko voittoa jää sen jälkeen, kun sitoutuneelle pääomalle on "
                "veloitettu sama tuottovaatimus. Koska molemmat nojaavat samaan "
                "ennusteeseen, samaan WACCiin ja samaan pääomapohjaan, ne antavat "
                "aina saman lopputuloksen — alla ne antavat saman oman pääoman arvon."
            ),
        },
        {
            "type": "table",
            "table_id": "deterministic_dcf_eva_equivalence",
            "title": "Arvonmäärityksen päämenetelmä ja täsmäytys",
            "unit": "tEUR",
            "columns": ["Näkökulma", "Arvo", "Rooli"],
            "rows": _method_rows(value),
        },
        {
            "type": "paragraph",
            "text": (
                "DCF ja EVA perustuvat samaan ennustepolkuun, WACCiin ja "
                "velka-/kassasiltaan. Näillä samoilla oletuksilla EVA ei ole "
                "toinen riippumaton arvonmääritysmenetelmä, vaan DCF:n "
                "laskennallinen täsmäytysnäkökulma."
            ),
        },
        {
            "type": "callout",
            "variant": "info",
            "title": "Menetelmien ekvivalenssi",
            "text": (
                "Painotettu base case ei keskiarvoista DCF:ää ja EVA:a. "
                f"Raportin ankkuriarvo on {_fmt_num(value)} tEUR, ja EVA:n "
                "tehtävä on osoittaa saman ennusteen sisäinen johdonmukaisuus."
            ),
        },
    ] + _verottaja_blocks(input_data, value)
    for sec in sections:
        if not (isinstance(sec, dict) and str(sec.get("id")) == "8"):
            continue
        kept = []
        for b in sec.get("blocks") or []:
            if _is_old_weight_table(b) or _is_old_method_chart(b) or _is_old_method_paragraph(b):
                continue
            kept.append(b)
        if any(isinstance(b, dict) and b.get("table_id") == "deterministic_dcf_eva_equivalence" for b in kept):
            return
        sec["blocks"] = new_blocks + kept
        return


def _normalize_section10(sections, input_data, value):
    if not (_is_num(value) and _has_eva(input_data)):
        return
    eva = ((input_data or {}).get("valuation_engine") or {}).get("eva") or {}
    ic = eva.get("invested_capital")
    pv_exp = _sum_nums(eva.get("discounted_eva"))
    pv_term = eva.get("pv_of_trm_eva")
    cap_change = eva.get("pv_of_cap_base_change")
    raw = eva.get("equity_value_before_floor_raw")

    # Every row comes from a source field as-is. A missing component is shown
    # as missing — NEVER backsolved as `value − ic − pv_exp` so the sum "adds
    # up": that fabricated a +3 891 tEUR terminal EVA on a real report and
    # presented it as engine output.
    rows = []
    if _is_num(ic):
        rows.append(["Investoitu pääoma", _fmt_num(ic)])
    rows.append(["PV, diskontatut EVA:t (ennustejakso)", _fmt_num(pv_exp)])
    rows.append(["Jatkuvan arvon (terminaali-EVA) nykyarvo",
                 _fmt_num(pv_term) if _is_num(pv_term) else "ei saatavilla lähdedatassa"])
    if _is_num(cap_change):
        rows.append(["Pääomapohjan muutoksen nykyarvo", _fmt_num(cap_change)])
    complete = _is_num(ic) and _is_num(pv_term)
    if complete:
        total = ic + pv_exp + pv_term + (cap_change if _is_num(cap_change) else 0)
        rows.append(["Komponenttien summa", _fmt_num(total)])
    if _is_num(raw):
        rows.append(["EVA-moottorin oma arvo ennen normalisointia", _fmt_num(raw)])
    rows.append(["Oman pääoman arvo (DCF, käytetty raportissa)", _fmt_num(value)])

    if complete:
        intro = (
            "EVA rakentaa oman pääoman arvon eri suunnasta kuin DCF: yhtiöön jo "
            "sitoutunut pääoma (investoitu pääoma) plus tulevien EVA-erien "
            "nykyarvo (ennustejakso ja terminaali). Alla olevat erät ovat "
            "laskentamoottorin tuottamia sellaisenaan."
        )
    else:
        intro = (
            "EVA-erittelyn kaikkia komponentteja ei ole saatavilla lähdedatassa "
            "(terminaali-EVA:n nykyarvo puuttuu), joten täsmäytyssummaa ei "
            "esitetä. Raportin arvostus perustuu DCF-menetelmään; EVA on tässä "
            "raportissa DCF:n rinnakkaisnäkymä, ei itsenäinen menetelmäarvo."
        )
    diff_note = None
    if _is_num(raw) and abs(raw - value) > max(1.0, 0.01 * abs(value)):
        diff_note = {
            "type": "callout",
            "variant": "warning",
            "title": "EVA- ja DCF-mallin ero",
            "text": (
                f"EVA-moottorin oma arvo ({_fmt_num(raw)} tEUR) poikkeaa DCF:n "
                f"oman pääoman arvosta ({_fmt_num(value)} tEUR). Ero kertoo "
                "mallien välisestä erosta lähdedatassa — se ei ole raportissa "
                "tasattu piiloon."
            ),
        }
    blocks = [
        {"type": "paragraph", "text": intro},
        {
            "type": "table",
            "table_id": "deterministic_eva_reconciliation",
            "title": "EVA-erittely" if not complete else "EVA-täsmäytys oman pääoman arvoon",
            "unit": "tEUR",
            "columns": ["Erä", "Arvo"],
            "rows": rows,
        },
        {
            "type": "callout",
            "variant": "info",
            "title": "Mitä EVA kertoo lisää",
            "text": (
                "EVA näyttää, tuottaako yhtiö sitoutuneelle pääomalleen enemmän "
                "kuin sen tuottovaatimus (ROIC vs WACC). Positiivinen EVA "
                "tarkoittaa, että yhtiö luo arvoa vuoden aikana, negatiivinen että "
                "se tuhoaa sitä. Tämä arvonluontinäkymä ei näy pelkästä "
                "kassavirtaluvusta."
            ),
        },
    ]
    if diff_note:
        blocks.append(diff_note)
    for sec in sections:
        if isinstance(sec, dict) and str(sec.get("id")) == "10":
            sec["blocks"] = blocks
            return


def normalize_report(report, input_data):
    value = _dcf_equity(input_data)
    if not (_is_num(value) and _has_eva(input_data)):
        return report
    scoring = report.get("_scoring")
    if isinstance(scoring, dict):
        _normalize_scoring(scoring, value)
    sections = report.get("sections") or []
    _normalize_section8(sections, input_data, value)
    _normalize_section10(sections, input_data, value)
    # Underscore = renderer/QA-only. The post-assemble QA compares the cover
    # figures against this anchor (the AWAKE.AI case: cover 762 vs assembled
    # DCF/EVA anchor 1 144 shipped with ready:true and nothing noticed).
    report["_valuation_anchor_teur"] = value
    return report
