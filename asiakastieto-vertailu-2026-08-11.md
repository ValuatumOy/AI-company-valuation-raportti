# Rakennevertailu: Asiakastiedon Arvoraportti vs. meidän AI-arvonmääritysraportti

Vertailukohta: `https://www.asiakastieto.fi/resources/raportit-api/malliraportti/suomen-asiakastieto-oy-arvoraportti-malli.pdf`
(41 sivua, laadittu 9.7.2020 — kuusi vuotta vanha massatuotteen mallikappale).
Meidän puoli: Heeros Oyj -ajo `dea1c5f9…`, 20 sivua, renderöity 2026-08-11
rakenneuudistuksen jälkeen.

## Asiakastiedon runko

| Sivu | Sisältö |
|---|---|
| 1 | Kansi: logo, kuvituskuva (timantti), yhtiön nimi + Y-tunnus, iso ARVORAPORTTI-sana |
| 2 | Asiakastieto yrityksenä + Avainlippu (markkinointisivu) |
| 3 | Sisällysluettelo, ryhmitelty kolmeen osaan sivunumeroineen |
| 4 | **Yrityksen perustiedot** — nimi, Y-tunnus, toimiala, vertailutoimiala, laatimispäivä, arvon laskentapäivä, käytetty tilinpäätös, tilinpäätöskaava |
| 5 | Väliotsikkosivu "Arvonmääritys" + osan oma minisisällys |
| 6 | **Osakekannan arvo** — jättinumero 149 387 657 €, substanssi/tuotto-jako donitsina, P/E ja P/B toimialan mediaania vasten, kumpikin selitettynä yhdellä lauseella marginaalissa |
| 7 | Arvon historiallinen kehitys, pylväät 2016–2020 + toimialan mediaaniviiva |
| 8 | **Arvonmääritysmalli lyhyesti** — kaavio + laskettu 1 M€:n esimerkki |
| 9 | Arvon kehittäminen — kaksi herkkyystaulukkoa, valittu solu korostettu |
| 10 | Ennuste — oletukset + toteuma/ennuste-pylväät samassa kuvaajassa |
| 11–12 | Toinen arvo: verottajan malli, oma jättinumero + selitys ja esimerkki |
| 13 | Väliotsikkosivu "Tilinpäätösanalyysi" |
| 14 | Sanallinen tilinpäätösanalyysi otsikoituna: Toimialavertailu / Volyymi / Kannattavuus / Maksuvalmius / Vakavaraisuus / Yhteenveto |
| 15–18 | Kuvaajat, joka ikisessä toimialan mediaani katkoviivana + Rating Alfa |
| 19–21 | Tunnusluvut, tuloslaskelma, tase |
| 26, 32 | Liitteet: tunnuslukujen laskentakaavat, arvonmääritysmallin kuvaus |

## Neljä asiaa, jotka heillä toimivat paremmin

1. **Kolmiosainen jäsennys väliotsikkosivuineen.** Lukija tietää koko ajan kummassa
   osassa on: arvo vai tilinpäätös. Meillä on yksi tasainen 16 kohdan lista ja vain
   yksi "Liitteet"-jakaja.
2. **Perustiedot omana sivunaan.** Arvon laskentapäivä, käytetty tilinpäätös,
   vertailutoimiala. Meillä renderer osaa tämän jo (`_mandate()` in `render.py`),
   mutta `meta.mandate` pyydetään VAIN kuolleessa 6-vaiheisessa promptissa
   (`6_tiivistelma.txt:53`) — `singlewriter.txt` ei pyydä sitä, joten lohko ei
   renderöidy koskaan tuotannossa.
3. **Toimialan mediaani joka ikisessä kuvaajassa.** Yksittäinen luku ilman
   vertailukohtaa ei kerro lukijalle onko se hyvä. Meillä ei ole yhtään
   mediaaniviivaa (tiedossa oleva aukko: sektorimediaanit Niklakselta).
4. **Malli selitetään rungossa, ei liitteessä** — kaaviona ja laskettuna
   esimerkkinä. Meillä metodologia on liitteessä ja pelkkää tekstiä.

## Missä me olemme selvästi parempia

Asiakastiedon "sanallinen analyysi" on mallipohjaista täytettä ("erittäin hyvä",
"välttävä") ilman kannanottoa. Ei skenaarioita, ei todennäköisyyksiä, ei
riskianalyysiä, ei kannanottoa siihen mikä arvion kaataisi, ei lähteitä.
Meillä on kaikki nämä, ja lisäksi markkinasignaalit ja verrokkivertailu.
Sisällöllisesti meidän raportti on eri sarjassa — ongelma on esitystapa, ei substanssi.

