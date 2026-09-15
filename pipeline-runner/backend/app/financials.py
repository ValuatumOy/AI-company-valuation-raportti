"""Deterministic financial-statement tables (tuloslaskelma + taseen päärivit).

`actuals.income_statement` / `actuals.balance_sheet` are already fetched for
every actual year the model has (up to /modeldata's 9) but nothing ever
rendered them — the writer only produced a hand-picked key-figure table. These
blocks put the full statements in the report straight from the export, so the
numbers are the model's, never the LLM's.

A row with no data in any year is dropped rather than shown empty. The balance
sheet is a selection of the export's rows, not a balancing statement — hence
"päärivit"; vastaavaa does not foot to vastattavaa here.
"""

INCOME_ROWS = [
    ("Liikevaihto", "net_sales"),
    ("Liiketoiminnan muut tuotot", "other_operating_income"),
    ("Myyntikate", "gross_profit"),
    ("Henkilöstökulut", "personnel_costs"),
    ("Liiketoiminnan muut kulut", "other_operating_costs"),
    ("Käyttökate (EBITDA)", "ebitda"),
    ("Poistot ja arvonalentumiset", "depreciation_total"),
    ("Liiketulos (EBIT)", "ebit"),
    ("Liiketulos ilman kertaeriä", "ebit_without_extras"),
    ("Kertaerät liiketuloksessa", "extras_in_ebit"),
    ("Korkokulut", "interest_expenses"),
    ("Nettotulos", "net_earnings"),
]

BALANCE_ROWS = [
    ("Kehittämismenot", "development_costs"),
    ("Aineettomat hyödykkeet", "intangibles_total"),
    ("Aineelliset hyödykkeet", "tangible_assets"),
    ("Vaihto-omaisuus", "inventories"),
    ("Myyntisaamiset", "trade_receivables"),
    ("Rahat ja pankkisaamiset", "cash_and_equivalents"),
    ("Taseen loppusumma", "total_assets"),
    ("Oma pääoma (ilman pääomalainoja)", "equity_excl_capital_loans"),
    ("Oma pääoma (sis. pääomalainat)", "equity_incl_capital_loans"),
    ("Pääomalainat", "capital_loans"),
    ("Lainat rahoituslaitoksilta", "loans_from_fin_institutions"),
    ("Lainat omistajayhteisöiltä", "loans_from_associated"),
    ("Saadut ennakot", "advances_received"),
    ("Ostovelat", "trade_payables"),
    ("Korolliset velat", "interest_bearing_debt"),
    ("Korottomat velat", "non_interest_bearing_debt"),
]


def _is_num(x):
    return isinstance(x, (int, float)) and not isinstance(x, bool)


def _fmt_num(v):
    if not _is_num(v):
        return ""
    sign = "-" if v < 0 else ""
    return f"{sign}{round(abs(v)):,.0f}".replace(",", " ")


def _rows(block, spec, n):
    out = []
    for label, key in spec:
        values = block.get(key)
        values = values if isinstance(values, list) else []
        cells = [_fmt_num(values[i] if i < len(values) else None) for i in range(n)]
        if any(cells):
            out.append([label] + cells)
    return out


def _drop_empty_years(years, rows):
    """A company younger than the 9 requested years has leading columns that
    are empty on every row (NoCFO: 2017-2020). Drop those years entirely."""
    keep = [i for i in range(len(years))
            if any(r[i + 1] for r in rows)]
    return ([years[i] for i in keep],
            [[r[0]] + [r[i + 1] for i in keep] for r in rows])


def _statement_blocks(source, suffix, title_suffix, id_suffix):
    years = (source or {}).get("years")
    if not isinstance(years, list) or not years:
        return []
    n = len(years)
    blocks = []
    for spec, key, title, table_id in (
        (INCOME_ROWS, "income_statement", "Tuloslaskelma", "deterministic_income_statement"),
        (BALANCE_ROWS, "balance_sheet", "Taseen päärivit", "deterministic_balance_sheet"),
    ):
        # Runs exported before 2026-08-27 have no forecast statements, but
        # their forecast block carries net_sales / ebitda / ebit / equity /
        # interest_bearing_debt under those same names — so an old run still
        # gets a per-year forecast table, just a shorter one.
        rows = _rows(source.get(key) or source, spec, n)
        shown, rows = _drop_empty_years(years, rows)
        columns = ["Erä"] + [f"{y}{suffix}" for y in shown]
        if rows:
            blocks.append({
                "type": "table",
                "table_id": table_id + id_suffix,
                "title": title + title_suffix,
                "unit": "tEUR",
                "columns": columns,
                "rows": rows,
            })
    return blocks


def build_financial_statement_blocks(input_data):
    """Blocks per section id: the actual years belong to section 5 (history),
    the forecast years (marked "e") to section 6 (ennusteet)."""
    data = input_data or {}
    return {
        "5": _statement_blocks(data.get("actuals"), "", "", ""),
        "6": _statement_blocks(data.get("forecast"), "e", " (ennuste)", "_forecast"),
    }


# ------------------------------------------------------------ forecast origin

def _pct(v):
    return f"{v:.1f}".replace(".", ",") + " %"


def _last_num(xs):
    for v in reversed(xs or []):
        if _is_num(v):
            return v
    return None


