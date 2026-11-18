# Swedish automatic orders

Sweden uses the same contract as Denmark (`DANISH_ORDERS.md`), with its own
source and gates. The order countries and their rules live in
`ORDER_COUNTRIES` in `valuatum_kit/config.py`. There is no Finnish fallback.

## Enablement

- `VALUATUM_SE_API_BASE_URL=https://sweden.valuatum.com/rest` (asp 116,
  company-valuation-se) and `VALUATUM_SE_TOKEN`.
- `VALUATUM_SE_ORDERS_ENABLED=1`.
- `VALUATUM_SE_FORECASTS_VERIFIED=1` only after one forecast import has been
  run and checked on asp 116. Estimate generation was verified on 2026-11-18
  (Husqvarna Stockholm AB, fid 52623); import has not been run yet.
- `VALUATUM_SE_SOURCE_UNIT=millions`. Checked on 2026-11-18: H & M Hennes &
  Mauritz GBC AB net sales 121 023 (MSEK) and Husqvarna Stockholm AB 25.0.
- `STRIPE_SECRET_KEY` and `INTERNATIONAL_CLIENT_SITE_URL`, as for Denmark.

## Differences from Denmark

- Identity is the organisation number, stored by the source as `556983-9805`.
  Customers may type `5569839805`, `556983-9805` or `SE5569839805`; all
  normalise to the hyphenated form, which is also the Stripe `businessId`.
- Currency is SEK only.
- Most Swedish models carry no estimates until generated (flat forecasts and no
  cashflows). `GET /api/public/company-readiness?country=SE` therefore runs
  estimate generation on the selected model before checking it, which takes
  about 40 seconds. `checkout-generate` rechecks without generating again;
  stage 0 generates once more before export, as for every order.
- Years before the first filed statement hold only valuation parameters (beta,
  WACC). The exporter drops them for all source-verified countries, so reports
  start from the first year with net sales or total assets.

## Known source limits (2026-11-18)

Coverage is mostly small and medium-sized companies; large listed groups are
not in the source. Husqvarna Stockholm AB passes readiness but has negative DCF
equity (about −58 MSEK), so its report falls back to the floor rule.
