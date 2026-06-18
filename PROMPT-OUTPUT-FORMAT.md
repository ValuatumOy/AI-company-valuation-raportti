# Promptin output ↔ designpohja — integraatiosopimus

**Sopimus = tuotantopromptin (v3) OUTPUT-skeema.** Renderöijä sopeutuu siihen,
ei toisin päin. Jos promptin skeema muuttuu, päivitä `scripts/lib/adapt.js`.

## Työnkulku

1. Aja prompti → tallenna mallin JSON nimellä `data/report.json`
   (```json-aidat ja ympäröivä teksti siedetään).
2. `node scripts/generate.js` → `outputs/raportti.html`.

`generate.js` tunnistaa promptin skeeman (`report_type` / `meta.company_name` /
`paragraph`-lohkot) ja muuntaa sen `adapt.js`:llä sisäiseen lohkomalliin, jonka
`blocks.js` + `charts.js` renderöivät designiin.

## Mitä adapteri mappaa (promptin skeema → design)

| Promptin kohta | Design |
|---|---|
| `meta.company_name / y_tunnus / report_date / industry` | kansi + sivun ylätunniste |
| `cover.headline_value` (tai `expected_value.value`) | kannen pääluku |
| `cover.headline_label` | kannen alaotsikko |
| `cover.secondary_lines` | kannen lisärivit |
| `confidence.level + deciding_rule` | sisällys-sivun lukuohje-callout |
| `sections[]` (id, title, blocks) | osiot + auto-sisällysluettelo + sivunumerot |
| lohko `heading` | väliotsikko |
| lohko `paragraph` | kappale (`**lihava**`, `*kursiivi*`) |
| lohko `callout` (`info`/`warning`/`key`) | callout (harmaa / punainen / vihreä) |
| lohko `metric_cards` | mittarikortit |
| lohko `key_value` | avain–arvo-rivit (`source` näkyy arvon perässä) |
| lohko `table` | taulukko; numerot → suomalainen muoto (1 000, −5,2) |
| lohko `chart` `bar_line` | combo (pylväät + viiva, 2 akselia) |
| lohko `chart` `bar_grouped` / `bar` | pystypylväät |
| lohko `chart` `heatmap_or_matrix` | taulukko (pohja ei renderöi heatmapia) |
| mikä tahansa `status:"not_available"` | otsikko + `reason`-teksti, ei tyhjää |
| `machine_readable` | sivuutetaan renderöinnissä (validaattorin käyttöön) |

Snapshot-sivua (vanha sivu 2) ei muodosteta — osio 1 TIIVISTELMÄ
(`metric_cards` + yhteenveto) hoitaa avainlukunäkymän.

## Kaksi suositusta promptin tekijälle

1. **Lukumuoto:** anna `chart`-lohkojen `values` raakana numerona (esim. `1300`,
   `-5.2`) — adapteri muotoilee akselit. `table`-lohkoissa numerot muotoillaan
   automaattisesti suomalaisiksi; jos haluat itse hallita esitysmuodon (esim.
   "n. 218"), anna solu merkkijonona. Vuosiluvut (1900–2100) jätetään
   erottelematta automaattisesti.
2. **Vaakapylväät:** jos haluat menetelmävertailun (osio 8) vaakapylväinä kuten
   alkuperäisessä designissa, voidaan lisätä promptiin `chart_type:"hbars"` ja
   adapteriin tuki. Nyt `bar` → pystypylväät.

## Jos jokin ei mäppää

Lähetä yksi oikea mallioutput → lisään puuttuvan mapin `adapt.js`:ään.
Ei templaten eikä promptin muutosta.
