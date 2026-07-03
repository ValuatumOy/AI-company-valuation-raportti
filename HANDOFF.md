# Handoff — 2026-07-03

Backend + client-site work, all pushed to `main` in both repos. No open
branches, no running dev servers, nothing mid-flight. Read this before
touching the pipeline validators, the enrichment prompt, or the client site's
purchase flow.

## 2026-07-03 (cont.) — 2-step interactive pipeline + analyst-grade reasoning (needs reseed)

Two connected upgrades, `build = 2026-07-03-twostep-analyst`. Backend 89 tests
pass, frontend builds. **Needs deploy + `/api/reseed`** for the prompt changes.

**A. Analyst-reasoning upgrade (prompt-only, both pipelines).** Teaches the AI
to value new products as a slice of an existing market instead of extrapolating
flat history, and to hunt regime-changes the numbers hide.
- `prompts/1_enrichment.txt`: VAIHE 2 kohta 9 (new-product category + named
  comparables + TAM/SAM), kohta 10 (`structural_inflections` hunt: lifted
  exclusivity/contract restriction, pivot, flat→hiring, exogenous/AI shift).
  New output fields: `business_lines[].product_category/category_comparables/
  tam_teur/sam_teur/market_basis` and `forward_revenue_view.optimistic_share_pct/
  optimistic_market_teur`; top-level `structural_inflections[]` and
  `clarification_requests[]`. Plus a `{{clarifications}}` round-2 ground-truth
  input block.
- `prompts/4_skenaariot.txt` + `prompts/singlewriter.txt` scenario section:
  periaate 3b/3c — optimistic value is a VISIBLE chain (market size → share% →
  revenue → EBIT% → value); millions allowed only with the chain shown, rejected
  without. Base case stays anchored to realized history.

