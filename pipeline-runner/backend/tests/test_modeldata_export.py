"""Stage-0 actuals now come entirely from /rest/modeldata.

These fields used to be null on every export because the exporter asked for
varNames Valuatum does not have (`personnel_costs`, `cash_and_equivalents`,
`capital_loans`, `dep_total_nega`, `non_interest_bearing_debt`, …). /modeldata
silently drops unknown varNames, so the holes were filled by a second Profinder
MCP integration keyed by company_code — which is what made konserni runs able to
pick up parent-level statements. The canonical names are cr_-prefixed; the
values below were verified against that MCP backfill (fid 184362, 9 years)
before it was deleted.
"""

from valuatum_kit import export_modeldata_json as export


def _model(**data_map):
    """Minimal /modeldata response: 2024 actual, 2025 the first estimate year."""
    return {
        "followedModelId": 184362,
        "companyName": "Valuatum Oy",
        "companyCode": "16123988",
        "currency": "EUR",
        "currentYear": 2025,
        "dataMap": {"2024": data_map, "2025": {"ns": 0.5}},
    }


def _actuals(**data_map):
    payload = export.build_payload(_model(**data_map), None)
    return payload["actuals"]


def test_income_statement_reads_canonical_cr_varnames():
    inc = _actuals(
        ns=0.421,
        cr_other_operating_income=0.001,
        cr_gross_profit=0.396,
        cr_employee_expenses=-0.263,
        cr_other_oper_expenses=-0.09,
        cr_depreciation=-0.003,
        cr_interest_expenses=-0.028,
        cr_net_earnings=0.037,
    )["income_statement"]

    assert inc["net_sales"] == [421]
    assert inc["other_operating_income"] == [1]
    assert inc["gross_profit"] == [396]
    assert inc["personnel_costs"] == [-263]
    assert inc["other_operating_costs"] == [-90]
    assert inc["depreciation_total"] == [-3]
    assert inc["interest_expenses"] == [-28]
    assert inc["net_earnings"] == [37]


def test_balance_sheet_reads_canonical_cr_varnames():
    bs = _actuals(
        cr_development_expenditure=0.011,
        cr_intangible_assets_total=0.02,
        cr_tangibles_assets_total=0.027,
        inventories=0.556,
        cr_curr_trade_debtors=0.016,
        cr_cash_and_bank_deposits=0.04,
        bs_total_assets=0.678,
        cr_shareholders_equity=0.159,
        fundu_equity_incl_cap_loans=0.405,
        cr_current_trade_creditors=0.011,
        liab_ib_total=0.225,
        non_interest_bearing_liabilities_calc=0.048,
    )["balance_sheet"]

    assert bs["development_costs"] == [11]
    assert bs["intangibles_total"] == [20]
    assert bs["tangible_assets"] == [27]
    assert bs["inventories"] == [556]
    assert bs["trade_receivables"] == [16]
    assert bs["cash_and_equivalents"] == [40]
    assert bs["total_assets"] == [678]
    assert bs["equity_excl_capital_loans"] == [159]
    assert bs["equity_incl_capital_loans"] == [405]
    assert bs["trade_payables"] == [11]
    assert bs["non_interest_bearing_debt"] == [48]


def test_split_balance_rows_are_summed():
    """Valuatum splits these into current + non-current; the report wants both."""
    bs = _actuals(
        cr_capital_loan_lt=0.2,
        cr_capital_loan_st=0.046,
        cr_non_current_loans_from_credit_ins=0.02,
        cr_current_loans_from_credit_ins=0.008,
        cr_non_current_advances_received=0.003,
        cr_current_advances_received=0.002,
    )["balance_sheet"]

    assert bs["capital_loans"] == [246]
    assert bs["loans_from_fin_institutions"] == [28]
    assert bs["advances_received"] == [5]


def test_split_row_survives_a_missing_half():
    """A company with only the current half must still get a value, not None."""
    bs = _actuals(cr_current_loans_from_credit_ins=0.028)["balance_sheet"]
    assert bs["loans_from_fin_institutions"] == [28]


def test_split_row_is_none_when_every_component_is_missing():
    bs = _actuals(ns=0.421)["balance_sheet"]
    assert bs["capital_loans"] == [None]
    assert bs["loans_from_fin_institutions"] == [None]


def test_interest_bearing_debt_excludes_capital_loans():
    """liab_ib_total, not interest_bearing_liabilities_calc.

    The two differ by exactly the capital loans. The valuation engine treats a
    capital loan as equity-like (singlewriter.txt rule 25), so using the
    including-variant would double-count it in the net-debt bridge.
    """
    bs = _actuals(
        liab_ib_total=0.225,
        interest_bearing_liabilities_calc=0.471,
        cr_capital_loan_lt=0.246,
    )["balance_sheet"]

    assert bs["interest_bearing_debt"] == [225]
    assert bs["capital_loans"] == [246]


