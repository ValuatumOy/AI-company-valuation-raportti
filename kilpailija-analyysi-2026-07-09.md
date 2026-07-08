# Competitor Analysis — Valuatum AI Valuation Report
*2026-07-09. Decision-ready synthesis across 8 competitors + 3 professional-standard benchmarks.*

**Method:** multi-agent web research (23 agents) — one researcher + one gap-analyst per source, then a synthesis pass. Sources: Equidam, BizEquity, Eqvista, Business Valuations Online (BVO), ValuAdder, Asiakastieto Arvoraportti, Arvento, Rotio, plus best-practice standards (sell-side equity research, IVS/USPAP/NACVA formal appraisal, PitchBook).

**Limitation:** research is public-web based — I did **not** have the actual competitor PDFs the colleague collected. Feature claims are the researchers' characterizations, not verified against live products. Pricing is inferred except where flagged. Drop the collected PDFs in the repo for a tighter line-by-line diff. Raw per-competitor gap data: `tasks/wy8zs1lhh.output` (session scratchpad).

---

## 1. Landscape

The field splits into two camps. **PDF-report producers** (Asiakastieto Arvoraportti, ValuAdder, BVO, sell-side equity research, formal IVS/USPAP appraisals, Eqvista's report tier) hand over a finished document; **interactive platforms** (Equidam, BizEquity, Rotio, Arvento, PitchBook) put a live-recomputing calculator on screen and treat the PDF as a byproduct. We sit in the first camp by delivery (a polished PDF/HTML deliverable) but carry the second camp's ambition — editable scenario probabilities and assumptions — without yet exposing them interactively. Our real methodological rigor (scored multi-method weighting, DCF transparency, probabilistic expected value) is closer to the *formal appraisal* gold standard than to any self-serve tool, but delivered automatically and cheaply.

**Closest competitor: Asiakastieto Arvoraportti** — the only rival that, like us, is Finnish-native, works from filed statements, and outputs a structured valuation report for the exact same Finnish SMB audience. Its moat (industry-median benchmarking off a proprietary all-Finnish-companies database) is precisely the thing our report most conspicuously lacks. **Arvento is the closest AI-native analogue** (Finnish, AI-generated, Y-tunnus auto-fetch, succession-tax method) and is arguably the more direct product-shaped threat; watch both.

---

## 2. One line per source

*Pricing largely inferred — flagged ⚠.*

| Source | Format | Data sourcing | Pricing | Single most distinctive thing |
|---|---|---|---|---|
| **Asiakastieto Arvoraportti** | PDF report | Filed statements (own DB) | Per-report ⚠ | Industry median overlaid on *every* metric (all-Finnish-companies DB) |
| **Arvento** | Interactive + report | Y-tunnus auto-fetch (Taloustutka/YTJ) | 19€/mo + one-off | Finnish AI with tax-authority succession method + Damodaran multiples |
| **Equidam** | PDF + XLSX + MD + on-screen | Manual founder entry | Freemium tiers ⚠ | 5-method weighted avg benchmarked to 140k+ private companies |
| **BizEquity** | Web + PDF | ~150 hand-keyed fields | Subscription/white-label ⚠ | 4 value conclusions mapped to use cases (asset/equity/enterprise/liquidation) |
| **Eqvista** | PDF + dashboard | 32-question questionnaire | Tiered (Growth/Advanced) ⚠ | Pre-revenue startup methods (Berkus/Scorecard/VC) |
| **BVO** | Human-prepared PDF | Xero/MYOB + questionnaire | AUD 1,399–4,499 | Signed forensic-accountant report with industry cost-structure benchmark |
| **ValuAdder** | Editable Word/PDF | 100% manual appraiser | $375–675 perpetual license | Full method toolkit + SDCF recast + editable appraisal builder |
| **Rotio** | Web cards (no charts) | 5 typed figures + 3 dropdowns | Free + lead-gen upsell | 2-minute instant multiplier estimate |
| **Sell-side equity research** | Prose PDF | Analyst manual | Institutional ⚠ | Listed-peer comps + football-field range + Buy/Hold/Sell |
| **Formal appraisal (IVS/USPAP/NACVA)** | Signed PDF | Analyst manual | ~$3k–50k | Credentialed human sign-off, court/tax-admissible |

---

## 3. Gap register (deduped, sorted by relevance then effort)

"Common" = shared by 3+ sources. Effort: quick / med / big.

| # | Gap | Who has it | Why it matters | Rel | Effort |
|---|---|---|---|---|---|
| 1 | **Implied multiple of our own value** (EV/EBITDA, EV/Sales) as a sanity line — descriptive, not invented | Rotio | Every advisor asks "so what multiple is this?"; instant gut-check, no data sourcing | H | quick |
| 2 | **Tax-authority / succession valuation** (verottajan malli: avg of substanssiarvo + tuottoarvo@15%) | Asiakastieto, Arvento | Succession & gift/inheritance tax is a top reason Finnish SMBs get valued; statutory rate, no invention | H | quick |
| 3 | **Expanded ratios** — DSO/DPO/CCC, quick ratio, ROA, ROIC/ROCE vs WACC, common-size % columns | BVO, Asiakastieto, sell-side, formal (common) | All fall inside "simple derived ratio"; ROIC-vs-WACC tells whether growth creates or destroys value | H | quick |
| 4 | **Table of contents / in-report navigation** | Eqvista, ValuAdder | 16-section report is long; ToC makes it navigable and feels finished | H | quick |
| 5 | **Goodwill vs net-tangible-asset split** / substanssi-vs-tuottoarvo decomposition | BVO, Asiakastieto, ValuAdder (common) | Drives asset-deal vs share-deal structuring & tax; simple subtraction from data we hold | H | quick |
| 6 | **Guaranteed substanssiarvo / net-asset reference line** in every report | BizEquity, ValuAdder, Arvento, Rotio, Asiakastieto, formal (common) | Finnish sellers/buyers anchor on it as a floor; absence reads as incomplete | H | quick |
| 7 | **Standard-of-value + premise + as-of date + scope/reliance statement** | ValuAdder, formal, Eqvista (common) | A number is meaningless without saying which value it is; nearly free framing text | H | quick |
| 8 | **Owner-earnings normalization** (owner-salary add-backs, one-offs, market-rate replacement) | BizEquity, BVO, ValuAdder, Arvento, formal (common) | THE defining SMB adjustment; un-normalized EBIT biases every income method | H | med |
| 9 | **XLSX export** of tables/model | Equidam, Eqvista, PitchBook (common) | Half our audience lives in Excel; editable assumptions are worthless if un-editable. Data already structured | H | med |
| 10 | **Method-dispersion range / football-field chart** (per-method min–max side by side) + method-weight donut | Equidam, Eqvista, sell-side (common) | Shows how much the answer depends on method choice; pure viz of data we already compute | H | med |
| 11 | **Qualitative SME value-factor intake** — customer concentration, owner/key-person dependency, contract quality | BizEquity, BVO, ValuAdder, Rotio, Arvento, sell-side (common) | Top value-killers, invisible in financials; needs a short intake form | H | med |
| 12 | **Populated industry/peer benchmarking** — sector medians + percentile ranking (our peers=[] slot) | Equidam, BizEquity, BVO, Asiakastieto, Arvento, PitchBook, sell-side, formal (common) | The #1 owner question ("good vs my industry?"); flips a stated weakness into a strength. Machinery built, feed empty | H | big |
| 13 | **Accounting-software / Y-tunnus auto-fetch ingestion** (Procountor/Netvisor/Taloustutka/YTJ) | BVO, Arvento | Biggest adoption barrier; gates whether owners reach the report at all | H | big |
| 14 | **IVS standards-alignment statement** | Equidam (IPEV), ValuAdder, BVO, formal (common) | Cheap credibility for banks/buyers/advisors; one referenced paragraph in §16 | M | quick |
| 15 | **Per-KPI plain-language explainer + methodology glossary + worked example** | BizEquity, Asiakastieto, BVO, Rotio (common) | Most owners aren't finance people; raises trust, cuts support questions | M | quick |
| 16 | **Full assumptions/parameters appendix** (input → value → source) | Equidam, Asiakastieto | One-glance auditability turns a black box into a defensible model | M | quick |
| 17 | **Forward projection charts** (revenue/EBIT/FCF trend) | Equidam, Eqvista | Owners grasp a rising curve instantly; reuse existing bar_line block | M | quick |
| 18 | **Markdown / AI-queryable export** | Equidam | Advisors want to "chat with" the report; trivial adapter over existing JSON | M | quick |
| 19 | **Capitalized-earnings single-period cross-check** | ValuAdder, formal | Cheap, intuitive DCF sanity line ("this year's earnings ÷ cap rate") | M | quick |
| 20 | **SWOT quadrant** | Arvento | The format owners recognize as "professional"; we have the substance already | M | quick |
| 21 | **Catalysts with timing** / sequenced value-driver roadmap | sell-side | Turns §14's flat lever list into an actionable timeline | M | quick |
| 22 | **Goal/purpose framing** ("why valuation matters" + succession/sale/growth mode) | Arvento, BizEquity | Owners frame by goal, not method; lets us foreground the right number | M | med |
| 23 | **Multiple value conclusions per use case** (osakekauppa vs liiketoimintakauppa) | BizEquity | Different tax/structuring numbers for the same company; directly actionable | M | med |
| 24 | **WACC build-up table** (decompose supplied WACC / documented convention) | ValuAdder, Asiakastieto, formal, Arvento | Discount rate drives DCF most; auditable build-up. *Tension with no-invent-WACC* | M | med |
| 25 | **Non-operating / excess-asset separation** (esp. excess cash) | formal | Cash-rich holding cos: folding surplus into DCF understates equity | M | med |
| 26 | **DLOM marketability discount** for unlisted shares | formal | Finnish SMB shares illiquid; essential the moment peer multiples turn on | M | med |
| 27 | **Always-on deterministic sensitivity heatmaps** | Asiakastieto | Ours only renders if the engine supplies it; generate from our own model | M | med |
| 28 | **Credit / financial-health composite score** | Asiakastieto | Single "health" read for lenders/buyers; buildable from ratios we compute | M | med |
| 29 | **Scenario waterfall / bridge chart** | sell-side | Decomposes base→scenario driver by driver; new chart type, data exists | M | med |
| 30 | **Value-map positioning chart** (size × growth) | BizEquity | One-glance "why you're worth this"; inputs already computed | M | med |
| 31 | **Historical value-over-time chart** | Asiakastieto | Shows if the business is getting more/less valuable; re-run on prior years | M | med |
| 32 | **Exit-multiple terminal value** (2nd DCF TV) | Equidam | Sanity-checks the perpetuity assumption; *rides on gap #12 (peer data)* | M | med |
| 33 | **Advisor-facing risk deliverable** (separate accountant's report) | BVO | Advisors are a core channel; mostly reframing existing content for /asiantuntija | M | med |
| 34 | **Debt-service / price-justification test** | ValuAdder | Reframes "worth" into "financeable"; pairs with our forecasts + net debt | M | med |
| 35 | **Live interactive sliders / value tracking over time** | Equidam, Eqvista, BizEquity, Arvento, Rotio (common) | Engagement + retention; *product surface, cuts against single-deliverable positioning + per-run cost* | M | big |
| 36 | **Multilingual (Swedish / English)** | Asiakastieto | Swedish owners + foreign buyers in M&A/diligence | M | big |
| 37 | Transaction/deal-multiples database | BizEquity, BVO, ValuAdder, sell-side, PitchBook (common) | Most persuasive private-co check; *Finnish deal data scarce/expensive* | L | big |

---

## 4. Prioritized recommendations

### (a) Quick wins — do these first (high relevance, quick/medium effort)

1. **Implied-multiple line** — add "this value implies ≈ X× EV/EBITDA, Y× EV/liikevaihto" to **§8** (and cover). Descriptive ratio of our own output; does not touch the peers=[] stance.
2. **Verottajan malli** — compute statutory fair value = avg(substanssiarvo, tuottoarvo @15% required return) as a labelled reference number in **§10/§8**, flagged for succession use. Highest value-per-effort item on the whole list.
3. **Expanded ratios + common-size columns** — add DSO/DPO/CCC, quick ratio, ROA and ROIC/ROCE-vs-WACC to **§5**, and % columns to the **§15** statement tables. All permitted derived ratios.
4. **Table of contents** — render a clickable section index after the cover.
5. **Substanssiarvo floor + goodwill/net-tangible split** — always show net-asset value as a reference line and split equity into tangible backing vs goodwill in **§8**.
6. **Standard-of-value + premise + as-of date + scope/reliance statement** — one framing block at the top of **§1** and in **§16**.
7. **IVS alignment paragraph** — short "methodology follows IVS income/market/asset approaches" note in **§16**.
8. **Per-KPI explainer + method glossary + worked example** — inline "mitä tämä tarkoittaa" per key ratio in **§5**, glossary + one worked calculation in **§16**.
9. **Assumptions appendix** — one flat input→value→source table (new appendix / end of **§15**).
10. **Forward projection chart** — reuse the bar_line block on forecast data in **§6**.
11. **Method-value football-field + weight donut** — per-method min–max bars and a DCF%/EVA% donut in **§8**.
12. **SWOT + catalysts-with-timing** — a SWOT quadrant folded into **§12/13** and a time-sequenced version of the **§14** levers.
13. **Owner-earnings normalization** — upgrade the "Normalisointihuomiot" note into a structured add-back table in **§5** (owner salary vs market rate, one-offs). *Needs one intake field for owner salary → medium; do it early — single biggest credibility gap for owner-managed firms.*
14. **XLSX + Markdown exports** — openpyxl transform of financial/forecast/valuation tables + a clean Markdown render, both off the machine_readable block. Pipeline output, not report content.

### (b) Bigger bets — high relevance, big effort

1. **Industry benchmarking (fill the peers slot).** Feed sourced Finnish sector medians/percentiles (PRH/Asiakastieto/Tilastokeskus/Finder) into the existing peers machinery so §5, §7, §8, §15 show "your EBIT% is at the Nth percentile of Finnish [toimiala]." #1 gap; plumbing already exists. *Must be sourced, never modelled.*
2. **Qualitative value-factor intake.** Short form: customer concentration, owner/key-person dependency, contract quality → §12/13 + pessimistic scenario as *transparent, disclosed adjustment bands*.
3. **Auto-fetch ingestion by Y-tunnus** (Taloustutka/YTJ + Procountor/Netvisor connector) to kill the biggest adoption barrier.

### (c) Consider later / market-dependent

- **WACC build-up table** — only as a *documented, editable convention* (each component owner-adjustable, like our probabilities); deliberate decision against current no-invent-WACC rule. Unlocks DCF when no WACC supplied.
- **Multiple value conclusions** (osake- vs liiketoimintakauppa), **DLOM**, **excess-asset separation**, **capitalized-earnings cross-check** — solid medium adds; sequence after quick wins.
- **Exit-multiple terminal value** — rides on benchmarking (b1); do once peers live.
- **Live sliders / value-tracking / multilingual / advisor risk-report / transaction-multiples DB / human-review tier** — real but each a business-model or data-sourcing decision, not a report-quality fix. Interactive/tracking ones fight single-deliverable positioning + per-run cost.

---

## 5. Do NOT copy

| Feature | Source(s) | Why not |
|---|---|---|
| Berkus / Scorecard / Risk-Factor / VC pre-revenue methods | Equidam, Eqvista | Require invented industry pre-money averages & subjective scores — conflict with numbers-only |
| Buy/Hold/Sell rating + 12-month price target | sell-side | No tradeable price for a private co; no-recommendation is a chosen compliance stance |
| Cap table / per-share pricing / ownership-% / 409A | Equidam, Eqvista, PitchBook | US-startup/VC convention; Finnish ownership sits in the trade register |
| Round info (valuation→equity%), use-of-funds | Equidam | Purely fundraising outputs; our audience is succession/sale/reporting |
| Tear-sheet market data, consensus analyst estimates | sell-side, PitchBook | Presuppose a listed security and sell-side coverage |
| Reg AC / MiFID II / conflict disclosures | sell-side | Investment-advice regulation; a private-co valuation isn't investment advice |
| Daily ML mark-to-market | PitchBook | Needs a huge proprietary dataset; collides with traceable-number discipline |
| ESOP binomial / APV / NPV-IRR project screening | ValuAdder | US cap-table / corporate-project-finance tools |
| Signed credentialed appraiser certification | formal standards | Structurally impossible for an automated product — hold as a positioning line, offer only as optional paid human-review add-on |
| Raw listed P/E, P/B applied to unlisted SMBs | Asiakastieto | Our skepticism is correct — overstates value; only a byproduct if benchmarking built, then with DLOM/size discount |
| Qualitative company radar (Mgmt/Product/Market x/10) | Eqvista | Needs subjective scores we don't collect — reframe onto data-quality/confidence if at all |

---

## 6. What we already do better

- **Probabilistic expected value** — three scenarios, editable probabilities summing to 100%, E[V] = Σ(prob×value). Nobody else weights by probability; competitors show mechanical method-variance (Equidam) or a flat ±15% band (Rotio) at best.
- **Limited-liability owner floor at 0** with explicit separation of computed base case, floor, and unquantified option/strategic value — jurisdiction-correct for Finnish Oy owners; lets us value distressed/negative-equity companies honestly. No competitor models this.
- **Market-signal reverse-calculation** — backing out the implied perpetual FCF (EV×WACC) a real offer/round/M&A price requires, and separating strategic/buyer-specific value from the base case (§4). Genuinely novel.
- **Data-quality section that flags internally inconsistent input figures** and lowers confidence *before* valuing. Every competitor trusts hand-keyed or filed input as clean.
- **Transparent method selection** — hard reject rules → 0–100 scoring → correlation penalty → normalized weights, reasoning shown. Sell-side and BizEquity hide this; Asiakastieto runs one fixed engine.
- **Anti-hallucination discipline enforced in prompt + engine** — never invents market size, WACC, comps or multiples; every figure traces to input or marked derivation. Competitors silently borrow Damodaran/industry data (Arvento) or assert TAM/target multiples on thin sourcing (sell-side).
- **Rules-driven confidence rating** with the deciding rule named; deep DCF transparency; prescriptive "actions to increase value"; machine-readable data block. **Finnish-native throughout.**

---

## 7. Biggest single opportunity

**Turn on industry benchmarking by feeding sourced Finnish sector medians into the already-built peers slot.** Most-cited gap in the entire research — every serious competitor has it and it is Asiakastieto's whole moat — yet for us it is a *data-feed* problem, not a build problem: the EV/Sales & EV/EBITDA median machinery, the §15 Toimialavertailu, the method scoring, and the listed-vs-unlisted discount logic already exist and ship dark behind `peers=[]`, printing a confidence-lowering "no market cross-check available" warning in every real report today. Populating it with coarse Finnish toimiala medians (PRH/Asiakastieto financials, Tilastokeskus/Finder) flips our single most visible stated weakness into a strength, answers the first question every owner and advisor asks, and cascades into three further features almost for free — percentile ranking (§5), a market leg on the football-field chart (§8), and the exit-multiple terminal-value cross-check (§9). Sourced-not-modelled, it stays fully inside our no-invention discipline. If only one big bet ships this year, ship this — with owner-earnings normalization as the cheaper close runner-up, since together they remove the two credibility gaps an advisor notices within thirty seconds.

---

*Research-confidence flags: (1) Pricing inferred except BVO (AUD 1,399–4,499), ValuAdder ($375–675), Arvento (19€/mo), PitchBook (~$20–40k), formal appraisal (~$3k–50k). (2) Competitor feature claims and "we do better" points are the researchers' characterizations, not independently verified. (3) "Closest competitor = Asiakastieto" per brief; weight Arvento equally as closest AI-native threat. (4) Effort/relevance ratings are directionally reliable but not costed against our actual codebase.*
