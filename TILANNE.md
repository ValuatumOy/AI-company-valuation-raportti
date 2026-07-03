# Projektin tilanne — AI-arvonmääritys (2026-07-04)

## Mitä on tehty

**Raportin laatu (CEO-palautteen pohjalta)**
- Tekoäly osaa nyt järkeillä kuin analyytikko: tunnistaa uuden tuotteen
  kategorian ja verrokit (esim. luottoriskit.fi ≈ Fonecta Finder), arvioi
  markkinan koon (TAM/SAM) ja etsii aktiivisesti "käännekohtia" joita tasainen
  tilinpäätöshistoria ei näytä (poistunut myyntirajoite, pivot, kasvuvaihe,
  tekoälyn tuoma murros). Optimistinen skenaario johdetaan läpinäkyvänä ketjuna
  (markkina → osuus-% → liikevaihto → EBIT-% → arvo), joten miljoonaluokan arvo
  on perusteltavissa silloin kun se on aidosti perusteltu — ei arvattuna.
- DCF ja EVA näytetään samana arvona kahdella tavalla + verottajan mallin
  ristiintarkistus. FCFF-erittely näkyy vuosittain.
- 2-vaiheinen tarkennus: käyttäjä saa raportin + tekoälyn omat kysymykset,
  vastaa niihin, ja saa tarkennetun raportin (kierros 2 on maksuton).

**Asiantuntijakäyttö (valmis, käytössä)**
- Kutsuavaimet + krediitit: jokaiselle asiantuntijalle oma `exp_`-avain, jolla
  voi tuottaa rajatun määrän raportteja (esim. 2). Tarkennukset ovat maksuttomia.
- Sivu `/asiantuntija`: kirjaudu avaimella → valitse yritys → tuota
  arvonmääritys → näet raportin → tarkenna. Ei tarvita tiliä.

**Asiakassivusto (maksu-ensin)**
- Etusivun päätoiminto on nyt yrityshaku → hinnan näyttö → Stripe-maksu
  (aiemmin ilmainen lomake, joka oli ristiriidassa hinnoittelun kanssa).
- Kaikki "Tilaa raportti" -painikkeet ohjaavat maksulliseen polkuun.
- Toimitusaika korjattu: **30–60 minuuttia** (aiemmin "1–2 arkipäivää").
- Rikkinäiset esimerkkiraporttilinkit korjattu.

## Mitä seuraavaksi

1. **FID-ratkaisu (tekninen tiimi):** järjestelmä tarvitsee Valuatumin sisäisen
   FID-numeron hakeakseen taloustiedot. Asiakassivustolla on vain Y-tunnus.
   Kysymys tekniselle tiimille: *onko Valuatumissa rajapinta joka muuntaa
   Y-tunnuksen (tai nimen) FID-numeroksi, tai palauttaako yrityshaun rajapinta
   sen valmiiksi?* Tällä hetkellä itsepalvelu toimii vain etukäteen haetuille
   yrityksille (7 kpl valmiina).
2. **Maksullinen itsepalvelu + uudelleengenerointi ilman tiliä:** maksu →
   raportti generoituu automaattisesti → sähköpostiin **allekirjoitettu linkki**
   (`/raportti/{id}?t=…`), josta asiakas näkee raportin ja voi tarkentaa sitä.
   Ei tilejä, ei salasanoja — linkki on tunniste.
3. 199 € paketin kassapolku (puuttuu vielä).

## Miten saadaan tuotantoon

1. Vastaus FID-kysymykseen tekniseltä tiimiltä.
2. FID-resolveri backendiin → generointi onnistuu pelkällä Y-tunnuksella.
3. Allekirjoitettu raporttilinkki (backend) + Stripe-webhook, joka laukaisee
   automaattisen generoinnin maksun jälkeen.
4. `/raportti`-sivu asiakassivustolle (näyttää raportin + tarkennuksen).
5. Testaus todellisilla yrityksillä + julkaisu.

> Yksittäinen raportin generointi maksaa n. 0,67 € (mallikustannus). Testaus:
> luo `exp_`-avain, avaa `/asiantuntija`, valitse yritys, tuota raportti.
