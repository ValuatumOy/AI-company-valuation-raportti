"""Import user-edited forecasts into a fresh ValuBuild model and wait for it.

On the feedback round a user may edit revenue (ns) / EBIT forecasts. This drives
the ValuBuild forecast-import REST endpoint (ACE #3048), which builds a
brand-new followed model (new fid) from the user's absolute values without
touching the base model or the shared consensus table, then waits for that
persistent job to finish and returns the new fid.

Mirrors estimate_trigger.py (same POST → poll → OK/ERROR/timeout shape, every
failure normalized to one exception type), with two differences: the request
carries a JSON body, and success yields the new resultFid rather than nothing.
"""
import asyncio
import os
import time
from typing import Any

import httpx

from valuatum_kit.config import api_base_url


POLL_INTERVAL_SECONDS = 10.0
# The phase-0 spike measured ~99 s for a cold import; the underlying model
# generation is the same recalculating save as estimate generation (which caps
# at 300 s). 600 s leaves margin so a slow cold run does not spuriously time out
# and strand a paid refinement round.
TIMEOUT_SECONDS = 600.0
REQUEST_TIMEOUT_SECONDS = 20.0


class ForecastImportError(RuntimeError):
    """Forecast import could not be completed safely."""


def _error_detail(response: httpx.Response) -> str:
    try:
        payload = response.json()
    except (ValueError, TypeError):
        payload = None
    if isinstance(payload, dict):
        for key in ("error", "message", "detail"):
            value = payload.get(key)
            if value:
                return str(value)
    text = response.text.strip()
    return text[:500] if text else f"HTTP {response.status_code}"


def _job_id(payload: Any) -> int:
    if not isinstance(payload, dict):
        raise ForecastImportError("ValuBuild palautti virheellisen job-vastauksen.")
    value = payload.get("jobId")
    try:
        job_id = int(value)
    except (TypeError, ValueError) as exc:
        raise ForecastImportError(
            "ValuBuild-vastauksesta puuttui kelvollinen jobId."
        ) from exc
    if job_id <= 0:
        raise ForecastImportError("ValuBuild palautti virheellisen jobId:n.")
    return job_id


def _result_fid(payload: Any) -> int:
    value = payload.get("resultFid") if isinstance(payload, dict) else None
    try:
        result_fid = int(value)
    except (TypeError, ValueError) as exc:
        raise ForecastImportError(
            "Ennusteiden tuonti valmistui, mutta ValuBuild ei palauttanut resultFid:tä."
        ) from exc
    if result_fid <= 0:
        raise ForecastImportError("ValuBuild palautti virheellisen resultFid:n.")
    return result_fid


async def import_and_wait(base_fid: int, values: list[dict]) -> int:
    """Import *values* onto *base_fid* and return the new fid once status is OK.

    Every failure is normalized to ForecastImportError so the caller can abort
    the refinement round before any paid LLM stage starts.
    """
    if base_fid <= 0:
        raise ForecastImportError("Ennusteiden tuonnin base-FID:n pitää olla positiivinen.")
    if not values:
        raise ForecastImportError("Ennusteiden tuontiin ei annettu yhtään arvoa.")

    token = os.environ.get("VALUATUM_TOKEN", "").strip()
    if not token:
        raise ForecastImportError(
            "VALUATUM_TOKEN puuttuu, joten ennusteiden tuontia ei voida käynnistää."
        )

    base_url = api_base_url()
    headers = {
        "accept": "application/json",
        "content-type": "application/json",
        "authorization": f"Bearer {token}",
    }
    body = {"baseFid": base_fid, "values": values}
    deadline = time.monotonic() + TIMEOUT_SECONDS

    try:
        async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT_SECONDS) as client:
            response = await client.post(
                f"{base_url}/estimates/import", headers=headers, json=body
            )
            if response.status_code not in (200, 202):
                raise ForecastImportError(
                    "Ennusteiden tuonnin käynnistys epäonnistui "
                    f"(HTTP {response.status_code}): {_error_detail(response)}"
                )
            payload = response.json()
            job_id = _job_id(payload)
            status = str(payload.get("status") or "").upper()

            while True:
                if status == "OK":
                    return _result_fid(payload)
                if status == "ERROR":
                    reason = payload.get("errorMessage") or "syytä ei ilmoitettu"
                    raise ForecastImportError(
                        f"Ennusteiden tuonti epäonnistui (job {job_id}): {reason}"
                    )
                if status not in ("PENDING", "RUNNING"):
                    raise ForecastImportError(
                        f"Ennusteiden tuonnin job {job_id} palautti tuntemattoman tilan: "
                        f"{status or 'puuttuu'}"
                    )

                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise ForecastImportError(
                        f"Ennusteiden tuonti aikakatkaistiin {int(TIMEOUT_SECONDS)} "
                        f"sekunnin jälkeen (job {job_id})."
                    )
                await asyncio.sleep(min(POLL_INTERVAL_SECONDS, remaining))

                response = await client.get(
                    f"{base_url}/estimates/imports/{job_id}", headers=headers
                )
                if response.status_code == 404:
                    raise ForecastImportError(
                        f"Ennusteiden tuonnin job {job_id} katosi pollauksen aikana (HTTP 404)."
                    )
                if response.status_code != 200:
                    raise ForecastImportError(
                        f"Ennusteiden tuonnin tilakysely epäonnistui (job {job_id}, "
                        f"HTTP {response.status_code}): {_error_detail(response)}"
                    )
                payload = response.json()
                returned_job_id = _job_id(payload)
                if returned_job_id != job_id:
                    raise ForecastImportError(
                        f"Ennusteiden tuonnin tilakysely palautti väärän jobId:n "
                        f"{returned_job_id} (odotettiin {job_id})."
                    )
                status = str(payload.get("status") or "").upper()
    except ForecastImportError:
        raise
    except (httpx.HTTPError, ValueError, TypeError) as exc:
        raise ForecastImportError(
            f"Yhteys ValuBuildin ennusteiden tuontiin epäonnistui: {exc}"
        ) from exc
