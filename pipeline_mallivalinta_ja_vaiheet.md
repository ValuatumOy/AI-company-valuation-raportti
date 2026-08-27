/# Pipeline-arkkitehtuuri ja mallivalinta — AI-arvonmääritysraportti

Tarkoitus: ratkaista output-katkeaminen pilkkomalla raportti vaiheisiin, reitittää jokainen vaihe oikealle mallille OpenRouterissa, ja määritellä mitä kukin vaihe tekee. Hinnat ovat OpenRouter-listahintoja kesäkuussa 2026 (per 1M tokenia, input/output) ja muuttuvat — varmista openrouter.ai/models ennen lukitsemista.

---

## Ohjaava periaate

Näiden raporttien laatu ei tule mallin "älystä" vaan kahdesta asiasta: (1) ettei malli keksi tai laske numeroita väärin, ja (2) että suomi on julkaisukelpoista. Opus-vs-GLM-vertailu osoitti, että parempikin malli teki saman numeerisen ydinvirheen (käytti virheellistä DCF-dataa) — eli **koodivalidaattori on tärkeämpi kuin mallivalinta numero-osioissa.** Tästä seuraa reitityssääntö:

- **Numeroita käsittelevät vaiheet** → halvempi, kuuliainen malli + koodivalidaattori. Kallis malli ei korjaa numerovirhettä, jonka data sisältää.
- **Ei-ilmeistä analyysiä ja suomea vaativat vaiheet** (tiivistelmä, optimistinen skenaario, ennusteen uskottavuus) → vahvempi malli. Tässä laatuero näkyy.
- **Hakua vaativa vaihe** (enrichment) → grounded-malli, jolla on natiivi web-haku. Gemini sopii tähän koska se on grounded; sen ungrounded-hallusinointiriski ei realisoidu kun se hakee.

---

## Mallit ja hinnat (OpenRouter, kesäkuu 2026)

| Malli | Input $/1M | Output $/1M | Konteksti | Vahvuus | Heikkous |
| --- | --- | --- | --- | --- | --- |
| DeepSeek V4 Flash | 0,09 | 0,18 | 1M | Halvin vakavasti otettava; rakenteinen output, sääntöjen soveltaminen | Ei grounded-hakua; suomi testaamatta |
| DeepSeek V4 Pro | ~0,44–1,74 | ~0,87–3,48 | 1M | Lähellä frontieria (SWE 80,6 %), 1M konteksti | Suomi testaamatta; reasoning_content-vuoto JSON-pipelinessä |
| Kimi K2.6 | 0,68 | 3,41 | 262K | Vahva agentic + monivaihe-tool, multimodaali | Suomi testaamatta; output kallis |
| Gemini 2.5 Flash | 0,30 | 2,50 | 1M | Natiivi grounded web-haku + tool calling | Ungrounded-hallusinointi |
| Gemini 3.5 Flash | ~1,50 | ~9,00 | 1M | Vahvempi tool-ketju (MCP Atlas 83,6 %) | Kalliimpi output |
| Gemini 3.1 Pro | ~2,00 | ~12,00 | 1M | Vahvin haku + synteesi, luotettava 1M | Kalliimpi |
| Claude Sonnet 4.6 | ~3,00 | ~15,00 | 200K | Paras suomi + analyysi (vertailun ankkuri) | Kallein tässä listassa |

**Ohjaava jako:** kiinalaiset avoimet mallit (DeepSeek/Kimi/Qwen) ovat huippuja koodissa, matematiikassa ja rakenteisessa outputissa — mutta benchmarkit eivät mittaa tuotantolaatuista **suomea**, joka on pieni kieli niiden treenidatassa. GLM 5.2 -testi vahvisti tämän: benchmark-kärkeä, mutta suomessa anglismeja ("kate-erit", "ostotarjus"). Siksi: kiinalaiset mallit numero- ja hakurakennevaiheisiin (halpa + validaattori hoitaa tarkkuuden), mutta asiakkaalle näkyvä suomi (analyysi + tiivistelmä) testataan rinnakkain ennen lukitsemista.

---

## Pipeline: 6 vaihetta

