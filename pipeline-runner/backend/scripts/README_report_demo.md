# English demo preparation

`prepare_report_demo.py` reads saved source snapshots and writes an admin run
request plus a readiness manifest. It makes no network calls, initializes no
database, and never starts generation or regenerates estimates. `APP_TOKEN`
and provider credentials are unnecessary for preparation.

Run from `pipeline-runner/backend`:

```powershell
python -X utf8 scripts/prepare_report_demo.py `
  --input <saved-modeldata.json> --input-format valuatum-model `
  --source-metadata <saved-source-metadata.json> `
  --source-host upcloudtest.valuatum.com `
  --pipeline-id <existing-local-single-writer-pipeline-id> `
  --country-code DK --source-currency DKK --report-language en `
  --output <jysk-en-request.json>
```

For the selected Amazon SEC snapshot, use `--input-format sec-companyfacts`,
`--country-code US`, and `--source-currency USD`. The SEC adapter retains
historical facts and provenance. It does not infer aggregate debt, EBITDA,
forecasts, tax rates, WACC or DCF values.

The request uses the existing admin `RunIn` contract: supplied `input_data`,
`params.report_language`, `params.country_code`, and `params.source_currency`.
Defaults remain Finnish, Finland and EUR. Source currency and monetary units
must agree. Raw Valuatum or reviewed SEC amounts in millions are multiplied
by 1,000 to thousands of **the same source currency**, with no FX conversion.
Canonical input metadata is `meta.report_language`, `meta.country_code`,
`meta.currency` and `meta.unit`; the top-level `unit` is `tEUR`, `tDKK` or
`tUSD`. Legacy keys such as `value_teur` retain their names for compatibility
and contain thousands of the declared source currency.

Preparation checks recent actuals (default: latest year at least current
year minus two). The manifest separately checks source forecast revenue/EBIT,
WACC and DCF value availability. `numerical_input_ready` is a completeness
check. It does not mean approval, verified valuation quality or production
source verification. Inspect the source-country and endpoint provenance:
JYSK currently comes from the Danish dataset at `upcloudtest.valuatum.com`.
Amazon's selected SEC facts lack a current forecast/WACC/DCF source and are
not ready for a full valuation. Do not fill those gaps with Wisdom's stale
2018 actuals or invented assumptions.

After explicit approval, a local backend can run the prepared request through
the existing admin pipeline with a new isolated SQLite database and the real
local single-writer pipeline ID. Keep `DATABASE_URL` unset. Local admin auth
is disabled when `APP_TOKEN` is unset; provider credentials are still required
for paid research/writing. Keep `RUNS_PAUSED=1` until execution is approved.
Do not submit a prepared request with an arbitrary placeholder pipeline ID.
Foreign demos require supplied normalized data: automatic Finland export and
Finnish peer retrieval are disabled for foreign country/currency contexts.

The writer receives per-run English schema adaptation and a provider system
instruction, including correction/truncation retries. Stored prompts and
validators remain unchanged, so a reseed is unnecessary. English output
quality still needs an explicitly approved generation and human review.
