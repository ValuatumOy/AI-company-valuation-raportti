"""Shared Valuatum endpoint configuration for the backend and kit scripts."""

import os
import re
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
    "SE": ("VALUATUM_SE_API_BASE_URL", "VALUATUM_SE_TOKEN", "Swedish"),
    "PDF": ("VALUATUM_PDF_API_BASE_URL", "VALUATUM_PDF_TOKEN", "PDF service"),
}
# Customers' own uploads: never generate estimates or import edits into them.
READ_ONLY_SOURCES = ("PDF",)

# Countries with automatic paid orders from their own Valuatum source.
# code: company code as the source stores it (a Danish group model adds K).
# generate_on_check: the source's models carry no estimates until generated, so
# the pre-checkout readiness check generates them first.
ORDER_COUNTRIES = {
    "DK": {"code": r"\d{8}K?", "id": "an exact eight-digit CVR", "currencies": ("DKK", "EUR"),
           "generate_on_check": False},
    "SE": {"code": r"\d{6}-\d{4}", "id": "an exact organisation number", "currencies": ("SEK",),
           "generate_on_check": True},
}


def order_code(country: str, query: str) -> str | None:
    """Canonical business id typed by a customer, or None if it is not one."""
    if country == "DK":
        clean = re.sub(r"^DK\s*", "", query.strip().upper())
        return clean if re.fullmatch(r"\d{8}", clean) else None
    if country == "SE":
        clean = re.sub(r"^SE|[\s-]", "", query.strip().upper())
        return f"{clean[:6]}-{clean[6:]}" if re.fullmatch(r"\d{10}", clean) else None
    raise ValueError("Unsupported order country")


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


def require_country_orders(country: str) -> None:
    api_token(country)
    if country not in ORDER_COUNTRIES \
            or os.environ.get(f"VALUATUM_{country}_ORDERS_ENABLED") != "1" \
            or os.environ.get(f"VALUATUM_{country}_FORECASTS_VERIFIED") != "1" \
            or os.environ.get(f"VALUATUM_{country}_SOURCE_UNIT") != "millions":
        raise ValueError(f"{SOURCES[country][2]} automatic orders await source and forecast/import verification")


def require_danish_orders() -> None:
    require_country_orders("DK")
