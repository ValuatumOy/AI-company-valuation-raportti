"""Trigger estimate generation and wait for its persistent job.

The integration is a hard gate: modeldata must not be fetched until Valuatum
has persisted fresh estimates. The REST API defaults to profindertest.
"""
import asyncio
import os
import time
from typing import Any

import httpx

from valuatum_kit.config import api_base_url


POLL_INTERVAL_SECONDS = 10.0
TIMEOUT_SECONDS = 300.0
REQUEST_TIMEOUT_SECONDS = 20.0


class EstimateGenerationError(RuntimeError):
    """Estimate generation could not be completed safely."""


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
        raise EstimateGenerationError("ValuBuild palautti virheellisen job-vastauksen.")
    value = payload.get("jobId")
    try:
        job_id = int(value)
    except (TypeError, ValueError) as exc:
        raise EstimateGenerationError(
            "ValuBuild-vastauksesta puuttui kelvollinen jobId."
        ) from exc
    if job_id <= 0:
        raise EstimateGenerationError("ValuBuild palautti virheellisen jobId:n.")
    return job_id


async def trigger_and_wait(fid: int) -> None:
    """Start estimate generation for *fid* and return only after status OK.

    Every failure is normalized to EstimateGenerationError so stage 0 can
    stop before any paid LLM stage starts.
    """
    if fid <= 0:
        raise EstimateGenerationError("Ennustegeneroinnin FID:n pitää olla positiivinen.")

    token = os.environ.get("VALUATUM_TOKEN", "").strip()
    if not token:
        raise EstimateGenerationError(
            "VALUATUM_TOKEN puuttuu, joten ennustegenerointia ei voida käynnistää."
        )

    base_url = api_base_url()
    headers = {"accept": "application/json", "authorization": f"Bearer {token}"}
    deadline = time.monotonic() + TIMEOUT_SECONDS

    try:
        async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT_SECONDS) as client:
            response = await client.post(
                f"{base_url}/estimates/generate/{fid}", headers=headers
            )
            if response.status_code not in (200, 202):
                raise EstimateGenerationError(
                    "Ennustegeneroinnin käynnistys epäonnistui "
                    f"(HTTP {response.status_code}): {_error_detail(response)}"
                )
            payload = response.json()
            job_id = _job_id(payload)
            status = str(payload.get("status") or "").upper()

            while True:
                if status == "OK":
                    return
                if status == "ERROR":
                    reason = payload.get("errorMessage") or "syytä ei ilmoitettu"
                    raise EstimateGenerationError(
                        f"Ennustegenerointi epäonnistui (job {job_id}): {reason}"
                    )
                if status not in ("PENDING", "RUNNING"):
                    raise EstimateGenerationError(
                        f"Ennustegeneroinnin job {job_id} palautti tuntemattoman tilan: "
                        f"{status or 'puuttuu'}"
                    )

                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise EstimateGenerationError(
                        f"Ennustegenerointi aikakatkaistiin {int(TIMEOUT_SECONDS)} "
                        f"sekunnin jälkeen (job {job_id})."
                    )
                await asyncio.sleep(min(POLL_INTERVAL_SECONDS, remaining))

                response = await client.get(
                    f"{base_url}/estimates/jobs/{job_id}", headers=headers
                )
                if response.status_code == 404:
                    raise EstimateGenerationError(
                        f"Ennustegeneroinnin job {job_id} katosi pollauksen aikana (HTTP 404)."
                    )
                if response.status_code != 200:
                    raise EstimateGenerationError(
                        f"Ennustegeneroinnin tilakysely epäonnistui (job {job_id}, "
                        f"HTTP {response.status_code}): {_error_detail(response)}"
                    )
                payload = response.json()
                returned_job_id = _job_id(payload)
                if returned_job_id != job_id:
                    raise EstimateGenerationError(
                        f"Ennustegeneroinnin tilakysely palautti väärän jobId:n "
                        f"{returned_job_id} (odotettiin {job_id})."
                    )
                status = str(payload.get("status") or "").upper()
    except EstimateGenerationError:
        raise
    except (httpx.HTTPError, ValueError, TypeError) as exc:
        raise EstimateGenerationError(
            f"Yhteys ValuBuildin ennustegenerointiin epäonnistui: {exc}"
        ) from exc