Vaiheet ajetaan järjestyksessä. Jokainen saa edellisten välitulokset. Tiivistelmä kirjoitetaan VIIMEISENÄ hyväksyttyjen osioiden avainväitteistä — ei ensin.

### Vaihe 0 — FAKTAT-lohkon kokoaminen (ei LLM)
**Malli:** ei mitään. Tämä on CTO:n MCP-skripti + normalisointi.
**Tekee:** hakee MCP:stä actuals/forecast/valuation_engine/key_ratios/credit_risk, normalisoi yksiköt tEUR:iin (EVA-skaalausansa), lisää `flags`-merkinnät tunnetuille ristiriidoille (ROE 0, ROI-etumerkki), kokoaa yhdeksi `[input_data]`-JSONiksi.
**Output:** validoitu `[input_data]`.

### Vaihe 1 — Enrichment (web-haku)
**Malli:** Gemini 2.5 Flash (grounded haku, halpa, 1M konteksti) — tai Gemini 3.5 Flash jos tool calling -ketju on monimutkainen.
**Miksi:** tämä on ainoa vaihe jossa haetaan netistä (yritysprofiili, VC-rahoitus, yrityskaupat, johto). Gemini on grounded tässä, joten sen hallusinointiriski ei realisoidu, ja web-haku + Y-tunnus-varmistus on sen vahvuus. Opus-OGOship osoitti että juuri tämä (6,3 M€ VC-rahoituksen löytäminen) teki raportista hyvän.
**Tekee:** Y-tunnus-identiteetin varmistus, yrityksen verkkosivut, 2–3 markkinasignaalihakua, palauttaa strukturoidun lähderekisterin (väite + lähde + pvm + luotettavuusluokka).
**Output:** `enrichment`-lohko (liiketoimintaprofiili + markkinasignaalit lähteineen).
**Kriittinen guardrail:** verkkodata ei koskaan muuta lukuja, vain tekstiä ja signaaleja. Jos Y-tunnusta ei voi varmistaa → degradaatio tilinpäätöspohjaiseen profiiliin.

### Vaihe 2 — Pisteytys ja skenaariorunko (ei vapaata proosaa)
**Malli:** DeepSeek V4 Flash (halvin, rakenteinen output, sääntöjen soveltaminen).
**Miksi:** tämä on deterministinen sääntöjen soveltaminen, ei luova tehtävä. Output on pieni JSON, ei suomenkielistä proosaa, joten suomen heikkous ei haittaa. Halvin malli riittää.
**Tekee:** soveltaa menetelmien hylkäyssäännöt + pisteytyksen (0–100, korrelaatiosakko) + painot, valitsee todennäköisyysprofiilin ja skenaariotodennäköisyydet, laskee odotusarvon. Ei kirjoita analyysitekstiä.
**Output:** `scoring`-lohko (menetelmäpisteet, painot, painotettu arvo, skenaariot, todennäköisyydet, odotusarvo).
**Validaattori ajetaan heti perään:** tarkistaa painojen normalisoinnin (Σ = 100 %), odotusarvon laskennan (Σ p×arvo), floorin (jokainen skenaario ≥ 0).