**B. 2-step interactive feature.** Round 1 emits `clarification_requests`
(the AI's own blind spots); the user answers; round 2 re-runs from enrichment
(`from_order=1`) with the answers as ground truth, reusing the parent's stage-0.
- Data model: `runs.parent_run_id` column + migration (both PG + SQLite),
  `db.py`. `store.clone_run(parent_id, params)` copies input_data + the order-0
  stage_result, merges params, links parent.
- `runner._fmt_clarifications` + a `context["clarifications"]` line (mirrors the
  `user_input` block) so `{{clarifications}}` substitutes.
- `POST /api/runs/{rid}/round2` (`main.py`, `Round2In` model) → `clone_run` →
  `_start_bg(from_order=1)`. No new pipeline preset, no new stage.
- Frontend: `api.round2`, `Run.parent_run_id` + `ClarificationRequest` types,
  `ClarifyPanel.tsx` (renders the questions + answer boxes below the progress
  banner when a run settles), `startRound2` in `App.tsx`.

**Decisions taken (user):** build everything in one pass; round-2 = re-run from
enrichment (not writer-only); cap at 2 rounds (re-emitting clarification_requests
makes round-3 free later — not built); pricing = agnostic/decide later.

**Deferred:** round-3 loop UI; history child-under-parent grouping (parent_run_id
stored, not rendered); a stage-4 "unexplained millions" validator (add when a run
actually produces one). The Asiakastieto industry-quartile benchmark remains
impossible (no sector medians in input_data).

## 2026-07-03 (cont.) — Report fixes: FCFF build-up, competitors, EVA/DCF page (NOT yet reseeded)

CEO/user review of a Fable single-writer report flagged: (1) DCF page missing
the full FCFF build-up, (2) zero competitors, (3) confusing EVA-vs-DCF page.
A 5-agent investigation established the deterministic machinery was already
right; the gaps were narrower than they looked. Fixes committed on `main`,
`build = 2026-07-03-report-fixes`. **Needs deploy + `/api/reseed`** (the
prompt + validator changes only land in the live DB on reseed).

- **HOW STAGE 0 (FAKTAT) DATA IS PRODUCED — read this first.** There is NO
  auto Y-tunnus fetch: `fetchers/company_data.py:fetch_company_data` is a
  `NotImplementedError` stub. In production the operator runs the Valuatum kit
  via `POST /api/valuatum/company-json` → `app/valuatum.py:export_stream` runs
  `valuatum_kit/export_modeldata_json.py`, which emits the FULL
  `valuation_engine.dcf` / `eva` / `wacc` blocks and streams them straight into
  the run as `input_data` (the "manual paste" path, auto-filled). So on a fresh
  run the dcf sub-block IS present. If a report ever lacks the FCFF build-up,
  the run's stage-0 `valuation_engine.dcf` was thin/legacy, not a code bug.

- **FCFF build-up was never a code bug.** `app/dcf_detail.build_dcf_detail_blocks`
  already builds the full image-2 driver table (EBIT → +Poistot → −verot →
  −käyttöpääoma → −investoinnit → FCFF → disk. FCFF) + EV→equity bridge, and
  `assemble._inject_dcf_detail_blocks` injects it into DCF section id `9` (which
  the singlewriter prompt emits; the "section 8" display number is a render
  artifact — `SECTION_ORDER` drops id `7`). The screenshotted report predated
  the injection / ran on thin data. **Change made:** `dcf_detail.py` now emits a
  visible `warning` callout when the dcf block is *partly* populated (drivers
  present, FCFF/discounted missing) instead of silently returning `[]`. A wholly
  empty dcf (legit no-forecast company) still stays silent.

- **EVA == DCF was never a math bug.** `export_modeldata_json.py` pins
  `eva.equity_value_before_floor` = DCF equity, and
  `app/valuation_equivalence.normalize_report` deterministically rebuilds
  section 8 (DCF=EVA equivalence table) and section 10 (EVA reconciliation:
  Investoitu pääoma + PV(EVA) = yritysarvo = PV(FCFF)). Literature confirms the
  CEO: EVA is DCF rearranged → identical value on identical inputs (Damodaran,
  McKinsey/Koller, ACCA). **Change made:** enriched the deterministic text in
  `valuation_equivalence.py` — section 8 leads with a plain-language "DCF ja EVA
  ovat sama arvo kahdella tavalla" framing paragraph; section 10 explains the
  reconciliation and adds a "Mitä EVA kertoo lisää" callout (ROIC vs WACC). This
  lives in code, NOT the prompt, because `_normalize_section10` fully replaces
  the model's section-10 blocks.

- **Competitors were a real prompt gap.** Enrichment produces
  `enrichment.competitors`, the writer was told to *read* them but had no section
  to *write*. **Change made:** `prompts/singlewriter.txt` Section 3 now mandates
  `### Markkina ja sen koko` + `### Kilpailijat ja kilpailuasema` (table from
  `enrichment.competitors` + 2–3 paragraphs), mirroring the working 6-stage
  `2_profiili_kilpailijat.txt`. Falls back to a "ei löytynyt kilpailijoita" line
  when the list is empty. NOTE: if a run still shows no competitors, check the
  stage-1 Gemini `enrichment.competitors` actually populated — that's a
  web-search data issue, separate from this prompt fix.

- **Tests:** 86 passed (was 85). New `test_dcf_detail_surfaces_partial_dcf_...`;
  loosened the section-8 equivalence assertion to presence; added a
  competitor-section presence guard to the single-writer seed test.

- **Asiakastieto additions (2026-07-03, LIVE on deploy — runtime code, no reseed):**
  - **Verottaja cross-check DONE.** `valuation_equivalence._verottaja_blocks`
    appends a "DCF/EVA vs. verottajan malli" table + explanation into section 8:
    tuottoarvo = 3 v:n keskitulos (`actuals.income_statement.net_income`) / 0.15,
    substanssiarvo = `actuals.balance_sheet.equity`, käypä arvo = keskiarvo kun
    tuottoarvo > substanssiarvo, muuten substanssiarvo. Skips gracefully if
    net_income/equity missing. `build = 2026-07-03-verottaja-crosscheck`.
  - **Industry-quartile benchmark NOT built — data does not exist.** The exporter
    emits `peers: []`, `industry_bankruptcy_risk_pct: [None…]`, and `key_ratios`
    holds only the company's own ratios. There are NO sector medians/quartiles in
    `input_data`, so a deterministic peer-benchmark block is impossible without a
    new data source (web/registry enrichment). Do NOT fabricate sector figures.
  - Do NOT fabricate an Asiakastieto AAA–C rating or PD% (proprietary Enento).

## 2026-07-03 (cont.) — Foundation rebuild after CEO review (LIVE + verified)

CEO review: reports didn't understand what companies do (described Valuatum
as "just a valuation calculator", guessed numbers) and flip-flopped on
omavaraisuusaste. Both fixed at the root, deployed, reseeded, and verified
end-to-end on Valuatum ($0.67 run, all 6 stages ok, validators pass).

