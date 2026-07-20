"""Turn a user's free-text forecast request into structured forecast edits.

Stage-0 forecast values are expressed in tEUR. ValuBuild's forecast import API,
and therefore every ``ForecastEdit.value``, uses millions. Keeping that unit
boundary explicit here is important: a plausible-looking 1000x error otherwise
passes the ordinary allowlist and finite-number validation.
"""
from __future__ import annotations

import json
import math
import os
from typing import Any

from . import openrouter


DEFAULT_MODEL = "google/gemini-3.1-pro-preview"
_SERIES_BY_VARNAME = {"ns": "net_sales", "ebit": "ebit"}
_LABELS = {"ns": "Liikevaihto", "ebit": "EBIT"}


class ForecastInterpretError(RuntimeError):
    """The interpretation call failed or returned an unusable response."""


def _current_rows(forecast: dict[str, Any]) -> list[dict[str, Any]]:
    years = forecast.get("years") or []
    rows: list[dict[str, Any]] = []
    for varname, series_name in _SERIES_BY_VARNAME.items():
        values = forecast.get(series_name) or []
        for index, year in enumerate(years):
            if index >= len(values):
                continue
            value = values[index]
            if isinstance(year, bool) or not isinstance(year, int):
                continue
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                continue
            if not math.isfinite(value):
                continue
            rows.append({
                "varname": varname,
                "year": year,
                "current_value_meur": value / 1000,
            })
    return rows


def _prompt(text: str, forecast: dict[str, Any]) -> str:
    current = _current_rows(forecast)
    return f"""Tulkitse käyttäjän ennusteiden muokkauspyyntö rakenteiseksi JSONiksi.

Sallitut muuttujat:
- ns = liikevaihto
- ebit = EBIT

KRIITTINEN YKSIKKÖSOPIMUS:
- Alla olevat current_value_meur-arvot ja vastauksen edits[].value ovat MILJOONIA EUROJA.
- Esimerkiksi 5,3 miljoonaa euroa on JSON-luku 5.3, EI 5300.
- Käyttäjä voi kirjoittaa tEUR, kEUR tai tuhatta euroa; muunna silloin miljooniksi
  (esimerkiksi 5 300 tEUR -> 5.3).

Palauta vain yksi JSON-objekti tässä muodossa:
{{
  "edits": [{{"varname": "ns|ebit", "year": 2027, "value": 5.3}}],
  "summary": "lyhyt suomenkielinen kuvaus siitä, miten pyyntö tulkittiin",
  "notes": ["mahdolliset epävarmuudet tai oletukset suomeksi"]
}}

Säännöt:
- Käytä vain alla olevia muuttuja-vuosi-pareja.
- value on absoluuttinen uusi arvo, ei prosentti eikä muutosmäärä.
- Laske prosentti-, kasvu- ja marginaalipyynnöistä kaikki niiden muuttamat vuosiarvot.
- Älä palauta soluja, joiden arvo ei muutu.
- Jos pyyntö on ristiriitainen, valitse varovainen yksiselitteinen tulkinta ja kerro
  oletus notes-kentässä. Älä keksi käyttäjän pyynnön ulkopuolisia muutoksia.

Nykyennuste (miljoonaa euroa):
{json.dumps(current, ensure_ascii=False)}

Käyttäjän pyyntö:
<user_request>{text}</user_request>
"""


def _normalise_response(data: Any) -> dict[str, Any]:
    if not isinstance(data, dict):
        raise ForecastInterpretError("AI-tulkinta ei palauttanut JSON-objektia.")
    edits = data.get("edits")
    if not isinstance(edits, list):
        raise ForecastInterpretError("AI-tulkinnasta puuttuu edits-lista.")

    normalised_edits: list[dict[str, Any]] = []
    seen: set[tuple[Any, Any]] = set()
    for edit in edits:
        if not isinstance(edit, dict):
            raise ForecastInterpretError("AI-tulkinnan edits sisältää virheellisen rivin.")
        if not {"varname", "year", "value"}.issubset(edit):
            raise ForecastInterpretError("AI-tulkinnan muutosriviltä puuttuu kenttiä.")
        key = (edit.get("varname"), edit.get("year"))
        if key in seen:
            raise ForecastInterpretError("AI-tulkinta palautti saman ennustesolun kahdesti.")
        seen.add(key)
        normalised_edits.append({
            "varname": edit.get("varname"),
            "year": edit.get("year"),
            "value": edit.get("value"),
        })

    summary = data.get("summary")
    if not isinstance(summary, str):
        summary = ""
    summary = summary.strip()

    raw_notes = data.get("notes")
    if isinstance(raw_notes, str):
        raw_notes = [raw_notes]
    notes = []
    if isinstance(raw_notes, list):
        notes = [str(note).strip() for note in raw_notes if str(note).strip()]

    return {"edits": normalised_edits, "summary": summary, "notes": notes}


async def interpret(text: str, forecast: dict[str, Any]) -> dict[str, Any]:
    """Call the configured LLM and return its normalised structured proposal."""
    model = (os.getenv("FORECAST_INTERPRET_MODEL") or DEFAULT_MODEL).strip()
    try:
        response = await openrouter.chat(
            model=model,
            prompt=_prompt(text, forecast),
            temperature=0.0,
            max_tokens=4000,
            expects_json=True,
        )
    except Exception as exc:
        raise ForecastInterpretError(f"Ennustepyynnön AI-tulkinta epäonnistui: {exc}") from exc

    data = openrouter.extract_json(response.get("text") or "")
    return _normalise_response(data)


def magnitude_notes(forecast: dict[str, Any], edits: list[Any]) -> list[str]:
    """Warn when a proposed value differs by more than one order of magnitude."""
    current = {
        (row["varname"], row["year"]): row["current_value_meur"]
        for row in _current_rows(forecast)
    }
    notes: list[str] = []
    for edit in edits:
        varname = getattr(edit, "varname", None)
        year = getattr(edit, "year", None)
        value = getattr(edit, "value", None)
        old = current.get((varname, year))
        if old is None or not isinstance(value, (int, float)) or not math.isfinite(value):
            continue
        if math.isclose(old, 0.0, abs_tol=1e-12):
            if not math.isclose(value, 0.0, abs_tol=1e-12):
                notes.append(
                    f"{_LABELS.get(varname, varname)} {year}: nykyarvo on 0, joten "
                    "muutoksen kertaluokka kannattaa tarkistaa."
                )
            continue
        ratio = abs(value) / abs(old)
        if ratio > 10 or ratio < 0.1:
            notes.append(
                f"{_LABELS.get(varname, varname)} {year}: ehdotus poikkeaa nykyarvosta "
                "yli kymmenkertaisesti. Tarkista erityisesti miljoonat/tEUR-yksikkö."
            )
    return notes
