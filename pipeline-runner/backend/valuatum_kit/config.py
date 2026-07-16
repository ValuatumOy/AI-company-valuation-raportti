"""Shared Valuatum endpoint configuration for the backend and kit scripts."""

import os


DEFAULT_VALUATUM_API_BASE_URL = "https://profindertest.valuatum.com/rest"


def api_base_url() -> str:
    """Return the REST base URL, defaulting to the test environment."""
    configured = os.environ.get("VALUATUM_API_BASE_URL", "").strip()
    return (configured or DEFAULT_VALUATUM_API_BASE_URL).rstrip("/")


def mcp_url() -> str | None:
    """Return the optional MCP URL, including its configured API key."""
    return os.environ.get("VALUATUM_MCP_URL", "").strip() or None
