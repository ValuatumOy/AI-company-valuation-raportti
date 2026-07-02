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
    cols = [str(c).lower() for c in block.get("columns") or []]
    return "menetelmä" in " ".join(cols) and ("paino" in " ".join(cols) or "kontribuutio" in " ".join(cols))


def _is_old_method_chart(block):
    if not isinstance(block, dict) or block.get("type") != "chart":
        return False
    txt = (str(block.get("title") or "") + " " + str(block.get("chart_id") or "")).lower()
    return "menetelm" in txt or "method" in txt


def _is_old_method_paragraph(block):
    if not isinstance(block, dict) or block.get("type") != "paragraph":
        return False
    txt = str(block.get("text") or "").lower()
    mentions_methods = "dcf" in txt and "eva" in txt
    mentions_weighting = any(s in txt for s in ("paino", "painot", "vahvem", "korrelaatio", "menetelm"))
    return mentions_methods and mentions_weighting


def _normalize_section8(sections, value):
    if not _is_num(value):
        return
    new_blocks = [
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
    ]
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
    pv_term = value - (ic if _is_num(ic) else 0) - pv_exp
    rows = []
    if _is_num(ic):
        rows.append(["Investoitu pääoma", _fmt_num(ic)])
    rows.append(["PV, diskontatut EVA:t (ennustejakso)", _fmt_num(pv_exp)])
    rows.append(["Jatkuvan arvon (terminaali-EVA) nykyarvo", _fmt_num(pv_term)])
    rows.append(["Oman pääoman arvo ennen lattiaa", _fmt_num(value)])
    blocks = [
        {
            "type": "paragraph",
            "text": (
                "EVA esitetään täsmäytyksenä samaan arvoon kuin DCF, koska "
                "molemmat nojaavat samaan ennusteeseen ja pääomakustannukseen."
            ),
        },
        {
            "type": "table",
            "table_id": "deterministic_eva_reconciliation",
            "title": "EVA-täsmäytys oman pääoman arvoon",
            "unit": "tEUR",
            "columns": ["Erä", "Arvo"],
            "rows": rows,
        },
    ]
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
    _normalize_section8(sections, value)
    _normalize_section10(sections, input_data, value)
    return report