def build_forecast_origin_block(input_data, params=None):
    """One deterministic callout at the top of §6 saying where the forecast
    comes from and why the margin moves the way it does.

    Verified on every prod run to date (2026-09-15): the system forecast is a
    linear EBIT-margin glide from the first forecast year to a per-company
    endpoint, and ROIC computed from the EVA block (noplat / (|cost_of_capital|
    / WACC)) lands near WACC at the end of the period — the excess return is
    competed away. Where it lands clearly BELOW WACC (Apogee: 49 % -> 5,3 %),
    the capital base grew by retained earnings during the period while the
    margin path was not re-solved for it; the paragraph says so from the same
    numbers. The one paying customer's one complaint was that the report never
    explained any of this.
    """
    data = input_data or {}
    f = data.get("forecast") or {}
    years = f.get("years") if isinstance(f.get("years"), list) else []
    m = [v for v in (f.get("ebit_pct") or []) if _is_num(v)]
    if not years or len(m) < 2:
        return None
    y0, yn = years[0], years[-1]
    p = params or {}
    edited = bool(p.get("forecast_edits") or p.get("forecast_changes"))
    text_id = "deterministic_forecast_origin"
    if edited:
        return {
            "type": "callout", "variant": "info", "table_id": text_id,
            "title": "Mistä ennuste tulee",
            "text": (
                "Liikevaihto- ja EBIT-ennuste on tilaajan antama: Valuatumin "
                "perusuraa on muokattu tilaajan omilla luvuilla, ja laskentamoottori "
                "on johtanut niistä muut rivit (kassavirta, tase, verot). "
                f"Viimeisen ennustevuoden {yn} EBIT-marginaali {_pct(m[-1])} jatkuu "
                "terminaalissa muuttumattomana, joten se painaa arvossa enemmän kuin "
                "yksikään muu yksittäinen vuosi."
            ),
        }

    act = data.get("actuals") or {}
    ays = act.get("years") if isinstance(act.get("years"), list) else []
    ans = (act.get("income_statement") or {}).get("net_sales") or []
    aeb = (act.get("income_statement") or {}).get("ebit") or []
    last_act = None
    if ays and len(ans) == len(ays) and len(aeb) == len(ays) and _is_num(ans[-1]) and ans[-1] and _is_num(aeb[-1]):
        last_act = (ays[-1], aeb[-1] / ans[-1] * 100)

    ve = data.get("valuation_engine") or {}
    wacc = (ve.get("wacc_parameters") or {}).get("wacc_pct")
    eva = ve.get("eva") or {}
    roic = cap = None
    if _is_num(wacc) and wacc:
        nop = eva.get("noplat") or []
        coc = eva.get("cost_of_capital") or []
        pairs = [(n, abs(c) / (wacc / 100)) for n, c in zip(nop, coc)
                 if _is_num(n) and _is_num(c) and c]
        if len(pairs) >= 2:
            roic = (pairs[0][0] / pairs[0][1] * 100, pairs[-1][0] / pairs[-1][1] * 100)
            cap = (pairs[0][1], pairs[-1][1])

    parts = [
        "Ennuste on Valuatumin laskentamoottorin deterministinen perusura — ei "
        "tekoälyn eikä yhtiön johdon näkemys."
    ]
    if last_act:
        parts.append(f"Lähtökohta on viimeisin toteutunut tilikausi {last_act[0]} "
                     f"(EBIT-% {_pct(last_act[1])}).")
    steps = [b - a for a, b in zip(m, m[1:])]
    linear = (max(steps) - min(steps)) < 0.5 and all((x < 0) == (steps[0] < 0) for x in steps)
    verb = ("laskee" if m[-1] < m[0] else "nousee") + (" tasaisesti" if linear else "")
    parts.append(f"EBIT-marginaali {verb} {_pct(m[0])} ({y0}) → {_pct(m[-1])} ({yn}), "
                 f"ja viimeisen vuoden taso jatkuu terminaalissa muuttumattomana.")
    if _is_num(wacc):
        parts.append("Uran taustalla on mallin yleinen oletus, että pääoman tuotto lähestyy "
                     f"pääoman kustannusta (WACC {_pct(wacc)}): ylituotto ei säily "
                     "ikuisesti, koska kilpailu syö sen — eikä alituotto jää pysyväksi.")
    if roic:
        parts.append(f"Sijoitetun pääoman tuotto (NOPLAT / pääomakanta) on ennusteen alussa "
                     f"{_pct(roic[0])} ja lopussa {_pct(roic[1])}.")
        if _is_num(wacc) and roic[1] < wacc - 1.0 and cap and cap[1] > cap[0] * 1.2:
            parts.append("Ura päätyy pääoman kustannuksen alapuolelle, koska mallin "
                         f"pääomakanta kasvaa ennustejaksolla {_fmt_num(cap[0])} → "
                         f"{_fmt_num(cap[1])} tEUR kertyvistä voittovaroista eikä "
                         "marginaaliuraa lasketa uudelleen kasvaneelle pääomalle.")
    parts.append("Tämä on mallin oletus, ei yhtiökohtainen näkemys: sen voi korvata omalla "
                 "ennusteella, jolloin oma viimeisen vuoden marginaali jatkuu terminaalissa.")
    return {"type": "callout", "variant": "info", "table_id": text_id,
            "title": "Mistä ennuste tulee", "text": " ".join(parts)}
