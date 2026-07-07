# Handoff — 2026-07-05

## ⛔ Never run a report generation against prod without asking first — see CLAUDE.md
This includes "just a verification run" suggested by a previous handoff's
"pick up here" section. Ask, then run.

## 🚨 PRODUCTION IS PAUSED (cost incident 2026-07-05)
2-generation runs hit $6+. All report generation is now blocked in prod:
`openrouter.runs_paused()` defaults to PAUSED whenever APP_TOKEN is set and
RUNS_PAUSED is unset. **To resume: set `RUNS_PAUSED=0` in Railway** (service
env vars). Local dev/tests unaffected (no APP_TOKEN).

Why the runs were expensive:
1. Round-1 single-writer = Fable 5 at **$10/$50 per MTok** (5× Sonnet 5);
   a whole report ≈ 30k+ completion tokens → $2–3 clean, before reasoning
   tokens (billed as completion).
2. Self-heal retry (`runner.run_stages`) re-runs a failed stage with an even
   larger correction prompt → doubles the stage cost when validators trip.
3. Round-2 = Opus 4.8 full-report rewrite ($5/$25), prompt carries the whole
   previous report — and was credit-free with NO count cap.
4. Gemini 3.1 Pro enrichment goes direct to Google and is recorded as
   **$0.00** in stage costs — real spend is higher than /api/costs shows.

Limits now live (commit d3748c0):
- `VALU_RUN_USD_CAP` default **$4/run**, `VALU_DAILY_USD_CAP` default
  **$25/day** (env 0 = disable). Checked before each paid stage.
- Round-2 capped at **2 per parent run** (`ROUND2_MAX_PER_RUN`), still
  credit-free. 429 beyond that.

**2026-07-05 LATER — TRUE root cause found (commit 89323cd):** the $6 runs
were SINGLE round-1 runs, not round1+round2 pairs. singlewriter.txt's cover
skeleton never asked for `cover.base_case_value`, which stage6_final HARD-
requires → 3/3 prod Fable runs failed validation and paid a full ~$2.9
self-heal retry (run totals exactly 2× stage sums). Fixed: KANSI rewritten
to renderer contract + field added to skeleton. Also: self-heal retry on
heavy stages (max_tokens ≥ 40000) now uses Sonnet 5 as editor
(CORRECTION_WRITER_MODEL). Round-2 writer = Sonnet 5 (ROUND2_WRITER_MODEL,
commit 402bdfc). Expected: Fable round-1 ≈ $3.1 first-try, round-2 ≈ $0.6.
NOTE: round-2 MUST re-run stage 1 — that's where clarifications fold in
(writer prompt has no {{clarifications}}); do not "optimize" it away.

CEO feedback batch (same commit): hard rules 24–34 in singlewriter.txt
(velkasilta ban, "ei saatavilla lähdedatassa", optioluonteinen explained,
audited-data tiers, headcount-outlier handling, meta.level stated, ROI-vs-
EBIT explanation, market-signal reinterpretation, EVA full-series table) +
enrichment 4b/4c (tender wins as signals, market-size verification) +
multi-series chart legends in render.py. CEO data-claims verified against
FAKTAT (run b26b77be): headcount [7,60,60,6,5,6,6,6,5] and inventories
548→94 are REAL in Profinder source (not our bug — CEO to check the
statement); po-lainat 246 vs interest_bearing_debt 225 is a genuine source
inconsistency (bridge uses 225) — question for Valuatum data team;
meta.level="parent" (emoyhtiö) — consolidated availability = Valuatum
question; EVA full 10y series EXISTS in engine data (one-year table was a
writer failure, now rule 34); "velkasilta" came from the literal FAKTAT
key `valuation_engine.dcf.bridge`.

