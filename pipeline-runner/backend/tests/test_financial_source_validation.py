import asyncio
import copy

import pytest

from app import debt_quality, fiscal_periods, runner, valuatum
from valuatum_kit import export_modeldata_json as export


def _model():
    return {
        'followedModelId': 987654, 'companyName': 'Test Oy', 'companyCode': '12345678',
        'currency': 'EUR', 'currentYear': 2026,
        'dataMap': {
            '2023': {'cr_fiscal_year_start': 20230701, 'cr_fiscal_year_end': 20240630,
                     'ns': 1.1, 'ebit': -0.1, 'bs_total_assets': 5.8},
            '2024': {'cr_fiscal_year_start': 20240701, 'cr_fiscal_year_end': 20250630,
                     'ns': 2.2, 'ebit': -0.2, 'bs_total_assets': 10.5},
            '2025': {'cr_fiscal_year_start': 20250701, 'cr_fiscal_year_end': 20251231,
                     'ns': 3.3, 'ebit': 0.3, 'bs_total_assets': 15.5,
                     'liab_ib_total': 2.2, 'interest_bearing_liabilities_calc': 5.0,
                     'cr_capital_loan_lt': 2.5},
            '2026': {'ns': 4.8, 'ib_debt_nega_prev_year': -2.2},
        },
    }


def test_export_retains_source_periods_and_separate_debt_totals():
    assert {'cr_fiscal_year_start', 'cr_fiscal_year_end',
            'interest_bearing_liabilities_calc'} <= set(export.EXTRA_VARS)
    p = export.build_payload(_model())
    assert p['actuals']['periods'] == [
        {'model_year': 2023, 'start': '2023-07-01', 'end': '2024-06-30'},
        {'model_year': 2024, 'start': '2024-07-01', 'end': '2025-06-30'},
        {'model_year': 2025, 'start': '2025-07-01', 'end': '2025-12-31'},
    ]
    assert p['actuals']['income_statement']['net_sales'] == [1100, 2200, 3300]
    assert p['actuals']['balance_sheet']['interest_bearing_debt_source_total'][-1] == 5000
    assert p['meta']['scenario_timing_version'] == 2


def test_period_gate_catches_year_mislabel_and_seven_month_period():
    p = export.build_payload(_model())
    errors = fiscal_periods.blockers(p)
    assert any('2024-07-01–2025-06-30' in s and '2024' in s for s in errors)
    assert any('2025-07-01–2025-12-31' in s and '184 päivää' in s for s in errors)
    with pytest.raises(ValueError, match='tilikausi'):
        fiscal_periods.require_comparable(p)


def test_ordinary_annual_period_and_missing_empty_history_are_allowed():
    p = {'actuals': {'years': [2024, 2025], 'periods': [
        {'model_year': 2024, 'start': None, 'end': None},
        {'model_year': 2025, 'start': '2025-01-01', 'end': '2025-12-31'}],
        'income_statement': {'net_sales': [None, 100]}}}
    assert fiscal_periods.blockers(p) == []
    p['actuals']['periods'][1]['start'] = None
    assert fiscal_periods.blockers(p)


def test_debt_gate_catches_omitted_amount_without_inventing_a_correction():
    p = export.build_payload(_model())
    before = copy.deepcopy(p)
    assert any('300.0' in s for s in debt_quality.blockers(p))
    with pytest.raises(ValueError, match='velat vaativat tarkistuksen'):
        debt_quality.require_review(p)
    assert p == before
    p['actuals']['balance_sheet']['interest_bearing_debt_source_total'][-1] = 4700
    assert debt_quality.blockers(p) == []


def test_debt_gate_does_not_mix_different_years():
    data = {'actuals': {'years': [2024, 2025], 'balance_sheet': {
        'interest_bearing_debt_source_total': [5000, None], 'capital_loans': [2500, 200]}},
        'valuation_engine': {'dcf': {'bridge': {'interest_bearing_debt': -2200}}}}
    assert debt_quality.blockers(data) == []


def test_legacy_run_period_lookup_verifies_source_before_attaching_dates(monkeypatch):
    model = _model()
    saved = export.build_payload(model)
    del saved['actuals']['periods']
    del saved['actuals']['balance_sheet']['interest_bearing_debt_source_total']
    calls = []

    async def get_model(fids, poses):
        calls.append((fids, poses))
        return {'987654': model}

    monkeypatch.setattr(valuatum, 'modeldata', get_model)
    result = asyncio.run(fiscal_periods.add_legacy_source_periods(saved, 987654))
    assert calls and calls[0][0] == [987654]
    assert result['actuals']['periods'][-1]['start'] == '2025-07-01'
    assert debt_quality.blockers(result)
    assert fiscal_periods.blockers(result)