def test_konserni_level_survives_a_company_code_override():
    """A K-stripped override must not relabel a consolidated model as emo.

    upsert_company stores meta.y_tunnus, which has the K stripped, and the admin
    UI feeds that straight back as an override on the next fetch — which used to
    flip meta.level to 'parent' on a konserni model whose forecasts stayed
    consolidated.
    """
    assert export.apply_level_suffix("16123988", "16123988K") == "16123988K"
    assert export.apply_level_suffix("16123988K", "16123988K") == "16123988K"
    # A parent model never gains a suffix it did not have.
    assert export.apply_level_suffix("16123988", "16123988") == "16123988"
    # Correcting a genuinely wrong code still works; only the suffix is pinned.
    assert export.apply_level_suffix("24388345", "16123988K") == "24388345K"


def test_meta_level_follows_the_fetched_model_not_the_override():
    model = _model(ns=0.421)
    model["companyCode"] = export.apply_level_suffix("16123988", "16123988K")
    payload = export.build_payload(model, None)

    assert payload["meta"]["level"] == "consolidated"
    assert payload["meta"]["y_tunnus"] == "1612398-8"


# --- credit risk -------------------------------------------------------------
# Was a Profinder MCP call keyed by company code. It ignored the K suffix and
# answered with one fixed entity: "16123988" and "16123988K" both returned
# companyId 95721 with identical figures, and those matched the EMO /modeldata
# series exactly — so konserni reports carried emo credit risk. /modeldata is
# keyed by fid, so the question cannot arise.

def test_credit_risk_reads_modeldata_and_scales_to_percent():
    payload = export.build_payload(
        _model(
            brm_ValuBooster2=0.00964645,
            industryRiskBankruptcy=0.00604915,
            **{"text_brc_Kirjain_luokitus_front-brm_ValuBooster2": "BB"},
            cr_credit_score_vb2=17.0,
        ),
        None,
    )
    cr = payload["credit_risk"]

    assert cr["available"] is True
    assert cr["years"] == [2024]
    # Both risk variables arrive as fractions; the schema and prompts want percent.
    assert cr["company_bankruptcy_risk_pct"] == [0.964645]
    assert cr["industry_bankruptcy_risk_pct"] == [0.604915]
    assert cr["rating"] == ["BB"]
    assert cr["credit_score"] == [17]


def test_industry_risk_is_populated_not_hardcoded_null():
    """It was `[None for _ in rows]` for as long as the block existed."""
    cr = export.build_payload(_model(industryRiskBankruptcy=0.00604915), None)["credit_risk"]
    assert cr["industry_bankruptcy_risk_pct"] == [0.604915]


def test_rating_survives_the_numeric_coercion():
    """arr() runs values through as_num, which would turn "BBB" into None."""
    cr = export.build_payload(
        _model(**{"text_brc_Kirjain_luokitus_front-brm_ValuBooster2": "BBB"}), None
    )["credit_risk"]
    assert cr["rating"] == ["BBB"]


def test_credit_risk_unavailable_when_the_model_has_none():
    cr = export.build_payload(_model(ns=0.421), None)["credit_risk"]
    assert cr["available"] is False
    assert cr["years"] == []


def test_payment_defaults_ride_along_even_when_risk_is_missing():
    defaults = [{"type": "protestoitu_verovelka", "date": "2025-01-01", "sum": 2400.0}]
    cr = export.build_payload(_model(ns=0.421), defaults)["credit_risk"]
    assert cr["available"] is False
    assert cr["payment_defaults"] == defaults


def test_payment_defaults_taken_from_whichever_entry_has_them(monkeypatch):
    """/creditrisk answers with every company in the hierarchy.

    Picking is a non-issue: payment defaults belong to the company code, so the
    emo and konserni entries carry identical ones. Everything that DOES differ
    between the two comes from /modeldata by fid.
    """
    rows = [
        {"companyId": 304002, "followedModelId": 256219, "isGroupCompany": True,
         "paymentDefaults": []},
        {"companyId": 95721, "followedModelId": 184362, "isGroupCompany": False,
         "paymentDefaults": [{"type": "protestoitu_verovelka", "sum": 2400.0}]},
    ]
    monkeypatch.setattr(export, "_creditrisk_rows", lambda code, token: rows)

    assert export.rest_payment_defaults("16123988", "tok") == [
        {"type": "protestoitu_verovelka", "sum": 2400.0}
    ]


def test_payment_defaults_survive_a_company_missing_from_the_database(monkeypatch):
    """Minimal response: defaults present, followedModelId null (openapi.yaml:1229).

    Filtering by fid would drop these, which is why we do not.
    """
    rows = [{"companyCode": "16123988", "companyId": None, "followedModelId": None,
             "paymentDefaults": [{"type": "yksipuolinen_tuomio", "sum": 1200.5}]}]
    monkeypatch.setattr(export, "_creditrisk_rows", lambda code, token: rows)

    assert export.rest_payment_defaults("16123988", "tok") == [
        {"type": "yksipuolinen_tuomio", "sum": 1200.5}
    ]


def test_payment_defaults_empty_when_no_entry_has_any(monkeypatch):
    rows = [{"followedModelId": 184362, "paymentDefaults": []},
            {"followedModelId": 256219, "paymentDefaults": []}]
    monkeypatch.setattr(export, "_creditrisk_rows", lambda code, token: rows)
    assert export.rest_payment_defaults("16123988", "tok") == []
