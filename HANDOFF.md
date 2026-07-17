# Handoff — 2026-07-16 (read this first)

## 2026-07-16 — Production hardening for paid go-live (61a1caf client, LIVE but dormant; cap env set)

Shipped the payment-critical gaps that stood between us and taking real money.
All DORMANT until Stripe keys are set (client Stripe is still unset → demo mode),
so pushing was safe. Client 61a1caf pushed (Vercel). Backend: no code change,
only an env var.

- **Durable Stripe webhook (audit H8) — client `/api/stripe/webhook`:** the old
  fulfilment ran in the browser on the `/kassa/valmis` success page; if the user
  closed the tab during the Stripe redirect, payment was taken and nothing
  generated. New server-to-server webhook (checkout.session.completed /
  async_payment_succeeded) calls the SAME backend `checkout-generate`, which is
  idempotent on session id, so webhook + success-page racing can't double-run.
  import/creditsafe fall to postOrder (ponytail: not session-idempotent → a
  possible dup order row if both fire; no money path, operator dedupes).
- **VAT — Stripe Tax (chosen by Esa: automatic_tax):** checkout now enables
  `automatic_tax`, `tax_behavior: 'exclusive'`, and required billing address, so
  the advertised "79 € + alv" actually adds Finnish VAT (25.5%) on top and
  handles EU B2B reverse charge. NOTE the contradiction found: `pricing.ts`
  comment said "prices + alv" (inclusive) but ALL UI says "+ alv" (exclusive) —
  went exclusive per the ads.
- **Per-run spend cap ON:** Railway `VALU_RUN_USD_CAP` 0 → 5 (day cap already
  $75). One stuck run can no longer eat the whole day cap.
- **M3 security (client):** editor auth now FAILS CLOSED when EDITOR_PASSWORD is
  unset (was falling back to the guessable default 'valuatum-editor').
  EDITOR_PASSWORD is set in Vercel prod (verified) so no lockout. JSON-LD from
  the content editor is now run through `safeJsonLd` (escapes `<`/`>`/`&`/
  U+2028/9) on home, content and blog pages — closes the `</script>` stored-XSS
  breakout. Verified: breakout string comes out fully `<`-escaped.