def test_legacy_periods_cannot_be_attached_to_different_financial_snapshot(monkeypatch):
    model = _model()
    saved = export.build_payload(model)
    del saved['actuals']['periods']
    model['dataMap']['2025']['ns'] = 3.0

    async def get_model(*args):
        return {'987654': model}

    monkeypatch.setattr(valuatum, 'modeldata', get_model)
    with pytest.raises(ValueError, match='eivät vastaa'):
        asyncio.run(fiscal_periods.add_legacy_source_periods(saved, 987654))


def test_stage_zero_rejects_uncomparable_data_before_any_writer():
    stage = {'id': 'stage-0', 'order': 0, 'name': 'FAKTAT',
             'model': runner.DATA_FETCHER_MODEL, 'prompt_template': ''}
    data = export.build_payload(_model())
    result = asyncio.run(runner._execute_stage(stage, {}, data, None, {}, rid='local'))
    assert result['status'] == 'error'
    assert 'tilikausi' in result['error_message'].lower()
    assert result['tokens_prompt'] == 0


def test_preflight_stops_before_estimate_generation_on_irregular_period(monkeypatch):
    monkeypatch.setenv('VALUATUM_TOKEN', 'dummy')
    model = _model()

    async def source(*args, **kwargs):
        return {'987654': model}

    async def forbidden(*args, **kwargs):
        pytest.fail('estimaattien generointia ei saa käynnistää epäkelvolla pohjalla')

    monkeypatch.setattr(valuatum, 'modeldata', source)
    monkeypatch.setattr(valuatum.estimate_trigger, 'trigger_and_wait', forbidden)

    async def collect():
        return [event async for event in valuatum.export_stream('Test Oy', 987654)]

    events = asyncio.run(collect())
    assert [event['step'] for event in events] == ['error']
    assert '184 päivää' in events[0]['message']


def test_legacy_refinement_stops_before_first_model_call(monkeypatch):
    model = _model()
    saved = export.build_payload(model)
    del saved['actuals']['periods']
    existing = {'results': [{'order': 0, 'status': 'ok', 'parsed_json': saved}]}
    writes, statuses = [], []

    async def source(*args):
        return {'987654': model}

    async def forbidden_chat(**kwargs):
        pytest.fail('LLM-kutsua ei saa tehdä, kun vanhan ajon tilikaudet eivät täsmää')

    monkeypatch.setattr(valuatum, 'modeldata', source)
    monkeypatch.setattr(runner.openrouter, 'chat', forbidden_chat)
    monkeypatch.setattr(runner.store, 'get_run', lambda rid: existing)
    monkeypatch.setattr(runner.store, 'upsert_result', lambda rid, result: writes.append(result))
    monkeypatch.setattr(runner.store, 'set_run_status', lambda rid, status: statuses.append(status))
    stages = [{'id': 'zero', 'order': 0, 'name': 'FAKTAT'},
              {'id': 'one', 'order': 1, 'name': 'Enrichment'}]
    run = {'id': 'local', 'identifier': 987654, 'params': {}, 'stop_on_failure': False}

    async def execute():
        return [event async for event in runner._run_stages(run, stages, from_order=1)]

    events = asyncio.run(execute())
    assert statuses == ['error']
    assert writes[0]['order'] == 1 and '184 päivää' in writes[0]['error_message']
    assert events[0]['status'] == 'error'


def test_writer_directive_uses_engine_timing_and_keeps_target_year_distinct():
    p = export.build_payload(_model())
    p['valuation_engine']['wacc_parameters']['wacc_pct'] = 10
    p['valuation_engine']['dcf']['years'] = [2026, 2027]
    p['valuation_engine']['dcf']['fcff'] = [100, 200]
    p['valuation_engine']['dcf']['discounted_fcff'] = [100 / 1.1 ** .25, 200 / 1.1 ** 1.25]
    directive = runner._valuation_integrity_directive(p)
    assert '2026' in directive and '0.25' in directive
    assert 'not evidence of a later terminal-year revenue' in directive
    assert 'raw EVA' in directive
    assert 'never subtract it again' in directive