**2026-07-06 — colleague-list execution (commit 69c194e + client site 4021f36):**
- Finnish sweep: cover hero label = "Yrityksen arvo (realistinen perusskenaario)",
  "raportin pääluku" gone from renderer copy, prompt rule 35 enforces
  perusskenaario terminology; rules 36 (financials = verified truth, no
  uncertainty analysis on realized data) and 37 (distinct labels for
  user_input vs clarifications tables); acquisition-direction rule (buyer =
  strength, target = price anchor) in enrichment + rule 33.
- Client site ExpertApp: expectation copy before + during generation
  (10–20 min, round-2 questions coming, 2 rounds included). Old "few
  minutes" copy corrected.
- **PEERS BLOCKED (investigated):** no company-search endpoint exists in
  /rest/modeldata or the 3 Profinder MCP tools; meta.industry is hardcoded
  None in the exporter, so "same industry" is undeterminable; same root as
  the FID blocker. Conditional path: manual peer y-tunnus list → financials
  fetchable TODAY via Profinder MCP statement tools (~100 lines / 3 files,
  sketch in workflow output). Zero-code first step: JSON-RPC tools/list
  probe against VALU_MCP_PROFINDER_URL (secret on Railway only) to see if a
  search tool already exists. Else → Valuatum tech team ask.
- **Reverse valuation AUDITED, no overclaim:** prompt implements a real
  crude calc (EV × WACC → required FCF vs achieved) but only when a signal
  has a sum + WACC; CEO's run had no signals → report correctly wrote
  "Käänteislaskelmaa ei voida tehdä…". Full Profinder reverse-valuation
  REST (Joona, trunk) not integrated. Untested branch: signal-with-sum
  arithmetic — exercise with a test run when one exists.

Open decisions for cost (not made unilaterally):
- Charge a credit for round-2 instead of/on top of the count cap?
- Price Gemini enrichment properly (add real prices to `_DIRECT_GOOGLE_MODELS`).
- Client frontends don't yet show friendly copy for 503 (paused) / 429
  (round-2 cap) — they surface raw error text.
- Opus 4.8 as single-writer was $1.48 and passed validators first try
  (run 61305402) — after the base_case_value fix, re-compare Fable ($3.1)
  vs Opus ($1.5) round-1 quality; CEO-reviewed reports were Fable.