### Vaihe 3 — Numero-osiot (historia, ennuste, DCF, EVA, arvonmääritys)
**Malli:** DeepSeek V4 Flash + pakollinen koodivalidaattori. (Tarkista että reasoning_content ei vuoda JSON-outputtiin; jos vuotaa, käytä non-reasoning-tilaa tai V4 Flashin ei-päättelevää reittiä.)
**Miksi:** nämä osiot toistavat ja tulkitsevat FAKTAT-lohkon lukuja. Ei tarvita kallista mallia — tarvitaan halpa malli joka ei muuta lukuja ja validaattori joka pysäyttää jos se muuttaa. Tämä on se vaihe jossa sekä GLM että Opus tekivät virheitä; malli ei korjaa sitä, koodi korjaa. Suomenkielinen sisältö on tässä mekaanista (taulukot, lyhyet tulkintalauseet); jos V4 Flashin suomi osoittautuu liian kankeaksi tässäkin, nosta numero-osioiden tekstilohkot vaiheen 4 malliin ja jätä V4 Flashille vain taulukko/luku-lohkot.
**Tekee:** osiot 5 (historia), 6 (ennuste + uskottavuusarvio), 8 (arvonmääritys), 9 (DCF), 10 (EVA). Jokainen `blocks`-rakenteena.
**Output:** `sections[5,6,8,9,10]`.
**Validaattori (pakollinen, koodi):**
- jokainen tekstin luku löytyy `[input_data]`:sta tai on sallittu yksinkertainen laskelma
- |diskontattu FCFF| ≤ |nimellinen FCFF| jokaisella positiivisella WACCilla (tämä olisi napannut sekä GLM:n että Opuksen anomalian)
- DCF-silta täsmää: Σ diskontattu FCFF + terminaali − nettovelka = equity-arvo
- sama termi ("base case") = sama luku kaikissa osioissa
- breakeven-laskelma täsmää (kiinteät / bruttokate-%)
Jos validaattori failaa → osio ajetaan uudelleen virheviestillä, ei päästetä läpi.

### Vaihe 4 — Analyysi-osiot (profiili, skenaariot, ajurit, riskit, toimenpiteet)
**Malli:** TESTAA RINNAKKAIN ennen lukitsemista — DeepSeek V4 Pro, Kimi K2.6 ja Claude Sonnet 4.6 samalla yhtiöllä, ja lue suomi rinnakkain. Lukitse halvin, joka tuottaa puhdasta asiantuntijasuomea ilman anglismeja.
**Miksi:** tässä syntyy ei-ilmeinen analyysi: optimistisen skenaarion markkinalogiikka, riskipolku, profiilin yhdistäminen tilinpäätökseen. Opus-OGOship osoitti että tämä on se osa joka erottaa hyvän raportin keskinkertaisesta. GLM:n kielivirheet ("kate-erit", "ostotarjus") tulivat osin tästä — heikko suomi ei riitä asiakkaalle näkyvään tekstiin. Benchmark ei kerro suomen laatua, joten päätös tehdään vain lukemalla output suomeksi. Jos V4 Pro tuottaa puhdasta suomea, käytä sitä ja säästä; jos siinä on yksikin anglismi, Sonnet voittaa hinnastaan huolimatta.
**Suomi-testiprotokolla:** aja sama yritys kaikilla kolmella, poimi osiot 3, 11 ja 12, ja tarkista: (a) anglismit ja lainasanat, (b) taivutusvirheet, (c) termien johdonmukaisuus (katteet vs kate-erit), (d) sävy (asiantuntijamainen vs konekäännösmäinen). Yksikin a–c-luokan virhe = hylätään asiakaskäyttöön.
**Tekee:** osiot 3 (profiili, käyttää enrichment-dataa), 4 (markkinasignaalit + käänteislaskelma), 11 (skenaariot + optimistisen oletukset), 12 (ajurit), 13 (riskit), 14 (toimenpiteet).
**Output:** `sections[3,4,11,12,13,14]`.
**Saa syötteenä:** FAKTAT + enrichment + scoring (vaiheet 0–2), jotta luvut ovat lukittuja eikä malli laske niitä uudelleen.

### Vaihe 5 — Tiivistelmä + kokoaja + loppuvalidointi
**Malli:** sama malli kuin vaihe 4 lukitaan (vaiheen 4 suomi-testin voittaja) tiivistelmälle + koodi (kokoaja).
**Miksi:** tiivistelmä on asiakkaan ensimmäisenä lukema osa ja vaatii saman suomen tason kuin analyysi-osiot, joten se ajetaan samalla mallilla. Se kirjoitetaan viimeisenä hyväksyttyjen osioiden avainväitteistä — tämä estää UPM-demojen sisäiset ristiriidat. Kokoaja on koodia, ei LLM:ää.
**Tekee:** kirjoittaa osion 1 (tiivistelmä + avainluvut + havainnot) jo hyväksytyistä osioista, sitten koodi liittää kaikki `sections` yhdeksi JSONiksi + `machine_readable`-lohko + `meta`/`cover`/`confidence`.
**Loppuvalidaattori:** ajaa kova sääntö 21:n (JSON-luvut = tekstiluvut), tarkistaa että `machine_readable` täsmää osioihin, varmistaa että kansisivun headline-luku = odotusarvo osiosta 11.

