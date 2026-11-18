"""Shared Valuatum endpoint configuration for the backend and kit scripts."""

import os
from urllib.parse import urlsplit


DEFAULT_VALUATUM_API_BASE_URL = "https://profindertest.valuatum.com/rest"

# Report locales. A source is the Valuatum environment a model lives in; the
# country is the company's own. The PDF source holds models built from uploaded
# financial statements, so its companies can be from any listed country.
LANGUAGES = ("fi", "en")
COUNTRIES = ("FI", "DK", "SE", "NO", "DE", "US")
CURRENCIES = ("EUR", "DKK", "SEK", "NOK", "USD")
# Foreign countries whose reports come from a Valuatum model and must pass the
# full source forecast and cashflow verification. US is SEC demo input only.
SOURCE_VERIFIED_COUNTRIES = ("DK", "SE", "NO", "DE")

# source: (base URL env, token env, name used in errors)
SOURCES = {
    "FI": ("VALUATUM_API_BASE_URL", "VALUATUM_TOKEN", "Finnish"),
    "DK": ("VALUATUM_DK_API_BASE_URL", "VALUATUM_DK_TOKEN", "Danish"),
    "PDF": ("VALUATUM_PDF_API_BASE_URL", "VALUATUM_PDF_TOKEN", "PDF service"),
}
# Customers' own uploads: never generate estimates or import edits into them.
READ_ONLY_SOURCES = ("PDF",)


def api_base_url(country: str = "FI") -> str:
    """Return the REST base URL, defaulting to the test environment for FI."""
    if country not in SOURCES:
        raise ValueError("Unsupported automatic source country")
    base_env, _, name = SOURCES[country]
    configured = os.environ.get(base_env, "").strip().rstrip("/")
    if country == "FI":
        return configured or DEFAULT_VALUATUM_API_BASE_URL
    parsed = urlsplit(configured)
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password \
            or "test" in parsed.hostname.lower():
        raise ValueError(f"{name} production source is not configured")
    return configured


def api_token(country: str = "FI") -> str:
    api_base_url(country)
    token = os.environ.get(SOURCES[country][1], "").strip()
    if not token:
        raise ValueError(f"Valuatum {country} source token is not configured")
    return token


def require_writable_source(source: str) -> None:
    if source in READ_ONLY_SOURCES:
        raise ValueError(f"{SOURCES[source][2]} models are customer uploads and read-only")


def require_danish_orders() -> None:
    api_token("DK")
    if os.environ.get("VALUATUM_DK_ORDERS_ENABLED") != "1" \
            or os.environ.get("VALUATUM_DK_FORECASTS_VERIFIED") != "1" \
            or os.environ.get("VALUATUM_DK_SOURCE_UNIT") != "millions":
        raise ValueError("Danish automatic orders await source and forecast/import verification")
