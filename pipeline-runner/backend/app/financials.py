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
        rows = _rows(source.get(key) or {}, spec, n)
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
    """Actual years first, then the engine's own forecast years (marked "e").

    The forecast statements only exist on runs exported after 2026-08-27; older
    runs simply get the actuals half.
    """
    data = input_data or {}
    return (_statement_blocks(data.get("actuals"), "", "", "")
            + _statement_blocks(data.get("forecast"), "e", " (ennuste)", "_forecast"))
