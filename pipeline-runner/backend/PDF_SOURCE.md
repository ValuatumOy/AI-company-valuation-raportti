# PDF-service source (Norway first)

The PDF service (`pdfanalysis.valuatum.com`) builds a Valuatum model from an
uploaded financial statement, with generated estimates and a DCF. That makes it
a source for companies of any country the service can read. Norway was verified
on 2026-11-18: the service read a scanned, image-only Brønnøysund annual
accounts PDF for 2TAL AS correctly (EBIT, net earnings, equity, total assets,
cash and revenue matched the register) and produced 2026e–2028e estimates.

## Configuration

- `VALUATUM_PDF_API_BASE_URL`: explicit HTTPS production REST base, ending `/rest`.
- `VALUATUM_PDF_TOKEN`: read-only REST credential for the PDF service.

Source and country are separate. The source is `PDF`; the company's country
(`NO`, later `SE` or `DE`) and the model's own currency (`NOK`, `SEK`, `EUR`)
go into the report context. Supported locales live in `valuatum_kit/config.py`.

## Read-only

PDF-service models are customers' own uploads. Estimate generation and forecast
import refuse the `PDF` source (`require_writable_source`), and every forecast
edit endpoint refuses runs whose country is not FI or DK (`_forecast_source`), so
a Norwegian run's model id never reaches an import. Forecast-edit refinement
rounds are therefore unavailable for PDF reports until a copy-on-write model
contract exists.

## First report: admin path only

1. Probe the model, read-only:
   `python scripts/pdf_source_probe.py --query "2TAL AS" --country NO`
   Check currency, current year, empty years, forecast years, WACC, DCF and
   `blockers`. Verify the native unit: the exporter treats modeldata money as
   millions (×1000 to thousands), so `sample_ns_by_year` for 2TAL AS 2025 must be
   about 178.0, matching the register's 178 033 kNOK sales. Only a model with
   `numerical_input_ready: true` and the right unit goes further.
2. Export with the PDF source as the kit's source:
   `VALUATUM_API_BASE_URL=$VALUATUM_PDF_API_BASE_URL VALUATUM_TOKEN=$VALUATUM_PDF_TOKEN
   python valuatum_kit/export_modeldata_json.py --fetch-script valuatum_kit/fetch_modeldata.py
   --fid <fid> --country-code NO --report-language en --source-currency NOK --output no.json`
3. `python scripts/prepare_report_demo.py --input no.json --input-format normalized
   --pipeline-id <id> --country-code NO --source-currency NOK --output no-run.json`
4. Submit `no-run.json` to the admin run endpoint. This is a paid LLM run and
   needs explicit approval.

## Not built yet

There is no public search, readiness or checkout for the PDF source. Those
models are private uploads, so an endpoint that takes a raw model id would let
anyone enumerate or buy reports on other customers' statements. A public flow
needs the PDF service to bind an upload session or user to its model id first.
