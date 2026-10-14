# Danish automatic orders

The international client uses the existing key-bound run, forecast review/edit,
report and refinement APIs. Finnish automatic orders keep their existing defaults.
Danish source selection has no Finnish or test-source fallback.

## Enablement

All of these server settings are required before the public capability becomes enabled:

- `VALUATUM_DK_API_BASE_URL`: explicit HTTPS production REST base, ending `/rest`.
- `VALUATUM_DK_TOKEN`: separately configured source credential.
- `VALUATUM_DK_ORDERS_ENABLED=1`.
- `VALUATUM_DK_FORECASTS_VERIFIED=1`: operator confirmation that estimate generation
  and import on this source have been verified. Stored test forecasts do not prove this.
- `VALUATUM_DK_SOURCE_UNIT=millions`: verified native modeldata monetary unit.
- `STRIPE_SECRET_KEY` and `INTERNATIONAL_CLIENT_SITE_URL`.

Keep orders disabled until the source forecast/import contract is verified.
The international origin must also be included in `ALLOWED_ORIGINS`.
No paid report or estimate/import call was used to verify this implementation.

## Public contract

- `GET /api/public/capabilities` returns
  `countries.DK={enabled,currency:'DKK',currencies:['DKK','EUR'],language:'en'}`.
- `GET /api/public/company-search?q=...&country=DK` queries only the Danish
  source. Eight-digit CVRs are sent without Finnish hyphen formatting. A
  source-provided `K` code is retained, never inferred.
- Before creating Stripe Checkout, call
  `GET /api/public/company-readiness?country=DK&fid=349862&code=24256790`.
  It returns only eligibility, trusted name/source code/FID, actual year,
  forecast years, native currency/unit and a reason. It reads existing source
  data, never invokes estimate generation/import. No incomplete company should
  be charged. Report currency must come from this response; there is no FX conversion.
- Verified Stripe metadata: `product='arvonmaaritys_ai_raportti'`, `country='DK'`,
  `businessId` (eight-digit CVR), `companyCode` (exact source code, possibly `K`),
  `fid`, `companyName`, `reportLanguage='en'`, `reportCurrency` (`DKK` or `EUR`),
  `customerEmail`, `forecast='true'` for forecast review.
- `POST /api/public/checkout-generate` retains its existing body with added
  `country='DK'` and `report_language='en'`. Paid company/FID/country/language,
  mode and delivery recipient are taken from verified Stripe metadata/customer
  details, then source-bound and rechecked. Replays return the same run/key.

Full source forecasts, DCF/EVA series and a source equity bridge are required.
Automatic exports verify the operating/free/FCFF cashflow chain before writing
the report. Capex sign is normalized only when the source cashflows prove which
sign reconciles, otherwise eligibility fails. Existing manual demo preparation
is separate and remains available without claiming automatic eligibility.

Forecast API `edits[].value` is **millions of source currency**; the report and
comparison rows use **thousands** (`tDKK` or `tEUR`). The factor is 1,000, not an
exchange rate. Country/currency survive edited model imports and refinements.
Customer emails are English and use the international `/report?key=...&rid=...` link.

## Known source limits (2026-10-14)

The production JYSK model resolves but its stored estimates lack complete EBIT
and DCF cashflow series. It must fail eligibility; the more complete Upcloud test
snapshot is not an acceptable production substitute. Novo Nordisk and Vestas
have different coverage, and Vestas is denominated in EUR. Source lookup alone
does not establish paid-order readiness. Production forecast generation/import
still require explicit operational verification before enablement.