---

## Reitityksen yhteenveto

| Vaihe | Malli | Syy | Output-koko |
| --- | --- | --- | --- |
| 0 FAKTAT | (koodi) | deterministinen | — |
| 1 Enrichment | Gemini 2.5/3.5 Flash tai 3.1 Pro | grounded web-haku | pieni |
| 2 Pisteytys | DeepSeek V4 Flash | sääntöjen soveltaminen, ei proosaa | pieni (JSON) |
| 3 Numero-osiot | DeepSeek V4 Flash + validaattori | numerokuri tulee koodista, ei mallista | keskisuuri |
| 4 Analyysi-osiot | testaa V4 Pro / Kimi vs Sonnet → halvin joka tuottaa puhdasta suomea | ei-ilmeinen analyysi + asiakkaan näkemä suomi | keskisuuri |
| 5 Tiivistelmä + kokoaja | vaiheen 4 voittaja + koodi | viimeisenä, ristiriitojen esto | pieni |

**Kustannuslogiikka:** halvat avoimet mallit (DeepSeek V4 Flash $0,09/$0,18, Gemini Flash) vaiheissa 1–3, koska ne ovat joko hakua tai rakenteista numerotyötä, jossa validaattori hoitaa tarkkuuden eikä suomen laadulla ole merkitystä. Kallein malli vain vaiheissa 4–5, ja sielläkin vain jos halvempi avoin malli ei läpäise suomi-testiä. Tämä on murto-osa siitä mitä koko raportin ajaminen Opuksella maksaisi, ja luotettavampi koska validaattori on silmukassa.

---

## Miksi pilkkominen ratkaisee katkeamisen

Output-katkeaminen ~10 sivun jälkeen oli `max_tokens`-katto: koko raportti yhtenä JSONina on 8 000–12 000 output-tokenia, ja JSON syö tokeneita enemmän kuin markdown. Pilkottuna jokainen vaihe tuottaa korkeintaan muutaman tuhannen tokenin, ei osu kattoon. Lisäbonus: jos yksi vaihe failaa, ajetaan vain se uudelleen, ei koko raporttia — ja rikkinäinen JSON ei enää kaada koko tulosta.

---

## Avoimet päätökset ennen lukitsemista

1. **DeepSeek V4 Flash vaiheissa 2–3:** testaa että se palauttaa puhdasta JSONia ilman reasoning_content-vuotoa, ja että sen mekaaninen suomi (taulukot, lyhyet tulkintalauseet) on riittävää. Jos numero-osioiden tekstilohkot ovat liian kankeita, siirrä ne vaiheen 4 malliin ja jätä V4 Flashille vain luku- ja taulukkolohkot.
2. **Gemini 2.5 vs 3.5 vs 3.1 Pro enrichmentissä:** 2.5 Flash riittää yksinkertaiseen hakuun; 3.5 jos MCP-tool-ketju on monivaiheinen; 3.1 Pro jos haluat vahvimman synteesin. Älä käytä kiinalaisia malleja tähän — niiden grounded-haku ei ole yhtä saumaton.
3. **Validaattori on rakennettava ensin.** Ilman sitä koko V4 Flash -säästö menetetään, koska numerovirheet pääsevät läpi. Tämä on kriittisin yksittäinen rakennuspala — tärkeämpi kuin mikään promptin tai mallin valinta.
4. **Vaiheen 4 suomi-testi ennen lukitsemista.** Aja sama yritys V4 Pro:lla, Kimillä ja Sonnetilla, lue suomi rinnakkain testiprotokollan mukaan. Tämä on ainoa tapa päättää — benchmark ei kerro suomen laatua.
5. **Prompt-välimuisti (caching):** FAKTAT-lohko + iso prompt toistuvat vaiheissa 3–4. DeepSeekin cache-read (~$0,0145/1M) ja Anthropicin prompt caching leikkaavat input-kustannusta merkittävästi kun sama konteksti syötetään monelle vaiheelle.