- **Editor REMOVED (follow-up same session, client 360b7cc).** The M3 content
  editor (/editor + /login + /api/editor/*) turned out to be fully filesystem-
  based (sessions, drafts, publish all fs.writeFile) → never worked on Vercel's
  read-only serverless fs (login 500'd → mislabeled "Väärä salasana"; saving
  could never persist). It had no UI entry point. Per the user's call, removed
  entirely rather than reworked. Homepage content is edited directly in
  `src/content/fi/home.json` + `src/content/site.json` (commit redeploys).
  loadPublishedBundle (the live site's content reader) kept; draft-preview
  branch + editor config consts dropped. `next build` clean. The earlier
  auth.ts fail-closed + stateless-cookie fixes are now moot (files deleted).
  safeJsonLd escaping stays (blog/content pages still emit JSON-LD).

- **E2E VERIFIED 2026-07-17 with Stripe TEST keys** (user set STRIPE_SECRET_KEY
  on Railway+Vercel, STRIPE_WEBHOOK_SECRET on Vercel, registered the dashboard
  webhook endpoint). Full paid flow driven via browser (test card 4242): real
  Stripe session (not demo), payment → backend verified payment_status=paid →
  resolved Supercell (fid 170898) → generation started (rid 319cfd0f). Webhook:
  bad/absent signature → 400, real event → 200; it fired at 08:31:54 BEFORE the
  browser success page, i.e. the webhook itself created the run (durability
  proven). Idempotency: exactly ONE run despite webhook + browser both calling
  checkout-generate. Per-run $5 cap active. (Two 08:28 webhook 400s were pre-
  deploy secret-propagation noise, resolved by 08:31.) Tax stayed off (gate) so
  no address collection — as expected. Test run ~$3 ran to completion, emailed
  a throwaway address.

REMAINING before real payments (config, not code — I can't do these, no keys):
0. Swap Stripe TEST keys → LIVE keys (same env vars on Railway + Vercel),
   register a LIVE-mode webhook endpoint, set its whsec. Then a live smoke test.
1. Stripe: create account keys; set `STRIPE_SECRET_KEY` on BOTH the client
   (Vercel) and backend (Railway); set `STRIPE_WEBHOOK_SECRET` (client);
   register the webhook endpoint `https://<client>/api/stripe/webhook` in the
   Stripe dashboard; enable + register Stripe Tax (else automatic_tax checkout
   sessions error). `EXTRA_ROUND_PRICE_CENTS` already defaults to 500 for paid
   round3+.
2. E2E NOT DONE — needs the above keys first (payment path is demo-mode until
   then). Once wired: Stripe test key + test card → real generation (~$3, within
   the $5 cap the user authorized) → confirm webhook fulfils when the success
   tab is closed, VAT shows on the Stripe page, report delivers.

Files: client src/app/api/stripe/webhook/route.ts (new), src/lib/jsonld.ts
(new), src/app/api/checkout/route.ts, src/editor/lib/auth.ts,
src/app/(site)/page.tsx, src/app/(site)/blogi/[slug]/page.tsx,
src/components/ContentPage.tsx.

## 2026-07-16 — User-set scenario probabilities + pessimistic floor fix (a5b6c4e backend, 82844cb client, LIVE + reseeded)

Backend HEAD a5b6c4e deployed (build `2026-07-16-scenario-probabilities`) +
reseeded (7 stages, live pipeline confirmed carrying all three new prompt
strings). Client 82844cb pushed (Vercel). 168 tests pass.

- **Pessimistic scenario floor fix (singlewriter.txt):** the prompt was
  effectively hardcoding owner value to 0 ("Omistaja-arvo 0 tEUR tai lähellä
  sitä") — 27/28 stored runs came out 0 (only Supercell nonzero). Reframed as
  a liquidation/realisation value: cash (full) + receivables + realizable
  assets at a haircut − all liabilities; the 0 floor applies ONLY when that is
  genuinely negative, not as a default for a healthy company with cash. Balance
  items are already in input_data so the writer computes it directly.
- **Profile rename:** "Tappiollinen käännekohde" → "Käännettä hakeva
  tappiollinen yhtiö" (clearer). Only occurrence was singlewriter.txt.
- **User-set scenario probabilities (the main feature):** the three scenario
  probabilities were AI-picked from 3 fixed profiles (kept — fixed profiles are
  correct: probability is the report's most uncertain number, free AI guessing
  would make the headline value swing per-run). Added an OPTIONAL user override
  on the round-2 form. Empty = AI picks the profile as before; filled must sum
  to 100. Design: `ScenarioProbabilities` model (sum=100 validated) →
  Round2In.scenario_probabilities → params → runner builds a
  `{{probability_override}}` directive → singlewriter.txt Todennäköisyydet
  section honours it instead of picking a profile. Threaded through the paid
  round3+ path too (new `pending_rounds.scenario_probabilities` column survives
  the Stripe redirect). Three optional percent inputs added to BOTH round-2
  forms: admin `ClarifyPanel.tsx` and client `ExpertApp.tsx` inline panel.
- **This is OUR-system UX, not an AI/report-quality fix.** It's the light
  half of Esa's "let the user steer the numbers" ask. The HEAVY half —
  letting the user rewrite the revenue/estimate forecasts (Valuatum's own model
  numbers, e.g. the absurd 2026→2030 revenue decline) — is NOT done: it needs
  the Valuatum "change estimates" channel wired up, a separate larger work item.
  Question still open for Sami: is that estimate channel usable for
  user-directed inputs (not just consensus/AI numbers)?

- Files: backend models.py/runner.py/main.py/store.py/db.py + prompts/
  singlewriter.txt + test_report_pipeline.py; admin frontend api.ts/App.tsx/
  components/ClarifyPanel.tsx; client repo src/expert/expertApi.ts + ExpertApp.tsx.
- Verify E2E (not done — needs a paid round-2 run, skipped per no-prod-runs
  rule): generate round 1, open round-2 form, set e.g. 30/50/20, confirm the
  report §11 uses exactly those and says "käyttäjän asettamat".

## 2026-07-11 — Roadmap steps 3–7 executed (6e2b77a…b8497d8, LIVE + reseeded, all 5 delivered RIDs verified)

Executed the reconciled cross-agent roadmap end to end. Backend HEAD b8497d8
deployed + reseeded (7 stages); client 40590ef pushed (Vercel). 152 tests.

- **Step 3 — fixtures + shadow validation (6e2b77a):** real SaaShop/Virnex/
  AWAKE/Valuatum extracts locked in tests/fixtures/runs/ with end-to-end
  characterization tests. stage6_final now validates the ACTIVE
  machine_readable.scenarios schema (count/names/values/prob-sum/EV math/
  cover==EV + ported zero-fundamental guard) in SHADOW mode — named in the
  validator report, never blocking. report_qa gained post-assemble scenario
  math + cover-vs-anchor checks (valuation_equivalence stamps
  _valuation_anchor_teur); catches AWAKE's 762-vs-1144, no SaaShop
  floor false positive.
- **Step 4a — EVA honesty (6e2b77a):** terminal EVA never backsolved; missing
  components shown as "ei saatavilla lähdedatassa", raw engine EVA shown,
  DCF divergence surfaced in a warning callout. SaaShop's fabricated
  +3 891 tEUR is gone from the live rerender.
- **Step 4b — deterministic optimistic waterfall (9233773):**
  app/scenario_waterfall.py computes (CV_n − net_debt_n)/(1+wacc)^n (+optional
  dilution, floor) from pinned machine_readable.optimistic_assumptions;
  assemble overrides scenario value/contributions/EV/cover and injects a
  derivation table into §11. Old runs without the field untouched. Prompt's
  flawed chain replaced with the time-consistent bridge (single financing
  representation). Applies to NEW runs (writers must emit the assumptions).
- **Step 5 — delivery gating (f67aa01 backend, 40590ef client):** email sent
  only when report_readiness ready (held + logged otherwise, Resend failure
  logged); client dropped force=1 (all 5 delivered RIDs verified ready:true
  first — links keep working) and surfaces 409 readiness issues.
- **Step 5b — payment protection (f67aa01):** GET /stream is now a READ-ONLY
  progress stream (it used to re-execute the paid pipeline); admin rerun
  endpoints keep executing via _stream_execute. checkout-generate verifies
  payment_status=paid from Stripe when STRIPE_SECRET_KEY is set (demo mode
  unchanged) + per-session lock closes the double-run race.
- **Step 6 — semantics (b8497d8):** section 7 scoring table surfaced into §8
  ("Menetelmävalinnan pisteytys", survives re-assemble); cover hero is now the
  conservative base value, expected value labeled "AI:n oletuksilla,
  käyttäjän vahvistamaton" with a downside/upside bridge; "raportin pääluku"
  gone. NOTE: this reverses the 08d95b3 hero choice (expected→base) per the
  reconciled list — flag to Esa.
- **Verified live (free rerenders, no force):** all 5 RIDs (4 testers +
  Esa's Valuatum) ready:true, cover chart intact, hero=base, scoring visible,
  no 3 891, honest EVA rows, PDF 200/application/pdf.

NOT done (deliberate): pessimistic recovery/downside-waterfall (waiting Esa's
spec); flipping shadow checks to blocking (dress-rehearsal first); Stripe
webhook/VAT/import persistence on the client site (own work item, before
Stripe live); H3–H9 backlog items (cost reservation, round-cap depth, expiry,
Profinder merge, enrichment source validator, editor password).

## 2026-07-11 — Stabilization pair shipped (fc4af74, LIVE, verified) — roadmap step 2 done

Per the reconciled cross-agent status list (audit follow-up), the two small
stabilization fixes are live and verified on free rerenders of the Valuatum
(71e41a6c…) and SaaShop (4922abf3…) runs:

- **equity_value alias hardening:** `render._scenario_num` +
  `scenario_compare._val` know equity_value/equity_value_teur explicitly and
  the generic `*value*` fallback now skips enterprise-value keys — a scenario
  item carrying both can no longer resolve to the debt-inclusive EV figure.
- **Reported headcount kept visible:** the source figure stays in the
  Henkilöstötehokkuus table unchanged; the personnel-cost-implied value is a
  separate "Henkilöstö (arvio henkilöstökuluista)" row. Ratios still use the
  implied value for flagged years. (Reverses the replace-behavior of 0bcf0b2
  per the shared decision, keeps its correction effect.)
- 137 tests pass. No prompt change, no reseed needed.

Next per the agreed order (no back-and-forth): step 3 — lock SaaShop/Virnex/
AWAKE data extracts as characterization fixtures, then build the
machine_readable.scenarios validator + post-assemble check in shadow mode
(warn, don't block). Then deterministic optimistic-scenario waterfall + EVA
(P0), then delivery gating, then report semantics. All verified via free
rerenders before any paid run (needs explicit approval).

## 2026-07-11 — Esa's feedback triage: 3 fixes shipped (0bcf0b2, LIVE + reseeded, verified on Esa's Valuatum run)

Esa reviewed his 10.7 Valuatum report (RID `71e41a6c68dd481e9300f64b82cf0bb6`).
Main point: the AI report is fine — the weak spot is the Valuatum SYSTEM's own
deterministic forecasts (Valuatum Oy: 421 tEUR 2025 actual → engine forecasts
decline 359→285 until 2030, an artifact of the 2023 one-off revenue bump of
~110 tEUR from the matured Finnair hybrid loan). Shipped fixes:

- **Headcount if-rule** (his item 6): `headcount_efficiency` now corrects
  reported headcount when it's >2× the personnel-cost-implied value
  (|costs|/50 tEUR), rescales the engine per-person ratios, adds a callout
  naming the corrected years. Valuatum 2017–18: 60 → 5, ratios now ~81 200
  €/person. Verified on live rerender.
- **Sparse-year gap markers** (item 4): `render._insert_year_gap_cols` puts a
  visible "…" column between non-consecutive year columns (2026e | … | 2030e
  | … | 2035e). Applies to all tables incl. old reports on rerender.
- **"Ei hajauta riskiä" phrasing dropped** (item 1): prompt rule 13 + stage-D
  bullet rewritten to plain language, phrase banned. Reseeded (updated 7
  stages), marker verified live. New runs only.
- 136 tests pass.

Answered from data (no code): scenario probabilities are prompt-pinned profile
defaults — 12 recent ok runs: pessimistic ALWAYS 35 %, profile
volatile-growth (35/40/25) 9×, turnaround (35/50/15) 3×; the stable-profitable
profile (20/60/20) has never fired. EVA/DCF backsolve = audit C2, on the P0
list, not forgotten.

**Big open feature (Esa's main ask): user-feedback loop into the Valuatum
system's "change estimates" channel** — user tells the AI why the base
forecast is wrong ("2023 was a one-off, growth resumes 2026"), AI produces
revised estimate series, pushes them through the same channel consensus/trunk
AI estimates already use, modeldata re-exported, report rerun. This repo's
valuatum_kit is read-only today — needs the change-estimates API details from
Esa/Valuatum team before we can build. Design question, not started.

## 2026-07-11 — SaaShop review fixes shipped (commits 5180987 + cc493a2, LIVE + reseeded, verified)

Full page-by-page review of the SaaShop PDF (one of the 4 external-tester
reports) surfaced 8 issues beyond the cover-chart bug; all deterministic ones
are now fixed and verified on the live re-render (floors=0, §13 uppercased,
zero-sea heatmap gone, cover chart intact on all 4 reports):

- **Inverted sensitivity blocks on deep-loss companies** (`5180987`):
  Revenue×EBIT-% heatmap claimed "worse margin → MORE value" (sign flip in
  the linear proxy when base EV < 0) and the alt terminal-margin table showed
  "−9,3 % → 0 tEUR" next to "0,8 % → −1 508 tEUR". Both now suppressed when
  the base is non-positive.
- **`cc493a2` (six fixes):** (1) `render._defloor` in `_clean` — the
  "floorattu/flooria/floor-käsittely" anglicism family (banned by rule 35,
  emitted anyway) now deterministically replaced with "lattiaan nostettu"
  forms, case-preserving. (2) Section titles uppercased at display time
  (`_title_case`) — model wrote §13 in sentence case. (3) `.mcard` value
  wraps at word boundaries ("rahoituskierro/s" break). (4) WACC×growth
  heatmap suppressed when >80 % of cells floor to 0 (SaaShop: 24/25).
  (5) Henkilöstötehokkuus per-person ratios blanked for years with revenue
  < 1 tEUR/person (SaaShop 2018: hc 30 vs 2 tEUR revenue = 67 €/person data
  error); raw headcount stays visible. (6) Company search prefers Finnish
  industry name from `industryTree` over English `industryText` (new runs).
- **Prompt rule 42** (reseeded, verified live): margin-% cells show "–" for
  years with revenue < 50 tEUR (was "−700,0 %" on a 2 tEUR founding year).
- 134 tests pass. NOT fixed (model-side, needs new runs to judge): 2016–2017
  all-dash columns in §5, pessimistic-scenario-always-0 (deferred, waiting
  Esa's spec — see cont. 3 below and the memory note).
- PDF page-14 `" "` artifact could not be reproduced in the HTML source —
  likely a PDF-extraction artifact, not report content.

## 2026-07-10 (cont. 3) — 🚨 Cover chart vanished on 4 externally-tested reports; FIXED (commits 26713a3 + 2f06ba2, LIVE + reseeded)

External testers generated 4 reports (SaaShop, AWAKE.AI, LightningChart,
Virnex, 15:08–15:12 UTC) whose covers rendered the hero figure but NO
scenario bar chart / explanation grid — a huge blank middle. Root cause:
`prompts/singlewriter.txt` skeleton had `"scenarios": []` with NO item
schema, so Sol picks the value key name per generation. Old runs emitted
`value_teur`/`owner_value_teur` (handled); this batch emitted `owner_value`
AND `equity_value` (two new spellings in one batch) → `render._scenario_values`
parsed every value to None → no `derived["range"]` → `_cover` silently
dropped the chart, and `scenario_compare` dropped the §11 table.

Fixes (both live, verified on all 4 runs via `report.html?force=1`):
- `render._scenario_num` + `scenario_compare._val`: known keys first, then a
  generic fallback — any numeric key containing "value" that isn't a
  prob/weight/contribution field. Re-renders fix stored reports at request
  time; **already-downloaded PDFs are static — testers must re-open their
  report link to get the fixed cover.**
- `singlewriter.txt`: machine_readable.scenarios schema now PINNED (3 items,
  `name`/`value_teur`/`probability_pct`, explicit "EI muita avainnimiä kuten
  owner_value"). Reseeded to prod, marker verified in both single-writer
  pipelines. Applies to new runs.
- Regression test `test_cover_chart_survives_scenario_key_name_drift`
  reproduces the real batch shapes. 128 tests pass.

Lesson (repeat of the Virnex table incident): any machine_readable field the
renderer builds visuals from MUST have its schema pinned in the prompt AND a
drift-tolerant reader in render.py — per-generation JSON-shape variance is
normal model behavior, not an anomaly.

## 2026-07-10 (cont. 2) — Kansisivu v3 + Esa traceability fixes + GPT-5.6 Sol trial + rate-limit conflation fix (LIVE)

Continuation of the CEO test-session below (same day, commits `4227552`
through `103385b`, 10:32→15:38). None of this was written up when it shipped —
Railway confirmed live: active deployment commitHash = `103385b`, RUNNING.
126 tests pass.

- **Kansisivu v3** (`08d95b3`): new cover — dark band, meta grid, one hero
  figure, 4-column scenario bar (pessimistic/conservative/expected/optimistic).
  Hero flips from conservative base case to **scenario expected value**
  (378 tEUR for Valuatum, was 256). "Realistinen perusskenaario" renamed to
  **"konservatiivinen perusskenaario"** throughout the active single-writer
  prompt/render/validator path (legacy 6-stage pipeline untouched, unused).
  `stage6_final` validator now checks `cover.headline_value ==
  expected_value_teur` (was `realistic_base_case_teur`). Reseeded + verified
  live (prompt carries 14× "konservatiivinen", 0× "realistinen").
- **Checkout retry bug fixed** (`103385b`) — root cause of the Turun Tislaamo
  incident recurring: demo checkout's `stripe_session_id` substitute is
  deterministic on (company, email), and `/api/public/checkout-generate` was
  idempotent on that id — so a failed first attempt handed the SAME dead run
  back on every retry, forever. Fixed: only reuse an existing order's run if
  it didn't fail; `get_order_by_session` resolves to the newest row (a retried
  session can leave >1 order row). New test
  `test_public_checkout_generate_retries_after_failed_run`. **Does NOT fix
  already-dead links** (e.g. the colleague's old `exp_637de3…&rid=8d49a301…`
  link still points at its dead run forever) — only new company/email combos
  benefit. Client site also fixed in parallel (nonce added to the demo
  checkout call + `demoKey`, commit `55fdf46` in `Company_valuation_nettisivut`).
- **Capital-loan sensitivity spelled out** (`4227552`) — Esa couldn't trace
  the alt-equity figure; rule 25 now forces the subtraction to render inline
  in report text instead of just stating the result.
- **Deterministic EV→equity waterfall chart + scenario table** (`de9b5fa`) —
  §9 gets a waterfall chart (`render._svg_waterfall`) built from the same
  `dcf.bridge` numbers the equity-bridge table already shows; §11 gets a
  Pessimistinen/Realistinen/Optimistinen comparison table
  (`app/scenario_compare.py`) prepended ahead of the section's own prose.
  Both are code-derived (no LLM, no invention risk), injected in
  `assemble.py` the same way `sensitivity.py`'s heatmaps already are.
- **§-cross-references now clickable** (`034c017`) — Esa's actual complaint:
  a correctly-calculated figure (256 tEUR capital-loan case) had no way to
  trace back to where it was derived. "osio N" mentions now render as
  `<a href="#sec-N">` deep links to the section's existing anchor. New prompt
  rule 41 requires every restated key figure to carry a "(ks. osio N)"
  back-reference; rule 25's capital-loan line now points to osio 9 (DCF),
  where the new waterfall lives.
- **Single-writer switched to GPT-5.6 Sol** (`28f1b26`, `06e1e89`) — trial
  swap from `anthropic/claude-fable-5` to OpenAI's Sol (released 2026-07-09)
  as the report writer, at Esa's request. Sol/Terra/Luna added to
  `modelPresets.ts` so any stage can switch to them from the admin dropdown.
  `reasoning_effort=medium` set explicitly for the Sol writer stage (was
  implicit default). Fable re-added to `modelPresets.ts` (it was only ever a
  hardcoded `seed.py` default, so switching away silently dropped it off the
  dropdown — swap back via the dropdown or `seed.py` if Sol's prose quality
  doesn't hold up). **GPT-5.6 pricing note (corrected from an earlier wrong
  assumption this session): Sol is $5/$30 per MTok — MORE expensive than
  Sonnet 5's $3/$15 (intro $2/$10 through Aug 2026), which is the round-2
  writer.** The round-1 Fable→Sol swap does NOT repeat the Fable→Sonnet
  round-2 cost saving, because round-2's baseline was already Sonnet 5.
- **Fixed a global 5-reports/hour ceiling** (`403b0f8`) — `/api/orders` is
  called from the visitor's browser (real per-user IPs), but
  `/api/public/checkout-generate` is called server-side by the client site's
  `/kassa/valmis` Server Component, so every buyer arrives on the same Vercel
  egress IP. Both shared one 5/hour per-IP limiter, which was silently a
  **global** cap of 5 reports/hour site-wide — the 6th buyer got a 429 the
  client swallowed into a generic "raportti toimitetaan sähköpostiisi".
  Split into separate buckets (search 60/min, order 5/h, checkout 40/h); the
  real money guard stays `VALU_DAILY_USD_CAP`. Also exposed
  `paid_rounds_enabled` + `free_rounds_per_report` on `/api/expert/me` so the
  UI stops offering a "buy an extra round" button that dead-ends in a 503
  while Stripe is unconfigured.
- **Raised the search rate limit** (`ca2d21a`) — same IP-conflation bug: the
  client site proxies company search through its own `/api/search` route, so
  60/min was also effectively site-wide, not per-visitor. A handful of
  simultaneous visitors could exhaust it; the client silently falls back to
  the bundled sample catalogue on a 429, so a visitor just quietly doesn't
  find their company. No LLM cost sits behind this endpoint.

**Not yet done:** none of the above have a dedicated live verification run
beyond what the CEO's ordinary testing already exercised (the round-2/cover
work was verified via the CEO's real runs per the section below; the
waterfall/scenario-table/clickable-refs/Sol-swap have NOT been eyeballed on a
real report yet — needs one round-1 generation, paid, needs go-ahead first).

## 2026-07-10 — CEO test-session fixes (round-2 preserve bug + unlimited rounds + error UX)

CEO (Esa) tested overnight/morning; feedback triaged against prod data. Findings + fixes:

1. **Round-2 swallowed the user's correction (worst).** His market-size fix
   (luottotietomarkkina 5 M€ → 30–40 M€) DID reach the round-2 enrichment
   (run `f8430a98`, business_lines sam_teur 5000→30000/40000) but the writer
   kept the old text ("SAM noin 5 000 tEUR") and old scenarios (0/256/1104) —
   maximal-preserve won. Fix: singlewriter rule 0 now has "KÄYTTÄJÄN KORJAUS
   VOITTAA SÄILYTTÄMISEN" — compare every market/TAM/SAM/competitor figure in
   [previous_report] vs fresh [enrichment], propagate diffs through body text,
   optimistic assumptions table AND the optimistic value chain. Deployed + reseeded.
2. **"Osta lisäkierros — 5 €" → 503** (Stripe unset) after 2 free rounds.
   Fix: `ROUND2_MAX_PER_RUN` cap skipped for unlimited keys (generations_limit<=0);
   keyless/admin stay capped (test_round2_cap_skipped_for_unlimited_key).
   Esa's demo key `exp_a6643cc8…` PATCHed to unlimited in prod (new endpoint
   `PATCH /api/access-keys/{key}`), so he can run round-3 on his EXISTING chain.
3. **Evening failure 18:04** was the known enrichment JSON-parse error — happened
   BEFORE the retry-fix (47bad3d, committed 21:57) deployed. Morning runs fine.
   Client now shows a human message + "krediitti palautettu" instead of the raw
   stage error; expertApi parses FastAPI `{"detail":…}` instead of dumping JSON.
4. **Resend bounce** to esam@valuatum.com 04:07 (transient, recipient-side;
   likely greylisting) — that's why he "never got" the round-2 email. The report
   WAS on the /testi page. No code change; watch if it repeats.

Backend deployed + reseeded (marker "KÄYTTÄJÄN KORJAUS VOITTAA" verified in both
single-writer pipelines). Client deployed (Vercel Ready). 117 tests pass. Also
today: QA-pass fixes (EVA plug, signal timing, ranges, null cells — commit
5d19cc7) + morning colleague fixes (dead source links HTTP-validated, emoyhtiö
wording, NACE double code, Kyrö column — commit 80ebec6). Competitor backlog
status + agreed paketti A/B plan in memory (competitor-gap-backlog) — paketti A
approved but NOT started.

## 2026-07-09 (cont. 5) — 🚨 Prompt fixes weren't reaching reports: STALE DUPLICATE pipeline

Root cause found by checking Lauri's actual report output: it had the OLD §14
title and none of the prompt edits (but DID have the deterministic verottaja
fix — that's code, not prompt). **There are TWO single-writer pipelines:**
- `8311e744` "Yhden kirjoittajan raportti (oletus)" — the canonical one; my
  reseed updated THIS (new §14, capital_loans rule, going-concern, 96k tokens).
- `d9173b4f` "Yhden kirjoittajan raportti (oletus, vanha ajohistoria)" — a stale
  leftover (created 07-03, renamed 07-07) with the OLD prompt + OLD 64k tokens.

Lauri's run went to **d9173b4f** (admin test-platform run with an explicit
`pipeline_id`; the default resolver picks 8311e744, but he selected the old one).
`reseed_defaults` only maintains the exact-named `8311e744`, so the duplicate
never got the fixes — a trap.

**Fixed (live, via API — no repo change):** `PUT /api/stages/{d9173b4f stage-2 sid}`
copied 8311e744's writer stage (prompt_template + validator + max_tokens 96k)
onto d9173b4f. Verified: BOTH single-writer pipelines now show §14-NEW=True,
capital_loans=True, old_numbers_directive=True, max_tokens=96000. Any future run
on either is now correct.

**Durable fix DONE (build `2026-07-09-reseed-all-singlewriter`):**
`seed._ensure_single_writer_pipeline` now force-refreshes EVERY pipeline whose
name starts with `SINGLE_WRITER_PIPELINE_PREFIX` ("Yhden kirjoittajan raportti"),
so reseed keeps all single-writer pipelines (canonical + any duplicate) current —
no more drift. Test: `test_reseed_refreshes_all_single_writer_pipelines`.
Reseeded live + verified BOTH single-writer pipelines carry all 5 prompt markers
(§14, capital_loans, going-concern, assumption-edit, old_numbers_directive) +
96k tokens. The duplicate d9173b4f was RENAMED to
"Yhden kirjoittajan raportti — ARKISTO (ÄLÄ KÄYTÄ, vanha ajohistoria)" so it
isn't mistaken for the default (kept the prefix so reseed still syncs it).
No pipeline-delete endpoint exists; renaming + always-current is the safe
equivalent. **Any single-writer pick now produces a correct report** — a
wrong-pipeline run can no longer waste money on a stale prompt.

Canonical to use for runs: **`8311e744` "Yhden kirjoittajan raportti (oletus)"**
(also what `_default_pipeline_id(None)` resolves to, so client-site/self-serve
runs route there automatically).

Note: Lauri's run 1202e59… therefore did NOT exercise the prompt fixes (old
pipeline). A fresh run on either pipeline now will. His deterministic
substanssiarvo/verottaja cross-check DID render (code path).

## 2026-07-09 (cont. 4) — Credit-refund on failure + paid-round toggle + expert UX (LIVE)

Triggered by Lauri's expert-page confusion. Findings + fixes, pushed to both
repos (build `2026-07-09-credit-refund-paid-toggle`), 115 backend tests pass.

Diagnosis of what Lauri saw ("Vaihe 3 epäonnistui $7.0875 ≥ $4.00"):
- That was YESTERDAY's failed Turun tislaamo run (`8d49a30…`, $7.09, when cap was
  $4). It resurfaced because his browser still had `/testi?key=…&rid=…` from
  yesterday (tab restore/history) → `resumeFromLink` loaded that failed run and
  dead-ended. **NOT the email** — a plain key sign-in shows a clean search form.
- His NEW run today (`1202e59…`) **succeeded at $3.5973** (stage2 writer $3.40),
  well under the current **$5.5** cap (verified `VALU_RUN_USD_CAP=5.5` live — the
  earlier "$4" note was stale). The truncation-double-billing fix held. This run
  was the real verification of all the prompt fixes.

Fixes shipped:
- **Credit refund on failure** (`runner.run_stages` finalization + `store.refund_generation`
  / `mark_credit_refunded`): a failed run now gives the generation credit back.
  Guards: only root runs (no `parent_run_id` — round-2 is credit-free) with an
  `access_key` (admin runs have none); params marker prevents restart-trap
  double-refund. Credits live in `access_keys` (`generations_limit/used`); there
  is still NO grant/reset endpoint — to top up a key manually:
  `UPDATE access_keys SET generations_used = 0 WHERE key='exp_…'`.
- **Show-old-numbers toggle now covers PAID rounds too**: flag rides the Stripe
  success_url → client reads it → `RedeemRoundIn.show_old_numbers` → redeem. No
  pending_rounds schema change.
- **Expert UX**: always-reachable "+ Aloita uusi" button in the header (client
  repo) so a failed/finished run no longer dead-ends the page.

Note: `demo:existin` = demo-mode checkout (Stripe unset on client) minting a
single-use `exp_` key that burns its credit at creation.

## 2026-07-09 (cont. 3) — "Show old numbers" toggle added to the CLIENT site (LIVE)

The round-2 toggle now exists on BOTH surfaces. Client repo
`Company_valuation_nettisivut` (Vercel `valuatum-arvonmaaritys`, expert app in
`src/expert/`), commit `aa4da64`: "Näytä vanhat luvut" checkbox in the client
ClarifyPanel → `Round2Body.show_old_numbers` → free round-2. tsc clean; verified
live — string present in the deployed chunk on the **/testi** expert route.
Pushed only after confirming no run was in progress (per owner). Paid extra-round
path still defaults to clean (backend pending path doesn't carry the flag).

## 2026-07-09 (cont. 2) — Cost-page 500 fix + konserni fetch fix (LIVE)

Two more fixes, pushed to main, 114 tests pass.

- **Cost history was blank because `/api/costs` 500'd on Postgres** (pre-existing,
  not this session's cost tab). `costs_summary`'s by_model query had
  `COALESCE(sr.model, st.model, '?')`; the Postgres shim `db._conv` does a blind
  `sql.replace("?", "%s")`, turning the `'?'` LITERAL into a phantom placeholder
  → "1 placeholders but 0 parameters". SQLite tests never hit it. Dropped the
  literal (Python `row["model"] or "?"` already covers the fallback). Commit
  `42aeaa1`. Verified live: `/api/costs` now 200, 29 runs, $34.73 total.
  ⚠️ `_conv` is fragile — any future SQL with a `?` inside a string literal will
  break the same way on Postgres.
- **Konserni fetch fix** (`_derive_company_code`, commit `23cdd69`): it stripped
  the `K` from the y-tunnus, so a konserni FID gave konserni forecasts + a
  "consolidated" label but PARENT-level Profinder actuals. **Verified against
  live Profinder**: `valu_balance_sheet` accepts `16123988K` (consolidated) and
  the dashed `1612398-8K` errors → for consolidated models, append K to the
  dash-stripped code. Parent + override paths unchanged.
- **Answered "which level was Esa's run":** all Valuatum runs were `level=parent`
  (code `16123988`, no K) — **emo, not the konserni he intended**. And konserni
  ≈ parent for Valuatum's balance total (probe: 0.678 vs 0.679 M€) — consolidating
  Valu Properties barely moves the total at this level; Esa should verify which
  konserni figures he expected (subsidiary investment assets may be
  equity-accounted / in a specific line, not the balance total).

## 2026-07-09 (cont.) — Esa/Lauri review fixes (pushed to main; reseed + paid run GATED)

Acted on laurik's task list + Esa's phone notes. All committed & pushed to `main`
(`f190f41` prompts, `130f688` cost tab), 112 tests pass, frontend typechecks.
Railway + Vercel auto-deploying.

Findings first (much was already fixed, only needed verification):
- **§14 heading** already renamed to "MITKÄ TEKIJÄT LIIKUTTAISIVAT ARVIOTA" in
  the live prompt (`prompts/singlewriter.txt:529`). No change needed.
- **Canonical prompt = `pipeline-runner/backend/prompts/singlewriter.txt`** (the
  single-writer flow is prod). Root `valuatum-arvonmaaritys-prompti-v3.md` is
  STALE — do not edit it. Prompt does NOT auto-sync on deploy; needs `POST /api/reseed`.
- **capital_loans 246 vs bridge.interest_bearing_debt 225**: NOT an engine/module
  bug. Engine correctly treats pääomalaina as equity-like (equity_incl =
  equity_excl + capital_loans); bridge never includes it. It's a prompt gap — the
  AI may self-compute "Nettovelka" and fold capital_loans in.

Prompt edits made (singlewriter.txt), reseed pending:
- Rule 25: self-computed Nettovelka must EXCLUDE capital_loans;
  dcf.bridge.interest_bearing_debt is the single EV→equity deduction source; the
  two interest_bearing_debt fields differ legitimately.
- Pessimistic scenario: keep "going concern" term but steer to concrete wording
  ("ohut/negatiivinen oman pääoman puskuri", kassan riittävyys); ban abstract
  "going concern -paine".
- Probabilities: explain how users edit defaults + that it moves expected value.

Cost tab: `💰 Kustannukset` view already existed; added a **Raportti (company)**
column (store.costs_summary now returns company_name, CostOverlay renders it) so
laurik can see which report cost what.

**Old-numbers toggle — BUILT** (owner clarified: a real on/off button). Commit
`a8d1c48`. Old numbers are emergent LLM prose (writer sees `{{previous_report}}`),
so the toggle is a round-2 param → prompt directive, not a deterministic diff:
`Round2In.show_old_numbers` (default False) → cloned run params → runner sets
`{{old_numbers_directive}}` (show "vanha → uusi" vs suppress) → singlewriter round-2
rule. Admin UI: "Näytä vanhat luvut" checkbox in ClarifyPanel. Default False =
clean report. 113 tests pass, frontend typechecks. Build marker →
`2026-07-09-review-fixes-toggle`.

Owner Q&A (verified in code):
- **Paid loop already exists.** Round-2 = a few free refinement rounds
  (ROUND2_MAX_PER_RUN=2), then €5/round Stripe-gated unlimited
  (`/round2/checkout` → `/redeem`, "Arvonmäärityksen lisätarkennuskierros").
- **Assumption-editing UI does NOT exist in this repo.** Only the round-2
  ClarifyPanel free-text path (type assumptions → LLM re-run). The report's
  "muokata Valuatumin järjestelmässä" promise has no backing slider/number UI;
  that would live in the SEPARATE client-site repo (valuatum-arvonmaaritys.vercel.app),
  not this workspace. Real gap — decide: build the editor (client repo) or soften
  the promise. Probabilities specifically could be a deterministic slider (E[V] =
  Σ p×value, no LLM) — cheap future win.
- **Emo/konserni already carried.** `meta.level` auto-derived from the Valuatum
  company code's `K` suffix (`export_modeldata_json.py:329`); report states it
  (rule 31). Latent bug: `_derive_company_code` (valuatum.py:174) strips the `K`
  so consolidated models can get PARENT actuals from the Profinder backfill —
  NOT fixed (touches external API, K-convention untested; needs a live check).
  **Subsidiary data (Valu Properties Oy) is not in any fetch path** — genuinely
  needs Valuatum to expose it or you supply it as `user_input`. Not derivable.

Reseed: owner authorized it directly (full control, no Esa confirmation needed).
Running `POST /api/reseed` once the new build (`2026-07-09-review-fixes-toggle`)
is live on Railway (poll `/api/health`). Reseed is free (no LLM).

**Pick up here:** owner runs ONE round-1 generation on the TEST platform himself
once reseed is confirmed live — verifies all prompt edits (capital_loans/nettovelka,
going-concern wording, §14, assumption text) on a real report. Then a round-2
run with the "Näytä vanhat luvut" checkbox to verify the toggle both ways.

## 2026-07-09 — Competitor analysis + verottaja cross-check bug fix (committed, NOT prod-verified)

Full competitor analysis of the AI/company-valuation field (Esa/colleague's
list): 11 sources researched via a 23-agent workflow — 8 competitors (Equidam,
BizEquity, Eqvista, BVO, ValuAdder, Asiakastieto, Arvento, Rotio) + 3
best-practice standards (sell-side equity research, IVS/USPAP/NACVA formal
appraisal, PitchBook). Full report, gap register (37 gaps), prioritized action
list and "do NOT copy" list in **`kilpailija-analyysi-2026-07-09.md`** (repo
root). Raw per-competitor data: session scratchpad `tasks/wy8zs1lhh.output`.
Caveat: public-web research only — the actual competitor PDFs the colleague
collected were not available; drop them in the repo for a tighter diff.

**Shipped autonomously (overnight, user-approved for clear wins): one bug fix.**
Commit on `main`, 112 tests pass, verified on real model-data locally — NOT run
against prod (no paid generation while user asleep, per CLAUDE.md rule).
- `valuation_equivalence._verottaja_blocks` read `net_income` / `equity`, but
  real Valuatum model-data uses `net_earnings` / `equity_excl_capital_loans`.
  The block silently returned `[]` on **every real report** — the intended §8
  verottajan-malli / substanssiarvo cross-check (our built-in answer to two of
  the most-cited competitor gaps: Asiakastieto/Arvento substanssiarvo +
  tuottoarvo) never rendered. Fixed keys (+ legacy fallback), floored
  substanssiarvo at 0 (Verohallinto rule + no negative reference for distressed
  cos). Real-data check: block now injects into §8 (e.g. teippimestarit →
  substanssiarvo 326, tuottoarvo 284, käypä 326 tEUR).

**Deliberately NOT auto-shipped** (await user go-ahead — each would override an
existing design decision or add a headline-adjacent number unsupervised):
- Implied-multiple line (EV/EBITDA, EV/liikevaihto of our own value) — §8. #1
  advisor ask; pure derived ratio, but needs a which-year-EBITDA decision.
- Method football-field chart (§8) and forward-projection chart (§6) — SVG
  helpers (`_svg_hbars`, `bar_line`) already exist; BUT §8 "deliberately does
  not inject derived visuals" (render.py:1426-1430) and the spec mandates no
  forecast chart. Both are deliberate choices — don't override unsupervised.
- Everything else (owner-earnings normalization, industry benchmarking to fill
  the `peers=[]` slot = the single biggest opportunity, expanded ratios,
  standard-of-value block, IVS statement, SWOT, glossary) → prioritized in the
  analysis doc §4. Most are prompt-layer → need a paid LLM run to verify.

**Pick up here:** (1) review the analysis doc's quick-win list and greenlight
which to build; the implied-multiple line + owner-earnings normalization are the
two cheapest credibility wins. (2) Biggest bet: populate the already-built
`peers=[]` slot with sourced Finnish sector medians — turns our most visible
stated weakness into a strength. (3) The verottaja fix is deterministic +
test-covered; to see it live, free `GET /api/runs/{rid}/report.html?force=1` on
any real run (no cost) — no prod generation needed.

## 2026-07-08 (cont. 3) — Full design review + 10 fixes incl. cover v2 (LIVE)

Screenshot-by-screenshot review of the re-branded report; all findings fixed in
the render layer (commits `455c12a`, `d92a074`, build
`2026-07-08-design-review-fixes-2`, 112 tests pass, verified on live Athlos
re-render + readiness ready:true):
- **Cover v2 implemented** (CEO-approved mockup): dark brand band, one hero
  figure, scenario range track with markers, plain-language legend, "Voit
  muuttaa oletuksia" note. Old cover CSS + dead colophon removed. NOTE: cover
  colophon/sign-off block is GONE by test contract
  (test_cover_cleans_industry_and_omits_trust_boilerplate).
- Range track needs scenarios: `_scenario_values` falls back to
  `machine_readable.scenarios` (single-writer runs have no `_scenarios`
  sidecar) — without this the track silently vanished on all real reports.
- `_heat_color` gradient red→pale→sage (old lime was hardcoded); white cell
  text at both dark ends.
- Combo-chart line axis outlier-robust (median±6·MAD, `_axis_vals`); off-axis
  points clamp to the edge as dashed markers.
- `_num_cell` colours only figure-cells (prose with a negative number no
  longer turns red).
- `_dedup_captions`: table title that repeats the heading above is dropped.
- key_value prose values stack under the label (`kv kvl`); paragraphs capped
  at 72ch; TOC rows are anchor links (`#sec-N`); page-break/min-height rules
  print-only (screen flows without fake page gaps).

## 2026-07-08 (cont. 2) — Sage accent + table-layout fix (LIVE)

CEO disliked the gold accent and the broken prose tables. Commit `989e5f6`,
build `2026-07-08-brand-refresh-2`, 111 tests pass, verified on a live Athlos
re-render:
- Accent gold → tonal sage green (`#4F7A6A`/`#33604F`) — single-hue palette
  with `#12352B`; section chips and chart segment labels now white-on-fill.
- Table root cause: `overflow-wrap:anywhere` collapsed min-content widths to
  ~1 char, starving the first column (mid-word breaks). Now `break-word`.
  The two remaining `anywhere` uses (mcard values, wide-table headers) are
  intentional.
- `_render_table` alignment is content-aware (`_col_aligns`): only columns
  whose cells are mostly figures stay right-aligned; prose columns left.
- Cover mockup v3 (sage accent): same artifact URL as below.

## 2026-07-08 (cont.) — Brand refresh + cover mockup v2 (LIVE)

New Valuatum brand applied to the whole report (`render.py`, commit `f626379`,
build `2026-07-08-brand-refresh`): primary green `#12352B`, warm gold accent
(`#D9973B`/`#B87A22`) replacing the old lime, display face Georgia (Gelasio
webfont fallback for the PDF container), body = system sans. `C["lime"]` key
kept but now holds the gold. Verified on a real prod Athlos re-render
(`report.html?force=1` — old `#A6CE39`/Archivo absent, screenshot checked).

Cover mockup v2 (user feedback: no overlapping texts, no boxes, brand color):
https://claude.ai/code/artifact/cc8e4f8d-1648-45e3-ad06-55a9aa695347 —
range-bar tags now all below the track on two staggered rows; explanations are
hairline legend rows. Source: session scratchpad `kansisivu-mockup.html`
(not in repo — copy it in if the design is approved for implementation).

## 2026-07-08 — $7.09 run explained + cost guards (LIVE)

Colleague's Turun tislaamo run (`8d49a301…`) cost **$7.09 and failed at "Vaihe 3"**.
Root cause chain (verified from the run record, not guessed):

1. Fable writer's first call hit the 64k `max_tokens` cap (`finish_reason='length'`
   — hidden thinking tokens count toward the cap; visible JSON was only ~59k chars).
2. The truncation retry in `runner._execute_stage` re-ran the WHOLE stage at 120k
   — paying the full 47k-token prompt again. Stage totals: 94,216 prompt +
   120,284 completion tokens = **$6.956 exact** at Fable's $10/$50 per MTok.
3. A stray empty stage **"Stage 3 – new"** (blank prompt, gemini-flash — an
   accidental admin-UI add) then hit the pre-stage spend-cap check → run failed.
   Without it the run would have completed OK at $7.09. (The July 4 runs at
   $6.4–6.65 were almost certainly the same truncation-double-pay pattern.)

Fixed (commit `286401e`, build `2026-07-08-cost-guards`, 110 tests pass):
- truncation retry now checks `_spend_cap_exceeded(rid)` before re-paying;
- enabled model stages with a blank prompt are skipped, never executed;
- writer `max_tokens` 64k → 96k (headroom so 'length' never fires; reseed applied
  to prod — verify with `GET /api/pipelines`, stage 2 shows 96000);
- heavy-stage HTTP timeout 1500s → 2700s (a timeout retry also re-bills);
- rogue "Stage 3 – new" DELETEd from prod pipeline `8311e744`.

Open: `VALU_RUN_USD_CAP` is still the default $4.0 (env unset) — a legit
long-thinking Fable run can now cost up to ~$5.3 in ONE call, which the cap only
catches afterwards but which then blocks round-2 on that run. Decide whether to
set VALU_RUN_USD_CAP=5.5 in Railway. Esa's 6 improvement points triaged in chat
2026-07-08 (cover redesign / assumption-editing guidance / old-numbers toggle =
feature decisions; going-concern wording + §14 heading = small prompt edits;
emo-vs-konserni = already handled via `meta.level` + prompt §31).

## 2026-07-07 (cont.) — Athlos reviewer fixes + report QA net (LIVE + verified)

Acted on a detailed external review of the Athlos report (14 errors + 7 agent
suggestions). The reviewed PDF was **stale** (predated commit `b0af93d`), so
several errors were already fixed: sensitivity calibration (526≠669), the DCF/EVA
method table, and the cover single-anchor. Remaining live issues fixed.

Commits on `main`: `b4abfbe` (fixes + QA net), `c00d4ee` (build marker),
`c2695d3` (strip+replace baked DCF blocks). Live build: `2026-07-07-athlos-review-fixes-2`.
Prompts **reseeded** (`POST /api/reseed`) — production pipeline
`Yhden kirjoittajan raportti (oletus)` (id `8311e744`) carries all edits.
108 tests pass. Verified live on a real Athlos re-render (GET, no generation).

Deterministic / rendering (effect immediately, incl. re-renders):
- `dcf_detail.py` — "Kumulatiivinen diskontattu FCFF" is now a true forward
  running sum (was the engine's mislabeled remaining-value series); DCF callout
  explains the tax-row sign convention.
- `valuation_equivalence.py` — EVA narrative matches its straight-to-equity table
  (dropped the false "yritysarvo minus nettovelka" claim).
- `assemble.py` — sensitivity and DCF-detail injection now **strip+replace**
  (idempotent, refresh stale baked blocks) instead of append/skip; new
  `_inject_dcf_caveats` adds the WACC + terminal-EBIT blocks to §9.
- `render.py` — wide-table headers wrap (no more "Oma pääoma ilmaKnop…" collision);
  `_resolve_section_refs` remaps `osio N` prose refs from internal-id to display
  numbers (fixes the systematic off-by-one); cover headline relabeled
  "Oman pääoman arvo (realistinen perusskenaario)".
- `dcf_detail.py` + `sensitivity.py` — new WACC-vs-credit-risk caveat and an
  alternative terminal-EBIT-margin value range (both fire on Athlos; numbers
  unchanged — engine outputs are never overwritten).

New `app/report_qa.py` — advisory QA over the ASSEMBLED report (duplicate-block
hash, sensitivity center-vs-headline calibration ±5%, euro-figure reconciliation),
wired into `store.report_readiness` (surfaced via `GET /api/runs/{rid}/readiness`
as `warnings`), **never blocks** a delivery. Skipped two reviewer-suggested checks
on purpose: cumulative-monotonic (a signed-cashflow cumulative is legitimately
non-monotonic) and data-layer corrupted-header (that collision is a CSS artifact,
fixed at the source).

Prompt edits (`singlewriter.txt`, apply to **new runs only**): forbid DCF/EVA
weight splits, drop the stale "(painotettu)" card + competing EV/odotusarvo cards,
terminology vocab, optimistic scenario must discount to the realization year with
forecast net debt + dilution, warn when one scenario drives >70% of expected value,
bridge annual credit risk to a cumulative probability.

Open / notes:
- Prompt changes are unverified on a live run (won't generate on prod without
  approval). Do one test generation when convenient to eyeball scenario/weighting.
- QA prose-reconciliation has minor false positives (e.g. flags the Y-tunnus
  `2752258`) — advisory only, tune later.
- Archived pipeline `Yhden kirjoittajan raportti (oletus, vanha ajohistoria)`
  still has the old prompt — unused (prod routes to the exact `(oletus)` name),
  harmless.
- #13 peers left as the accepted limitation (engine hardcodes `peers: []`).

## Project map — repos, local paths, live URLs

Two GitHub repos, worked on together as one product (this machine is
Windows; paths below are for it):

- **Backend + admin runner** — `github.com/ValuatumOy/AI-company-valuation-raportti`
  (this repo). Local: `C:\Users\Lauri H\Desktop\Valuatum projektit\AI-company-valuation-raportti`.
  - `pipeline-runner/backend` — FastAPI, deploys to Railway
    (`https://valu-pipeline-production-88f2.up.railway.app`, project
    `valu-pipeline`, service `valu-pipeline`, env `production`). Push to
    `main` auto-redeploys in ~45s.
  - `pipeline-runner/frontend` — the operator/admin tool (Vite/React),
    deploys to Vercel at `https://frontend-one-phi-77.vercel.app`.
- **Client site** — `github.com/ValuatumOy/Company_valuation_nettisivut`
  (recently moved from the `Valuatum` org — `git remote` may still point
  at the old `Valuatum/Company_valuation_nettisivut` URL and redirect; fine
  to leave as-is, GitHub forwards it). Local:
  `C:\Users\Lauri H\Desktop\Valuatum projektit\Company_valuation_nettisivut`.
  Next.js, deploys to Vercel at `https://valuatum-arvonmaaritys.vercel.app`.
  Push to `main` auto-redeploys.
  - `/` — public marketing site + Stripe purchase flow (BuyBox → operator
    fulfils manually, not self-serve yet).
  - `/testi` — the expert self-serve interface (`src/expert/ExpertApp.tsx`),
    invite-key gated (`exp_...`), this session's main focus. This is the
    interface intended to become the production customer-facing flow.

Both repos have their own `HANDOFF.md` (this file, and
`../Company_valuation_nettisivut/HANDOFF.md`) — the nettisivut one is kept
short and points back here for anything cross-repo.

## ⛔ Never run a report generation against prod without asking first — see CLAUDE.md
This includes "just a verification run" suggested by a previous handoff's
"pick up here" section. Ask, then run. The user said this explicitly, twice,
in the same session — treat it as non-negotiable, not a one-off preference.

## What shipped this session

Context: CEO tested `/testi` (the nettisivut expert self-serve page) and
reported two problems: (1) he never saw the round-1 report, only the AI's
clarifying questions and then the round-2 result, and (2) there was no
free-text company entry — he was stuck picking from the operator's
pre-fetched company list. Both are now fixed. A verification run to prove
it end-to-end has **NOT** been done — see "Pick up here".

**Backend (`AI-company-valuation-raportti`, commit `7e419b8` + latest continuation commit):**
- **New `GET /api/company-search?q=...`** (`app/valuatum.py:search_company`,
  wired in `app/main.py`) resolves a company name or Finnish y-tunnus to
  Valuatum FID(s) via the configured Valuatum REST API
  (`{VALUATUM_API_BASE_URL}/company`, same `VALUATUM_TOKEN` auth
  as the existing `/rest/modeldata` calls in `valuatum_kit/fetch_modeldata.py`).
  This is what unblocks self-serve for ANY company — `POST
  /api/expert/generate` already accepted any `fid`; the pre-fetched-company
  picker was the only actual blocker, not a backend limitation. Verified
  live against prod: `curl -H "Authorization: Bearer $APP_TOKEN"
  ".../api/company-search?q=Valuatum%20Oy"` and `?q=1612398-8` both return
  the correct 4 model candidates (parent + "K"-suffix group company, each
  with a "Profinder" auto model and a "Niklas Mäki" manual model). fid=184362
  ("Profinder" model, parent company code) is the one used all along.
  Now maps `fid`/`company_name`/`company_code`/`industry_text`/
  `industry_code`/`industry_id`/`industry_tree`/`analyst_name`.
  `POST /api/expert/generate` accepts those industry fields, the `/testi`
  frontend forwards them, and stage 0 overlays them into `input_data.meta`
  (`industry`, `industry_code`, `industry_id`, `industry_tree`) before the
  report writer sees the FAKTAT JSON. `export_stream` also does a best-effort
  `/rest/company` metadata lookup by FID for admin/operator exports, so the
  old `meta.industry = None` gap should be closed when Valuatum can match the
  FID.
  Added to the expert GET allowlist in `main.py` (`_EXPERT_GET` regex).
- **Fixed the round-2 refinement cap** (`app/store.py:lineage_depth`,
  used in `main.py`'s `round2_run`). The old check
  (`store.count_children(rid)`) counted a run's OWN direct children, which
  is always 0 for a freshly created run — so refining round 2's own result
  again (a chain: R1→R2→R3→...) never actually hit the "2 tarkennuskierrosta
  sisältyy" cap, since each new node in the chain started with a fresh
  zero count. Now walks `parent_run_id` back to the root and caps on chain
  depth, so round 2 and round 3 succeed (2 refinements) and a 3rd refinement
  attempt correctly 429s. New regression test:
  `test_round2_cap_bites_across_a_refinement_chain`.
- Tests: 98 passed, including industry metadata mapping, expert generate
  params, round-2 cap, and a fake Resend email-with-PDF-attachment test.
- **Email delivery scaffold is now built but inert until env vars are set.**
  New `app/email_delivery.py` sends finished reports through Resend's REST API
  after `_drive_run` finishes with status `ok`, but only when the run has
  `params.delivery_email` and Railway has `RESEND_API_KEY` plus
  `REPORT_EMAIL_FROM` (or `RESEND_FROM`). `REPORT_EMAIL_ENABLED=0` disables it.
  It attaches the generated PDF; if PDF rendering fails it falls back to an HTML
  attachment. `/testi` now has an optional email field that becomes
  `delivery_email`. No real email has been sent and no provider env vars were
  configured in this session.

**Frontend (`Company_valuation_nettisivut`, commit `c9565fa`):**
- `src/expert/ExpertApp.tsx` — reordered the round-1/round-2 display: the
  report iframe + PDF buttons + a clear "Ensimmäinen versio" / "Tarkennettu
  versio" heading (derived from `run.parent_run_id`) now render FIRST,
  with the `ClarifyPanel` (amber, asks for round-2 input) clearly separated
  BELOW it with its own "Haluatko tarkentaa raporttia?" heading. Previously
  the panel was rendered above the iframe in the same block — since it's a
  large, attention-grabbing amber box, this is almost certainly why the CEO
  said he never saw the round-1 report: he likely answered the questions
  without scrolling down to the iframe below. (The report was always being
  fetched and set in state correctly — this was a display-order/prominence
  issue, not a fetch bug.)
- Replaced the `<select>` dropdown of pre-fetched companies with a
  free-text "Yritys (nimi tai y-tunnus)" input + "Hae" button, calling the
  new `/api/company-search`. Shows a picker list when a search returns
  multiple model candidates (see the 4-candidates-per-company note above),
  auto-selects when there's exactly one, now displays the industry label in
  candidate rows, and forwards `industry_text`/`industry_code`/
  `industry_id`/`industry_tree` to the backend. `src/expert/expertApi.ts` has
  the new `searchCompany()` + `CompanyCandidate` type. The form also has an
  optional delivery email field; this only sends after backend env vars for
  Resend are configured.
- Also fixed a latent bug in `src/components/InlineMd.tsx` (module-level
  regex `.lastIndex` reset on every render — shared mutable state, unsafe
  under concurrent renders); made the regex local to the component. Small,
  unrelated, found while touching nearby files.
- Build + typecheck clean (`npm.cmd run build` on Windows; `npm run build`
  hits the local PowerShell execution-policy block), no test suite in this repo.

## What was NOT verified this session (important)

- **No live end-to-end run was completed against prod with the new code.**
  An earlier verification run (`bab71ae97c324dde98c1411fbaa69259`, started
  before the user said "no test runs yet, changes first") errored out
  mid-flight — turned out to be because a `git push` to this repo's `main`
  triggers a Railway auto-redeploy, which killed the in-flight background
  task (`_RUN_TASKS` lives in-process; a redeploy restarts the process).
  **Lesson: don't push to this repo's `main` while a run is in flight** —
  check `GET /api/runs` for any `"status":"running"` first (I now do this
  before every push).
- The `/testi` UI reorder + company search were verified by: (a) `npm run
  build` + TypeScript passing, (b) the company-search endpoint curled
  directly against prod (see above, worked correctly), (c) a **mocked**
  browser session (real Next.js dev server, `window.fetch` intercepted
  with canned JSON responses standing in for the backend) exercising
  sign-in → search → multi-candidate picker → select → generate → poll →
  round-1 report renders first with correct heading → clarify panel below
  it. This proves the React logic is correct but is NOT the same as a real
  run against real data. **A real live click-through on
  https://valuatum-arvonmaaritys.vercel.app/testi (or the local dev
  environment, see below) with a real report is still owed** — ask the
  user before spending the money on it (see the ⛔ rule at the top).
- Browser tooling was unusually painful this session on this Windows
  machine, worth knowing for next time:
  - The claude-in-chrome MCP extension was disconnected all session
    (retried 3×, never connected) — if it's connected next time, prefer it
    over everything below, it can hit the real deployed URL directly with
    real CORS/cookies.
  - Local `next dev` (Turbopack) hard-fails on this machine's actual repo
    path (`C:\Users\Lauri H\Desktop\Valuatum projektit\Company_valuation_nettisivut`)
    because of the spaces in `Lauri H` / `Valuatum projektit` — tried `subst
    V:`, a Windows 8.3 short path, and a junction for `node_modules`; all
    hit different Turbopack "leaves the filesystem root" panics. **What
    actually worked:** `robocopy` the whole repo (excluding
    `node_modules`/`.next`/`.git`) to a spaceless path (`C:\dev\nettisivut`)
    and run a real `npm install` there (junctioning `node_modules` back
    also fails the same way — it has to be a real install in the spaceless
    tree). `.claude/launch.json` (at `C:\Users\Lauri H\.claude\launch.json`)
    is already configured to `cd /d C:\dev\nettisivut && npm run dev`. Keep
    that copy in sync manually (`robocopy` again) after editing the real
    repo, or edit directly in `C:\dev\nettisivut` and copy changes back —
    it's NOT a git checkout, just a build copy, don't commit from there.
  - Even with the dev server working, a browser hitting `localhost:3000`
    can't call the real prod backend — `ALLOWED_ORIGINS` on Railway is set
    to something that only matches `https://*.vercel.app` (not
    `localhost`), so the browser's CORS preflight 400s. Either mock
    `window.fetch` (what I did) or test against a real Vercel deploy URL
    instead of local dev.
  - Railway CLI (`railway ...`) is NOT installed/linked on this Windows
    machine — a previous handoff describing it as "already authenticated"
    was written on a different (Mac) machine. If you need Railway env vars
    or `railway variables --set`, you'll need to `railway login` fresh
    here first (interactive — ask the user to run it).

## Pick up here

1. **Get explicit user approval, then run one real round-1 + round-2 report
   through `/testi`** (or the admin runner) to confirm: the free-text
   company search actually flows through to a real generation, the
   round-1 report is now clearly visible before the clarify panel,
   `input_data.meta.industry*` is populated from `/rest/company`, optional
   email delivery works when env vars are configured, and the round-2 cap
   behaves (2 refinements allowed, 3rd blocked). Check `GET /api/runs` shows
   nothing `"status":"running"` before pushing anything to this repo's `main`
   while that run is live.
2. **Configure email delivery if/when the user wants it live:** choose/verify
   the sender domain in Resend, then set Railway `RESEND_API_KEY` and
   `REPORT_EMAIL_FROM` (or `RESEND_FROM`). No env vars were set here and no
   email was sent. The code will send both round-1 and refinement outputs when
   `delivery_email` exists because round-2 inherits parent params; change this
   if the product decision becomes "final only".
3. Still open after this session: signed report links/accountless paid
   self-serve, Stripe webhook durability, real upload storage, friendly UI copy
   for paused/429 errors, and the live `/testi` verification above.

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
  probe against VALUATUM_MCP_URL (secret on Railway only) to see if a
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

## 2026-07-07 (cont., other machine) — Resend email delivery activated

`RESEND_API_KEY` and `REPORT_EMAIL_FROM=Valuatum <reports@valuatum.com>`
set in Railway (`valu-pipeline` prod service), confirmed via `railway
variables`. Redeploy confirmed healthy (`build:
2026-07-07-industry-metadata`). No code changes — `app/email_delivery.py`
was already built and dormant; this just turns it on. **Not verified
live** — no report run has actually gone through Resend yet, so PDF
attachment delivery is unconfirmed end-to-end. Next: run one real
`/testi` generation with `delivery_email` set (ask before running, per
the rule above) and confirm the email arrives with the PDF attached.

## 2026-07-07 (cont.) — PDF export crash fixed (no D-Bus in the container)

`render_pdf` in `app/render.py` was crashing on every run: Chrome's
headless `--print-to-pdf` tries to connect to `/run/dbus/system_bus_socket`,
which doesn't exist in the Railway Docker image (`ERROR:dbus/bus.cc:405`).
Fixed by setting `DBUS_SESSION_BUS_ADDRESS=/dev/null` in the subprocess env
and adding `--disable-dev-shm-usage`. Commit `72cb99c`. Tests pass (98);
**not yet verified against a real live PDF export** — next run should
confirm a PDF actually downloads/attaches, not just that Chrome exits 0.

## 2026-07-07 (cont.) — Public paid checkout now auto-generates (was manual)

Closed the client-site gap from the "colleague-list execution" section
above: the public homepage → company page → Stripe checkout flow
("existing financials" product only) now auto-starts generation instead
of an operator fulfilling by hand.

**Backend** (`cb809ad`): new `POST /api/public/checkout-generate`
(unauthenticated, same honeypot + IP rate limit as `/api/orders`,
idempotent on `stripe_session_id` so a page reload doesn't double-generate
or double-mint a key). Resolves the paid `business_id` (y-tunnus) to a
Valuatum FID via `valuatum.search_company` (heuristic pick when there's
more than one candidate model — see `_pick_checkout_candidate` in
`main.py`, documented as a ponytail-tagged guess, not guaranteed correct),
mints a single-use `access_key` (`generations_limit=1`, consumed
immediately), and starts the run with `delivery_email` set so the
existing Resend code actually fires. Orders table gained
`stripe_session_id`/`access_key`/`run_id`/`fid` columns
(`create_paid_order` in `store.py`) so the operator dashboard still sees
these alongside manually-fulfilled orders. `email_delivery.py`'s
`send_report_ready` now includes a `{CLIENT_SITE_URL}/testi?key=&rid=`
link in the email body when the run has an access key — `CLIENT_SITE_URL`
is now set in Railway (`https://valuatum-arvonmaaritys.vercel.app`). New
test: `test_public_checkout_generate_mints_key_and_starts_run` (covers the
mint-and-consume ordering bug I caught myself: the checkout call must
consume the generation itself, or the same key could still fire a free
`/api/expert/generate` afterward). 99 tests pass.

**Frontend** (`Company_valuation_nettisivut@6952ee0`): `BuyBox.tsx` gained
a free-text "info the AI can't find" field and now sends `businessId`
(y-tunnus) through checkout metadata (Stripe metadata values are capped at
500 chars, DB/model allow 4000 — only the Stripe round-trip is truncated).
`kassa/valmis/page.tsx` calls the new endpoint for `kind==='existing'`
only (`import`/`creditsafe` still need a human — upload/Creditsafe fetch
isn't wired to auto-generation) and shows a live "Seuraa raporttia tästä"
link plus an excl@valuatum.com contact line on the thank-you page.
`ExpertApp.tsx` (`/testi`) now reads `?key=&rid=` from the URL on mount
and jumps straight into that run (`resumeFromLink`) instead of requiring
manual key re-entry — this is what makes the emailed/on-screen link
actually useful. Also added a persistent "if something goes wrong,
excl@valuatum.com" footer line. `npm run build` + `tsc --noEmit` clean
(one pre-existing unrelated error in `.next/dev/types` about
`asiantuntija/page` — not caused by this change, noted in an earlier
session too).

**Not verified / open:**
- No real checkout has been run through this path yet (ask before running
  — same rule as report generations).
- **`STRIPE_SECRET_KEY` is still not set on Vercel** (see "Deferred / not
  done" above) — so the client site is still in demo-checkout mode in
  production. This code is ready for real payments but real money won't
  flow until that key is set — that's a decision for whoever owns the
  Stripe account, not something to flip unasked.
- The FID-resolution heuristic (`_pick_checkout_candidate`) has never
  been exercised against a real ambiguous multi-model company end-to-end
  in this flow — only in the unit test's single-candidate case.
- `import`/`creditsafe` checkout kinds are unchanged (still manual) —
  full auto-generation for those needs the file-storage and Creditsafe
  work noted elsewhere in this file first.