## Suurin selkeysongelma: kaksi eri lukua kahdessa eri yksikössä

Kansi: **12,3 M€** (skenaarioiden odotusarvo).
Heti seuraava sivu, avainlukukortti 1: **9 983 tEUR** (konservatiivinen perusskenaario).

Maallikkolukija näkee peräkkäisillä sivuilla kaksi eri numeroa kahdessa eri
mittakaavassa eikä tiedä kumpi on "se arvo". Asiakastieto näyttää yhden luvun
isona, ja toinen (verottajan malli) tulee viisi sivua myöhemmin omalla
otsikkosivullaan selvästi eri asiana. Tämä on isompi ongelma kuin kannen ulkoasu.

## Kannessa on virhe, joka on korjattava joka tapauksessa

`render.py:1450–1452` kovakoodaa kannen selitteen: "Laskettu yhtiön toteutuneista
luvuista ja ennusteista kassavirta- (DCF) **ja lisäarvomenetelmällä (EVA)**."

Heeros-ajossa painot olivat DCF 54,5 % ja tasepohjainen menetelmä 45,5 %, ja
raportti sanoo osiossa 6 suoraan: "EVA saa tämän vuoksi 0 %:n erillisen painon."
Kansi siis väittää arvon lasketun menetelmällä, jota raportti kertoo seuraavalla
sivulla nimenomaan jättäneensä käyttämättä. Teksti on renderöijän vakioteksti,
ei mallin tuotosta, joten virhe toistuu jokaisessa ajossa, jossa painot eivät ole
DCF+EVA. Korjaus: muodosta selite `derived["methods"]`-painoista.

## Kansi

Meidän kansi on itse asiassa **sisällöltään vahvempi** kuin Asiakastiedon:
heillä ei ole kannessa numeroa lainkaan, meillä on arvo, haarukka ja
skenaariokuvaaja. Mutta:

- Se ei ole kansi vaan tiivistelmäsivu. Kaksi selittävää kappaletta ja kolme
  selityssaraketta ovat liikaa kanteen.
- Yhtiön nimi on 14 pt:n metarivissä, ei otsikkona. Asiakastiedolla yhtiön nimi
  ja tuotteen nimi hallitsevat sivua.
- Alalaidan ~40 % on tyhjää.
- Ei visuaalista identiteettiä: ei kuvaa, ei tuotenimeä isona.

## Päätös, joka kuuluu toimarille, ei minulle

Kumpi luku johtaa raporttia: skenaarioiden odotusarvo (nyt kannessa) vai
konservatiivinen perusskenaario (nyt tiivistelmän ensimmäinen kortti)?

Tämä ei ole vahinko vaan `singlewriter.txt`:n nykyinen sopimus: KANSI-ohje
sanoo odotusarvon olevan kannen pääluku eikä sitä saa toistaa tiivistelmän
korttina. Joku on päättänyt näin. Yksikköjen yhtenäistäminen (M€ vs tEUR) on
turvallinen korjaus kummalla tahansa valinnalla; se kumpi luku johtaa, on
tuotepäätös.

## Mitä tekisin, tärkeysjärjestyksessä

| # | Toimenpide | Missä | Näkyy |
|---|---|---|---|
| 1 | Kannen EVA-väite pois — selite painoista | renderer | heti, myös vanhoissa ajoissa |
| 2 | Sama yksikkö kannessa ja tiivistelmässä | renderer (+prompti) | heti |
| 3 | Perustiedot-lohko `meta`-kentistä (nimi, Y-tunnus, toimiala, raportin päivä, käytetty tilikausi, yksikkö, taso) — EI `meta.mandate`-reittiä, koska puolet sen kentistä (käyttötarkoitus, tarkoitetut käyttäjät, markkinoitavuus) ei ole lähdedatassa ja malli joutuisi keksimään ne | renderer | heti |
| 4 | Väliotsikkosivut "Arvonmääritys" ja "Tausta ja tilinpäätös" nykyisen Liitteet-jakajan rinnalle | renderer | heti |
| 5 | Kannen tiivistäminen: yhtiön nimi isoksi, selityssarakkeet pois (ne ovat jo tiivistelmässä) | renderer | heti |
| 6 | Mallin selitys kaaviona arvonmääritysosion alkuun | renderer, staattinen | heti |
| 7 | Toimialan mediaanit kuvaajiin | odottaa Niklaksen sektoridataa | — |

Kohdat 1–6 ovat renderöijää eli verifioitavissa ilmaiseksi vanhalla ajolla.
Vain kohdan 2 promptipuoli liittyisi jonossa olevaan neljän testaamattoman
promptimuutoksen nippuun.
