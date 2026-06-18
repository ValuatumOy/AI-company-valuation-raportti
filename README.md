# Valuatum AI-arvonmääritysraportti — JSON-pohjainen generaattori

Pudota JSON, saat valmiin raportin. Ei tekoälyä, ei verkkoa generointivaiheessa —
sama JSON tuottaa aina saman taiton samaan designiin.

## Työnkulku (tämä toistat joka raportille)

1. Korvaa `data/report.json` uudella sisällöllä (sama rakenne, eri luvut/tekstit).
2. Aja:

   ```
   node scripts/generate.js
   ```

   Tuloste: `outputs/raportti.html` — yksittäinen, omavarainen HTML (fontit
   upotettu). Avautuu suoraan selaimessa.
3. PDF: avaa selaimessa → tulosta → "Tallenna PDF". Tai komentoriviltä Chromella:

   ```
   "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome" \
     --headless --no-pdf-header-footer --print-to-pdf=outputs/raportti.pdf \
     --virtual-time-budget=6000 "file://$PWD/outputs/raportti.html"
   ```

Halutessasi oma polku: `node scripts/generate.js oma.json ulos.html`.

## Miten se toimii

- `template/report.template.html` — **designrunko**: alkuperäinen taitto (CSS,
  upotetut fontit, sivutuslogiikka) jossa dynaaminen sisältö korvattu
  `{{PLACEHOLDER}}`-merkeillä. Generoitu kerran `index.html`:stä.
- `scripts/generate.js` — lukee JSONin, rakentaa HTML-lohkot ja sijoittaa ne runkoon.
- `scripts/lib/blocks.js` — JSON-lohko → HTML.
- `scripts/lib/charts.js` — puhtaat SVG-kuvaajat (skaalautuvat, tulostuvat terävinä).
- Selaimessa ajettava sivutusskripti virtaa osiot A4-sivuiksi ja numeroi
  sisällysluettelon + sivut automaattisesti.

### Jos itse designia (CSS/fontit) muutetaan

Harvinaista. Muokkaa designia `index.html`:ssä (alkuperäinen bundle) ja aja:

```
node scripts/build_template.js
```

Tämä rakentaa `template/report.template.html`:n uudelleen. Sisältö tulee aina
JSONista, ei templatesta.

## JSON-rakenne

Katso täysi esimerkki: `data/report.json` (sama sisältö kuin nykyinen raportti).

```jsonc
{
  "meta": {
    "brandName": "Valuatum",          // logon/otsikon nimi
    "company": "Valuatum Oy",
    "businessId": "1612398-8",
    "date": "12.6.2026",
    "reportTitle": "AI-Arvonmääritysraportti",
    "headerRight": "Valuatum Oy · 1612398-8 · 12.6.2026"  // sivun ylätunniste
  },
  "cover":    { /* kansi: tagline, title, company, metaLines[], headline{big,unit,caption,size} */ },
  "snapshot": { /* sivu 2: title, subtitle, blocks[] */ },
  "toc":      { /* sisällys-sivun callout: noteTitle, note  (rivit luodaan sektioista) */ },
  "sections": [ { "id", "number", "title", "subtitle?", "blocks": [ ... ] } ]
}
```

Sisällysluettelo ja sivunumerot muodostuvat automaattisesti `sections`-listasta.
Osioiden järjestys, otsikot ja sisältö = mitä laitat `sections`-listaan. Lisää,
poista tai järjestele osioita vapaasti.

### Lohkotyypit (`blocks`)

| `type`    | Kentät | Tuottaa |
|-----------|--------|---------|
| `h`       | `text` | pieni osioväliotsikko (KUVAAJA/AVAINLUVUT-tyyli) |
| `h3`      | `text` | suurempi väliotsikko |
| `p`       | `text`, `class?` | kappale (tukee `**lihava**`, `*kursiivi*`) |
| `list`    | `ordered`, `items[]`, `splitItems?`, `start?` | numeroitu/luettelolista |
| `table`   | `headers[]`, `rows[][]`, `note?` | taulukko (+ selitysrivi) |
| `callout` | `variant` (`kill`/`reality`/`neutral`), `title`, `paragraphs[]` tai `items[]`+`ordered?` | korostuslaatikko |
| `metrics` | `columns`, `style?`, `cards[]` (`value`,`label`,`valueSize?`,`unit?`,`accent?`) | mittarikortit |
| `kv`      | `rows[]` (`k`,`v`,`bold?`) | avain–arvo-rivit |
| `chart`   | `kind` + data (alla) | SVG-kuvaaja |
| `raw`     | `html` | oma HTML sellaisenaan |

Rivin tekstissä `**lihava**` ja `*kursiivi*` toimivat kaikkialla.

### Kuvaajat (`chart`)

Yhteiset: `title?`, `caption?`, `legend?` (`[{type:"bar"|"line", label, color}]`).

| `kind`  | Data | Käyttö |
|---------|------|--------|
| `bars`  | `labels[]`, `series:[{name,values[],color?}]` | pystypylväät |
| `line`  | `labels[]`, `series:[{name,values[],color?}]` | viivakuvaaja |
| `combo` | `labels[]`, `bars:{name,values[]}`, `line:{name,values[],percent?}` | pylväät + viiva (kaksi akselia) |
| `hbars` | `items:[{label,value?,status?,muted?}]` | vaakapylväät (esim. menetelmien arvot) |

`hbars`: jätä `value` pois ja anna `status` (esim. "hylätty") → rivi ilman pylvästä.
`muted:true` → himmennetty pylväs (viitearvo).

Luvut numeroina (`578.0`), eivät merkkijonoina, jotta akselit skaalautuvat oikein.
Negatiiviset arvot tuetaan (akseli ulottuu nollan alle automaattisesti).

## Tiedostot

- `index.html` — alkuperäinen bundle, designin lähde (build_template.js lukee tämän).
- `template/report.template.html` — generoitu designrunko (älä muokkaa käsin).
- `data/report.json` — **sisältö, jonka korvaat** joka raportilla.
- `outputs/raportti.html` — generoitu raportti.
- `scripts/` — generate.js, build_template.js, lib/.