- Email delivery + notification (CEO's #1) = part of the paid-flow build.

---

# Previous handoff — 2026-07-03

Backend + client-site work, all pushed to `main` in both repos. No open
branches, no running dev servers, nothing mid-flight. Read this before
touching the pipeline validators, the enrichment prompt, or the client site's
purchase flow.

---

# ⭐ CURRENT STATE + ARCHITECTURE (read this first) — updated 2026-07-04

## The three surfaces
1. **Backend** (this repo, `pipeline-runner/backend`, FastAPI on Railway
   `valu-pipeline-production-88f2.up.railway.app`). Runs the AI valuation
   pipeline. Single admin `APP_TOKEN` = unlimited. Reseed after ANY
   prompt/model/validator/stage change (`POST /api/reseed` with Bearer
   APP_TOKEN); runtime code changes go live on deploy without reseed. Build
   marker in `app/main.py:BUILD`, surfaced at `/api/health`. Deploy = push to
   `main` (Railway auto-builds ~45s). Two pipelines seeded: default 6-stage +
   single-writer "koeajo" (3-stage).
2. **Admin runner** (React/Vite, `pipeline-runner/frontend`, on Vercel). The
   operator tool: fetch a company's Valuatum data, run the pipeline, view the
   report. Full APP_TOKEN access.
3. **Client site** (`../Company_valuation_nettisivut`, Next.js on Vercel — its
   OWN repo `github.com/Valuatum/Company_valuation_nettisivut`). Public
   marketing + Stripe purchase site, PLUS the new `/asiantuntija` expert
   self-serve page. See that repo's HANDOFF.md.

## Expert self-serve (LIVE) — capped, invite-only, no accounts
- **Access keys** (`access_keys` table): mint `exp_…` keys with a per-key
  generation quota. A "generation" = one round-1 report; **round-2 refinements
  are free**; admin token unlimited. `store.consume_generation` is an atomic
  conditional UPDATE (race-safe). Mint: `POST /api/access-keys` (admin) →
  returns the key. `GET /api/expert/me` = an expert reads its own remaining
  quota. Quota == "credits" (the UI calls it "krediittiä").
- **Security (deny-by-default)**: the `app/main.py` auth middleware recognizes
  `Bearer exp_…` but allows it ONLY on `_EXPERT_GET`/`_EXPERT_POST` (their own
  run lifecycle + report + `/api/companies` + `/api/expert/*`). Everything else
  (reseed, edits, orders, minting, list-all-runs) is admin-only.
  `_require_run_access` enforces per-key run OWNERSHIP (`runs.access_key`
  column). Do NOT loosen this without care — it's access control.
- **Generation**: `POST /api/expert/generate {fid, company_name, company_code?,
  pipeline_id?, user_input?}` → consume 1 credit → `create_run` with
  `identifier=fid` (NO input_data) → stage 0 auto-fetches the Valuatum data →
  pipeline runs. `fetchers/company_data.py` wires stage 0 to
  `app.valuatum.export_stream(fid)` (was a NotImplementedError stub).
- **Client flow**: `/asiantuntija` on the client site — key gate → pick a
  company → generate → poll `GET /api/runs/{rid}` → report shown by fetching
  `report.html?force=1` WITH the bearer and injecting as `<iframe srcdoc>`
  (an iframe can't send an Authorization header) → round-2 ClarifyPanel.

## ⚠️ THE FID BLOCKER (the one thing gating "any company")
`/rest/modeldata` (DCF/EVA/forecasts) is keyed by Valuatum's internal **FID**
(numeric). The client site only has **Y-tunnus** + name. There is NO
Y-tunnus→FID resolver anywhere. Consequence:
- **What works TODAY without a resolver:** the `/asiantuntija` picker lists the
  operator's **pre-fetched** companies (`GET /api/companies` → `store.list_companies`,
  currently 7: Valuatum Oy fid=184362, Virnex, Supercell, SearchCo, Jungle Juice
  Bar, Athlos, OGOship). Each already has a stored FID (from when the operator
  fetched it in the admin runner). The expert picks by NAME; the FID rides along
  invisibly. So self-serve works for those companies right now.
- **What's blocked:** self-serve for ARBITRARY companies (search anything).
- **The unblock:** ONE fact from Valuatum's tech team — "is there an API that
  maps a Y-tunnus (or name) to the FID used by /rest/modeldata, or does the
  client's VALUATUM_DATA_API company search already return that FID?" Then add a
  resolver in the backend (small) so `generate` accepts a Y-tunnus. User is
  chasing this; until then, pre-fetch companies via the admin runner to make them
  available to experts.

## Paid customer flow (client site) — now paid-first (B), self-serve NOT built
- Today: search company → BuyBox → **Stripe pay** → `POST /api/orders` → an
  OPERATOR runs the pipeline in the admin runner and delivers the report. NOT
  self-serve. Delivery SLA copy = "30–60 minuutissa" (pipeline is automated;
  operator just triggers + delivers).
- **DESIGNED, NOT BUILT — paid self-serve + account-less regeneration:** pay →
  auto-generate a run → email a **signed per-run report link**
  (`/raportti/{run_id}?t=<HMAC(run_id, server_secret)>`) → opening it unlocks
  that report + a round-2 "add info & regenerate" flow. NO accounts, NO user
  table — the signed link IS the identity (same ownership model as expert keys,
  keyed by link instead of `exp_` key). This needs the FID resolver first (so
  payment can trigger generation by Y-tunnus). Limit refinements per report
  (e.g. 1–2) the same way as credits.

## Build order to get the paid product fully live (once FID resolver exists)
1. Backend Y-tunnus→FID resolver (per tech-team endpoint) → `generate` accepts
   Y-tunnus.
2. Backend: signed-report-link auth (HMAC per run_id) accepted by
   `report.html`/`round2`/`get_run` as an alternative to a bearer, scoped to
   that one run. A `/api/paid/generate` (Stripe-webhook-verified) mirrors
   `/api/expert/generate` but is gated by payment instead of a credit key.
3. Stripe: switch success flow (or webhook) to call `/api/paid/generate` and
   email the signed link. (Stripe webhook is the durable fix — the current
   success page posts the order client-side, guarded by an in-memory Set.)
4. Client site: a `/raportti/[id]` page that reads `?t=`, shows the report
   (srcdoc) + the round-2 refine panel. Reuse `/asiantuntija` components.
5. Persist uploaded statements (`/api/import` currently logs + discards).

---


## 2026-07-04 — Expert access keys (capped self-serve) — backend foundation (LIVE, no reseed)

CEO wants to share the system to a couple of invited experts, capped at a few
generations each. This is the SECURE BACKEND FOUNDATION; the client-site
self-serve flow (chosen surface = Option B) + UI quick-wins are the next chunk.
`build = 2026-07-04-expert-keys`. Runtime code → live on deploy, NO reseed.

- **Data:** `access_keys` table (key `exp_…`, label, generations_limit,
  generations_used, active, expires_at) in `db.py` SCHEMA; `runs.access_key`
  column (migration) tags which expert key created a run.
- **Quota:** `store.consume_generation(key)` — atomic conditional UPDATE
  (check-and-increment in one statement, race-safe). A "generation" = one
  round-1 `POST /api/runs`; round-2 (`clone_run`, has parent_run_id) and
  `start` never consume — refinement is free.
- **Auth (deny-by-default):** `main.py` middleware now recognizes `Bearer exp_…`
  keys but allows them ONLY on an allowlist (`_EXPERT_GET`/`_EXPERT_POST`):
  their own run lifecycle (create/start/round2/get/readiness/report.html+pdf/
  stream), `pipelines` read, `valuatum/company-json`, `expert/me`. Everything
  else (reseed, pipeline/stage edits, orders, key minting, list-all-runs,
  deletes, scoped stage reruns) is admin-token-only. `_require_run_access`
  enforces per-key run OWNERSHIP so one expert can't read another's/operator's
  runs. Admin token = unlimited, sees everything (access_key None).
- **Endpoints:** `POST /api/access-keys` (mint, admin-only) + `GET
  /api/access-keys` (list usage, admin-only) + `GET /api/expert/me` (an expert
  reads its own remaining quota — for the client gate).
- **Mint a key:** `curl -XPOST .../api/access-keys -H "Authorization: Bearer
  $APP_TOKEN" -d '{"label":"Matti","generations_limit":3}'` → returns the
  `exp_…` key to hand the expert.
- **Tests:** 91 pass (+2): quota atomicity + capped/scoped access (expert blocked
  from admin surfaces + others' runs).

**NEXT (client-site self-serve, Option B):**
1. DONE (`build 2026-07-04-expert-generate`): `fetchers/company_data.py` now runs
   the Valuatum kit (`app.valuatum.export_stream`) for a FID → stage-0
   input_data (was a NotImplementedError stub). `POST /api/expert/generate`
   {fid, company_name, company_code?, pipeline_id?, user_input?} consumes one
   generation, creates a run with `identifier=fid` (stage 0 auto-fetches) +
   `access_key`, starts it, returns run_id. Expert allowlist tightened: experts
   POST only `/api/expert/generate` + `round2` (raw `/api/runs` + `start`
   removed); GET adds `/api/companies` (pick a pre-fetched company). 91 tests.
2. Client site (`Company_valuation_nettisivut`): expert-key gate (clone the
   `editor/lib/auth.ts` cookie pattern), search → generate → poll → render report
   (iframe the backend `report.html`) → port the ClarifyPanel for round-2.
3. Client-site UI quick-wins (separate track, user approved): reconcile the free
   hero form vs paid BuyBox (revenue leak), hero uses real CompanySearch, fix the
   "email (optional)" label, fix/hide the 2 dead sample-report links, unify the
   delivery-SLA copy.

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