- **Foundation model swap** — stage 1 enrichment: `gemini-2.5-flash` →
  `google/gemini-3.1-pro-preview`. It's the step every later stage builds on;
  it was the cheapest model, which is why the business understanding was thin
  and swung run to run. **Models live now: 1 gemini-3.1-pro-preview (web),
  2/3/4 deepseek-v4-pro, 5/6 claude-sonnet-5.**
- **Enrichment rewritten** (`1_enrichment.txt`) into a forward-looking,
  LOCKED `business_thesis` (what it does / how it evolved / where it's heading
  / why history may mislead) + `business_lines[]` with per-line pess/neu/opt
  forward-revenue views. Market/share estimates now allowed but must be marked
  `(lähde: …)` or `"arvio, käyttäjän muokattava"`. Schema is ADDITIVE — old
  `business_profile`/`competitors` kept, so stage 2 / render / validators are
  untouched.
- **Locked-premise preamble** ("PERUSLÄHTÖKOHTA (LUKITTU)") in stages 2/4/5/6:
  build on `business_thesis`, don't re-derive or reduce it to the calculator;
  scenarios (stage 4) build from `business_lines` forward views.
- **Omavaraisuus lock** — rule 4 in all 7 prompt files now FORBIDS recomputing
  omavaraisuusaste and mandates the canonical
  `input_data.key_ratios.equity_ratio_pct` verbatim; stage 3 surfaces it as a
  §5 table row. Root cause of the flip-flop: rule 4 used to invite recompute,
  and with two equity figures (incl/excl capital loans) the model chose
  differently each paragraph.

Commits: `98ef551` (foundation), `5340abf` (omavaraisuus lock), + build bumps.
Verify a fresh run with `scratchpad/run_valuatum.py` / `poll_valuatum.py`
(drive a run via API + inspect thesis, §3, omavaraisuus consistency).

