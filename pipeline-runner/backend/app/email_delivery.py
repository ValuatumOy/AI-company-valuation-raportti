"""Email finished valuation reports.

Currently wired for Resend's REST API. It is inert until RESEND_API_KEY and a
sender address are configured, so deploying this code cannot send surprise
emails by itself.
"""
import asyncio
import base64
import html
import os
import re
from pathlib import Path

import httpx

from . import report, store

RESEND_URL = "https://api.resend.com/emails"


def _truthy_env(name: str, default: str = "1") -> bool:
    return (os.getenv(name, default) or "").strip().lower() not in {"0", "false", "no", "off"}


def _safe_filename(value: str) -> str:
    slug = re.sub(r"[^A-Za-z0-9_.-]+", "-", value).strip("-")
    return slug[:80] or "raportti"


def _report_version(run: dict) -> str:
    return "tarkennettu versio" if run.get("parent_run_id") else "ensimmainen versio"


def _recipient(run: dict) -> str | None:
    email = ((run.get("params") or {}).get("delivery_email") or "").strip()
    return email or None


def _sender() -> str | None:
    return (os.getenv("REPORT_EMAIL_FROM") or os.getenv("RESEND_FROM") or "").strip() or None


def _report_link(rid: str, run: dict) -> str | None:
    """The exclusive link back to the report + round-2 clarification UI. Only
    meaningful for a run tied to an access key (self-serve/paid flow) — admin
    runs have no key for a public page to authenticate with."""
    key = run.get("access_key")
    if not key:
        return None
    site = (os.getenv("CLIENT_SITE_URL") or "").strip().rstrip("/")
    if not site:
        return None
    return f"{site}/testi?key={key}&rid={rid}"


async def send_report_ready(rid: str) -> dict:
    """Send the finished run's report to params.delivery_email when configured.

    Returns a small status dict for tests/logging. Raises only on unexpected
    local errors; provider HTTP errors are returned as sent=False.
    """
    run = store.get_run(rid)
    if not run:
        return {"sent": False, "reason": "run-not-found"}
    to = _recipient(run)
    if not to:
        return {"sent": False, "reason": "no-recipient"}
    if not _truthy_env("REPORT_EMAIL_ENABLED", "1"):
        return {"sent": False, "reason": "disabled"}
    api_key = (os.getenv("RESEND_API_KEY") or "").strip()
    sender = _sender()
    if not api_key:
        return {"sent": False, "reason": "missing-resend-api-key"}
    if not sender:
        return {"sent": False, "reason": "missing-sender"}

    report_json = store.final_report_json(rid)
    if report_json is None:
        return {"sent": False, "reason": "no-report-json"}

    meta = (report_json.get("meta") or {}) if isinstance(report_json, dict) else {}
    company = meta.get("company_name") or "yritys"
    version = _report_version(run)
    safe = _safe_filename(f"{company}-{version}")
    attachments = []

    try:
        pdf_path = await asyncio.to_thread(report.generate_pdf, rid, report_json)
        content = base64.b64encode(Path(pdf_path).read_bytes()).decode("ascii")
        attachments.append({"filename": f"{safe}.pdf", "content": content})
    except Exception:
        html_path = await asyncio.to_thread(report.generate_html, rid, report_json)
        content = base64.b64encode(Path(html_path).read_bytes()).decode("ascii")
        attachments.append({"filename": f"{safe}.html", "content": content})

    link = _report_link(rid, run)

    subject = f"Arvonmaaritysraportti valmis: {company}"
    escaped_company = html.escape(str(company))
    link_html = (
        f'<p><a href="{html.escape(link)}">Avaa raportti ja jatka tarkennuksia</a></p>'
        if link else ""
    )
    link_text = f"\n\nAvaa raportti ja jatka tarkennuksia: {link}" if link else ""
    html_body = (
        "<p>Hei,</p>"
        f"<p>{escaped_company} -arvonmaaritysraportin {html.escape(version)} on valmis.</p>"
        "<p>Raportti on taman viestin liitteena.</p>"
        f"{link_html}"
        "<p>Ystavallisin terveisin,<br>Valuatum</p>"
    )
    text_body = (
        f"Hei,\n\n{company} -arvonmaaritysraportin {version} on valmis. "
        f"Raportti on taman viestin liitteena.{link_text}\n\nYstavallisin terveisin,\nValuatum"
    )
    payload = {
        "from": sender,
        "to": [to],
        "subject": subject,
        "html": html_body,
        "text": text_body,
        "attachments": attachments,
        "tags": [{"name": "run_id", "value": rid[:32]}],
    }

    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.post(
            RESEND_URL,
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            json=payload,
        )
    if resp.status_code >= 400:
        return {"sent": False, "reason": "provider-error", "status_code": resp.status_code,
                "detail": resp.text[:500]}
    try:
        data = resp.json()
    except Exception:
        data = {}
    return {"sent": True, "provider": "resend", "id": data.get("id")}
