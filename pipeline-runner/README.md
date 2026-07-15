# Valuaatio-pipeline runner

Paikallinen työkalu monivaiheisen AI-arvonmääritys-pipelinen ajoon. Muokkaa
jokaisen vaiheen promptia ja mallia UI:ssa, paina nappia, ketju ajetaan
järjestyksessä → output + Python-validaattorit per vaihe. Ei copy-pastea.

- **Backend:** FastAPI + SQLite + httpx (OpenRouter). API-avain pysyy
  serverillä, ei koskaan selaimeen.
- **Frontend:** React + Vite + TypeScript + Tailwind + Monaco.

## Käynnistys

```bash
./run.sh
```

Käynnistää backendin `:8000` ja frontendin `:5173`. Avaa
<http://localhost:5173>. Vite proxyttää `/api` → `:8000`.

Ensimmäisellä ajolla:
1. luo `backend/.venv` ja asentaa riippuvuudet,
2. kopioi `backend/.env.example` → `backend/.env` — **lisää
   `OPENROUTER_API_KEY`**,
3. seedaa SQLiteen 6-vaiheisen oletuspipelinen.

Manuaalisesti kahdessa terminaalissa:

```bash
# 1
cd backend && python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/uvicorn app.main:app --port 8000 --reload
# 2
cd frontend && npm install && npm run dev
```

## Käyttö

1. **Vaihe 0 (FAKTAT):** liitä input_data JSON käsin (tai täytä
   `backend/fetchers/company_data.py` ja hae Y-tunnuksella). Valmis
   esimerkki: `backend/fetchers/sample_input_data.json`.
2. Muokkaa vaiheiden promptit ja mallit. Muutokset tallentuvat SQLiteen
   automaattisesti (eivät katoa refreshissä).
3. **Aja koko pipeline** → vaiheet syttyvät live (SSE).
4. Validaattorin failatessa rivi muuttuu punaiseksi; "Pysäytä jos validaattori
   failaa" -kytkin pysäyttää ketjun. Korjaa prompti → "aja tästä eteenpäin".

## Valuatum JSON Export (Stage 0 FAKTAT)

Topbar **📊 Valuatum JSON** → syötä vain **Company name + FID** → **Generate
JSON** → ladattava `*_modeldata_complete.json`. Sama JSON voi mennä suoraan
Stage 0:n input_dataksi ("→ Käytä Stage 0 input_datana").

Backend ajaa vendoroidun kitin (`backend/valuatum_kit/`) kahdessa vaiheessa:
1. `/rest/modeldata` fid:llä → DCF / WACC / EVA / forecasts.
2. Profinder MCP backfill company_code:lla → historialliset actualsit.

Salaisuudet vain backendin `.env`:ssä (ei koskaan frontendiin):

```bash
VALUATUM_TOKEN=...              # pakollinen
VALU_ESTIMATE_GENERATION_URL=... # esim. https://profinder.valuatum.com/rest
VALU_MCP_PROFINDER_URL=...      # suositeltu (täydet actualsit + credit risk)
```

Kun `VALU_ESTIMATE_GENERATION_URL` on asetettu, backend generoi ennusteet aina
ennen modeldata-hakua ja odottaa jobin valmistumista. Generointivirhe tai viiden
minuutin aikakatkaisu pysäyttää Stage 0:n ennen maksullisia LLM-vaiheita. URL:n
puuttuminen ohittaa askeleen paikallista kehitystä ja hätäpalautusta varten.

- Oletukset `actuals=5`, `estimates=10` (Advanced-osiossa muutettavissa).
- Peruskäytössä company_code johdetaan modeldata-vastauksen y-tunnuksesta.
  Jos companyCode näyttää väärältä (esim. Teippimestarit Oy → 24388345),
  anna **Advanced → company_code_override**.
- Null-arvot säilytetään, mitään ei keksitä.
- Jos DCF/WACC/forecastit puuttuvat: *"Forecasts may need to be generated in
  Valuatum UI first, then rerun export."* — generoi forecastit Valuatumissa ja
  aja export uudelleen.

Top-level skeema = examples/*.json: `meta, headcount, actuals, forecast,
forecast_parameters, valuation_engine, key_ratios, credit_risk, peers,
client_reported_signals, flags`.

## Validaattorit

Python-funktio per vaihe, muokattavissa UI:ssa:

```python
def validate(output: dict, context: dict) -> dict:
    return {"passed": bool, "checks": [{"name","passed","detail"}, ...]}
```

Ajetaan eristetyssä subprosessissa (5 s timeout). Seedatut oikeat
validaattorit: `backend/validators_seed/`:

- **stage0_schema** — FAKTAT-skeema (pakolliset avaimet, series-pituudet).
- **stage2_scoring** — menetelmäpainot 100 %, `expected_value` =
  Σ(p × lattiaan rajattu arvo), todennäköisyydet 100 %, lattiat ≥ 0.
- **stage3_numbers** — tekstien numerot jäljitettävä input_dataan,
  diskonttaussaniteetti `|disc| ≤ |nominaali|`, DCF-silta täsmää,
  termikonsistenssi, breakeven. **Tärkein osa.**
- **stage5_final** — section-numerot vs `machine_readable`, kansiluku =
  `expected_value`.

## Mitä tämä EI tee

Ei renderöi raporttia (erillinen JSON→HTML-generaattori). Ei autentikointia,
Dockeria eikä pilveä. Stage 0:n hakulogiikka ja vaiheiden promptit ovat
käyttäjän täytettäviä — tämä on muokattava kuori, ei sisältö.
