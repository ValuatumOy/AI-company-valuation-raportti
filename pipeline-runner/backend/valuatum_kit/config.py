"""Shared Valuatum endpoint configuration for the backend and kit scripts."""

import os
from urllib.parse import urlsplit


DEFAULT_VALUATUM_API_BASE_URL = "https://profindertest.valuatum.com/rest"


def api_base_url(country: str = "FI") -> str:
    """Return the REST base URL, defaulting to the test environment."""
    if country == "DK":
        configured = os.environ.get("VALUATUM_DK_API_BASE_URL", "").strip().rstrip("/")
        parsed = urlsplit(configured)
        if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password \
                or "test" in parsed.hostname.lower():
            raise ValueError("Danish production source is not configured")
        return configured
    if country != "FI":
        raise ValueError("Unsupported automatic source country")
    configured = os.environ.get("VALUATUM_API_BASE_URL", "").strip()
    return (configured or DEFAULT_VALUATUM_API_BASE_URL).rstrip("/")


def api_token(country: str = "FI") -> str:
    api_base_url(country)
    token = os.environ.get("VALUATUM_DK_TOKEN" if country == "DK" else "VALUATUM_TOKEN", "").strip()
    if not token:
        raise ValueError(f"Valuatum {country} source token is not configured")
    return token


def require_danish_orders() -> None:
    api_token("DK")
    if os.environ.get("VALUATUM_DK_ORDERS_ENABLED") != "1" \
            or os.environ.get("VALUATUM_DK_FORECASTS_VERIFIED") != "1" \
            or os.environ.get("VALUATUM_DK_SOURCE_UNIT") != "millions":
        raise ValueError("Danish automatic orders await source and forecast/import verification")