**NEXT:** Ogoship end-to-end (CEO's 2nd named test company); then optionally
tie the optimistic scenario more explicitly to named business-line market
shares (his "luottoriskit.fi takes a few %" ask). Deferred still: stage-5
grounding advisory→blocking, spend-cap env vars.

## 2026-07-03 (cont.) — 15y history + single-writer mode (LIVE)

- **History default 5 → 15** (commit baafd53). Valuatum now gets 9 actual years
  (2016–2024); 9 is the hard cap from the Valuatum modeldata API (single-digit
  Y-offsets only — `build_var_poses` clamps to Y-9; deeper history would need
  extending the year-set from the Profinder backfill).
- **Single-writer mode** (commits 4c417ae, bc5bfff) — a 2nd pipeline preset
  "Yhden kirjoittajan raportti (koeajo)": stage 0 FAKTAT + one model
  (Fable 5, web search) writes the whole report in one pass. Selectable via a
  new dropdown in the operator UI top bar. Reuses `assemble()` (deterministic
  DCF/sensitivity/headcount blocks still inject from stage 0). Prompt =
  `prompts/singlewriter.txt`. Why: a single writer fixes the 6-stage pipeline's
  repetition + materiality failures structurally (they were architectural, not
  a model fault — proven with a no-steer test).
- **Known trade-off:** single-writer is slow (~12–15 min) + expensive
  ($2–4/report) because the OpenRouter web plugin uses Anthropic's native
  agentic engine (injects 100–276k tokens). `openrouter.chat` now gives heavy
  stages a 1500s timeout so they complete. Future tuning: exa engine (cheaper,
  shallower) or split research from writing.
- Also fixed: `<cite>` web-plugin markup leaking into prose (render `_clean`);
  `list_pipelines()` ordering (default stays `[0]`).
- Scratchpad test scripts: `run_singlewriter.py`, `render_json.py`,
  `run_swmode_prod.py` (end-to-end via prod API).

## Repos involved

- **This repo** (`AI-company-valuation-raportti`) — the report-generation
  pipeline (backend: `pipeline-runner/backend`, FastAPI + OpenRouter;
  frontend: `pipeline-runner/frontend`, the operator UI). Deploys to Railway
  (`https://valu-pipeline-production-88f2.up.railway.app`, project
  `valu-pipeline`, linked via `railway` CLI — already authenticated on this
  machine as `github@valuatum.com`).
- **`../Company_valuation_nettisivut`** (github.com/Valuatum/Company_valuation_nettisivut)
  — the public-facing client site, Next.js 16, deploys to Vercel. Has its own
  `HANDOFF.md`.
- **`../company-valuation-site`** (github.com/ValuatumOy/company-valuation-site)
  — an older sales-site prototype with useful functionality (search/Stripe/
  import/calculator) but weaker design. Ported INTO nettisivut this session
  (see below); now superseded. Consider archiving on GitHub.

## Critical operational fact: prompts need a manual reseed

`prompts/*.txt` changes do **not** reach the live pipeline on deploy.
Validators and token limits auto-sync on boot (`seed.sync_code_and_limits`);
prompt text does not, because prompts are user-editable in the operator UI
and a blind overwrite would clobber operator edits. After changing a
`prompts/*.txt` file and pushing, force-reseed manually:

```bash
TOKEN=$(railway variables --kv 2>/dev/null | grep "^APP_TOKEN=" | cut -d= -f2-)
curl -sS -X POST https://valu-pipeline-production-88f2.up.railway.app/api/reseed \
  -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json"
```

`{"ok":true,"updated":7,...}` means all 7 stages got the vendored defaults
back. Verify a specific change landed with `GET /api/pipelines` (same
Bearer token) and grep the stage's `prompt_template`.

## What shipped this session (7 backend commits, 1 site commit)

1. **`2a9fdb9`** — DCF bridge validator sign bug. Valuatum's
   `ib_debt_nega_prev_year` field is pre-negated; `validators_seed/stage3_numbers.py`
   was subtracting it (`ev - debt + cash`), double-counting debt. Now
   `ev + debt + cash`. Also fixed headcount fetch — `cr_employees` was
   missing from the varname candidate list in
   `valuatum_kit/export_modeldata_json.py`.
2. **`f0dedc8`** — `prompts/1_enrichment.txt`: mandatory searches for the
   company's current customers and recent product launches, plus a
   recency-conflict rule (newer verified source wins over stale marketing
   copy). Root cause of a report describing Valuatum itself as a free
   calculator despite having enterprise/bank customers.
3. **`c2ab159`** — "saved companies" quick-pick in the pipeline UI
   (`pipeline-runner/frontend/src/components/StageEditor.tsx`) loaded stale
   cached `input_data` on click instead of refetching. Now always refetches.
4. **`3a8e355`** — new deterministic per-employee efficiency table
   (`app/headcount_efficiency.py`, injected into section 5 via
   `app/assemble.py`). Uses confirmed Valuatum varnames `cr_ns_per_employee`,
   `cr_added_value_per_employee`, `cr_employee_expenses_per_employee`,
   `cr_ebitda_per_employee`, `cr_net_earnings_per_employee` — EBIT/employee
   has no confirmed varname so it's derived locally (ebit ÷ headcount).
5. **`e11015d`** — inline `(lähde: domain, pvm)` citations in report prose
   are now clickable (`app/render.py`, `_collect_source_urls` +
   `_SOURCE_CITE_RE` + a `ContextVar` domain map built once per
   `render_html()` call — thread-safe under concurrent report renders).
6. **`29b35ee`** — found by reading a real generated Valuatum Oy PDF:
   (a) duplicate DCF table on the DCF page — the LLM's own horizontal FCFF
   table (per the OSIO 9 prompt instructions) wasn't being recognized as
   "old" by `_is_old_fcff_table` in `app/assemble.py` (it only matched the
   narrow 3-column legacy shape); broadened to match by title + row labels.
   (b) per-employee ratios rendered as "0" — the `cr_*_per_employee` fields
   come back in millions like every other Valuatum money field, but were
   fetched unscaled; now fetched with `money=True` and scaled ×1000 at
   render time in `headcount_efficiency.py`.
7. **`29354c8`** — from a 3-agent audit (web fact-check with sources,
   full numeric recompute, buyer-perspective prose read) of a Supercell PDF:
   - `prompts/1_enrichment.txt`: mandatory search for the company's own
     annual-results/newsroom before ever concluding "no explanation found"
     for a revenue swing; historical ownership transactions (e.g. a 2016
     acquisition) now count as market signals even if old; junk
     methodology-only sources (calculator sites, theses) excluded from the
     source register.
   - `prompts/4_skenaariot.txt` + `validators_seed/stage4_scenarios.py`:
     pessimistic scenario no longer defaults to value 0 — a ~0-value
     scenario whose own table shows sustained positive EBIT and net cash
     now fails validation (rough perpetuity check).
   - `validators_seed/stage3_numbers.py`, `stage4_scenarios.py`,
     `stage6_final.py`: blocking check — any years-as-columns table where
     every row lacks a label cell now fails the stage.
   - `app/render.py`: raw engine floats in table cells now format
     Finnish-style; schema token leaks (`market_signals`,
     `client_reported_signals`, `tukee_kasvua`, etc.) now translate to
     readable Finnish instead of leaking into prose.

Test suite: `cd pipeline-runner/backend && python3 -m pytest tests/ -q` → 81
passed as of this session.

## Client-site merge (nettisivut commit `f480342`)

Two site repos existed; ported `company-valuation-site`'s functionality
INTO `Company_valuation_nettisivut` (design repo) via a 4-agent workflow —
design is systemic (CSS tokens, content-driven sections), functionality is
modular (API routes + lib files port as units). New routes: `/yritys` +
`/yritys/[id]` (search + BuyBox purchase), `/api/checkout` +
`/kassa/valmis|peruutettu` (Stripe, demo mode without `STRIPE_SECRET_KEY`),
`/tilinpaatokset` + upload flow, `/laskuri`, `/kertoimet`. Pricing: 79€ base
/ 99€ import (79€ with data-sharing) / 129€ creditsafe, all in
`src/lib/pricing.ts`, env-overridable in cents.

**The connection to this repo**: `/kassa/valmis` verifies the Stripe
session server-side, then POSTs the order to **this** backend's
`POST /api/orders` (the same endpoint the site's original hero form already
used) — every paid purchase lands in the operator's Tilaukset view
automatically. Verified end-to-end live: search → company page → demo
checkout → order appeared in the Railway backend with the correct price and
a `KOEMAKSU` marker (then manually marked `spam` to clean up the test row).

Full detail in `../Company_valuation_nettisivut/HANDOFF.md`.

## Deferred / not done

- **Stage-5 (analyysi) grounding validator is still advisory, not
  blocking.** Recommended twice this session as the next highest-value
  report-quality item — the dress rehearsal already showed 0 false
  positives for stage 3's equivalent blocking gate, so promoting stage 5
  should be low-risk. Not started.
- **Spend caps not set.** `VALU_RUN_USD_CAP` / `VALU_DAILY_USD_CAP` Railway
  env vars are coded but default OFF (0). OpenRouter dashboard hard balance
  cap also not set. This is the cheapest unaddressed financial risk in the
  system — 5 minutes of dashboard work, no code.
- **Per-employee scale fix and the Supercell 5-fix batch are verified by
  unit tests and a manual PDF review, not by a fresh live rerun.** Confirm
  on the next real Valuatum Oy or Supercell run before treating either as
  fully closed.
- Stripe webhook for durable order posting on the client site (current
  success-page post is in-memory-deduped only — lost on server restart or
  a lost redirect after payment).
- Import file storage on the client site — files are acknowledged but not
  persisted; Vercel's ~4.5MB body limit will also block real uploads once
  storage is wired (needs direct-to-blob).
- `STRIPE_SECRET_KEY` / `NEXT_PUBLIC_SITE_URL` not set on Vercel — checkout
  runs in demo mode until set.
- Company search on the client site uses a bundled sample dataset (Rovio,
  Wolt, Supercell, Relex...), not live Valuatum data. `VALUATUM_DATA_API_URL`
  hook exists in `src/lib/companies.ts` but isn't wired to anything real.

## Pick up here

Confirm the Vercel deploy picked up nettisivut `f480342` and the live
`/yritys` → checkout flow works against production (not local); then
either promote the stage-5 grounding validator to blocking, or set the
spend caps — both are one-sitting jobs and both were pushed to "next
session" today.
