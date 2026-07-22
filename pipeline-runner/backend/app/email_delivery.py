"""Email finished valuation reports.

Currently wired for Amazon SES (API v2) via boto3. It is inert until an AWS
region and a sender address are configured, so deploying this code cannot send
surprise emails by itself. Boto3 resolves credentials through its normal
provider chain (environment, session token, profile, or compute role).
"""
import asyncio
import html
import os
import re
from email import policy
from email.message import EmailMessage
from pathlib import Path

import boto3
from botocore.config import Config
from botocore.exceptions import BotoCoreError, ClientError, NoCredentialsError

from . import report, store

SES_CONFIG = Config(
    connect_timeout=10,
    read_timeout=30,
    # Prefer a possible duplicate over silently losing a finished report. Most
    # retries are safe (connect failures, throttling, transient 5xx), but a read
    # timeout can be ambiguous: SES may have accepted the previous attempt even
    # though its response never reached us.
    retries={"total_max_attempts": 3, "mode": "standard"},
)


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
    return (os.getenv("REPORT_EMAIL_FROM") or "").strip() or None


def _aws_region() -> str | None:
    return (
        os.getenv("AWS_REGION")
        or os.getenv("AWS_DEFAULT_REGION")
        or ""
    ).strip() or None


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


def _send_with_ses(
    *,
    region: str,
    sender: str,
    recipient: str,
    raw_message: bytes,
    run_id: str,
) -> dict:
    client = boto3.client(
        "sesv2",
        region_name=region,
        config=SES_CONFIG,
    )
    return client.send_email(
        FromEmailAddress=sender,
        Destination={"ToAddresses": [recipient]},
        Content={"Raw": {"Data": raw_message}},
        EmailTags=[{"Name": "run_id", "Value": run_id[:32]}],
    )


async def _dispatch(message: EmailMessage, *, region: str, sender: str,
                    to: str, rid: str) -> dict:
    """Send a built message via SES, normalising provider errors to sent=False.
    Shared by the report-ready and forecast-ready emails."""
    try:
        response = await asyncio.to_thread(
            _send_with_ses,
            region=region,
            sender=sender,
            recipient=to,
            raw_message=message.as_bytes(),
            run_id=rid,
        )
    except NoCredentialsError:
        return {
            "sent": False,
            "reason": "missing-aws-credentials",
            "detail": "AWS credentials were not available",
        }
    except ClientError as exc:
        error = exc.response.get("Error", {})
        return {
            "sent": False,
            "reason": "provider-error",
            "status_code": (
                exc.response.get("ResponseMetadata", {}).get("HTTPStatusCode")
            ),
            "code": error.get("Code"),
            "detail": str(error.get("Message") or "")[:500],
        }
    except BotoCoreError as exc:
        return {
            "sent": False,
            "reason": "provider-error",
            "status_code": None,
            "detail": type(exc).__name__,
        }
    return {"sent": True, "provider": "ses", "id": response.get("MessageId")}


async def send_report_ready(rid: str) -> dict:
    """Send the finished run's report to params.delivery_email when configured.

    Returns a small status dict for tests/logging. Raises only on unexpected
    local errors; provider errors are returned as sent=False.
    """
    run = store.get_run(rid)
    if not run:
        return {"sent": False, "reason": "run-not-found"}
    to = _recipient(run)
    if not to:
        return {"sent": False, "reason": "no-recipient"}
    if not _truthy_env("REPORT_EMAIL_ENABLED", "1"):
        return {"sent": False, "reason": "disabled"}
    region = _aws_region()
    sender = _sender()
    if not region:
        return {"sent": False, "reason": "missing-aws-region"}
    if not sender:
        return {"sent": False, "reason": "missing-sender"}

    report_json = store.final_report_json(rid)
    if report_json is None:
        return {"sent": False, "reason": "no-report-json"}

    meta = (report_json.get("meta") or {}) if isinstance(report_json, dict) else {}
    company = meta.get("company_name") or "yritys"
    version = _report_version(run)
    safe = _safe_filename(f"{company}-{version}")

    try:
        pdf_path = await asyncio.to_thread(report.generate_pdf, rid, report_json)
        attachment_data = Path(pdf_path).read_bytes()
        attachment_name = f"{safe}.pdf"
        attachment_type = ("application", "pdf")
    except Exception:
        html_path = await asyncio.to_thread(report.generate_html, rid, report_json)
        attachment_data = Path(html_path).read_bytes()
        attachment_name = f"{safe}.html"
        attachment_type = ("text", "html")

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

    message = EmailMessage(policy=policy.SMTP)
    message["From"] = sender
    message["To"] = to
    message["Subject"] = subject
    message.set_content(text_body)
    message.add_alternative(html_body, subtype="html")
    message.add_attachment(
        attachment_data,
        maintype=attachment_type[0],
        subtype=attachment_type[1],
        filename=attachment_name,
    )

    return await _dispatch(message, region=region, sender=sender, to=to, rid=rid)


async def send_forecast_ready(rid: str) -> dict:
    """Email the buyer a link to review/confirm forecasts before the report is
    generated (paid forecast-mode run parked at awaiting_forecast). No attachment
    — the report does not exist yet; the link is the whole point, and without it
    an opted-in buyer who closes the success page would never get their report."""
    run = store.get_run(rid)
    if not run:
        return {"sent": False, "reason": "run-not-found"}
    to = _recipient(run)
    if not to:
        return {"sent": False, "reason": "no-recipient"}
    if not _truthy_env("REPORT_EMAIL_ENABLED", "1"):
        return {"sent": False, "reason": "disabled"}
    region = _aws_region()
    sender = _sender()
    if not region:
        return {"sent": False, "reason": "missing-aws-region"}
    if not sender:
        return {"sent": False, "reason": "missing-sender"}
    link = _report_link(rid, run)
    if not link:
        return {"sent": False, "reason": "no-link"}

    company = (run.get("params") or {}).get("company_name") or "yritys"
    subject = f"Tarkista ennusteet: {company} -arvonmaaritys"
    escaped_company = html.escape(str(company))
    esc_link = html.escape(link)
    html_body = (
        "<p>Hei,</p>"
        f"<p>Kiitos tilauksesta. Ennen kuin luomme {escaped_company} "
        "-arvonmaaritysraportin, voit tarkistaa ja halutessasi muokata "
        "liikevaihto- ja EBIT-ennusteita.</p>"
        f'<p><a href="{esc_link}">Avaa ja vahvista ennusteet</a></p>'
        "<p>Raportti luodaan vasta kun olet vahvistanut ennusteet linkin takana. "
        "Voit myos jatkaa suoraan meidan ennusteillamme.</p>"
        "<p>Ystavallisin terveisin,<br>Valuatum</p>"
    )
    text_body = (
        f"Hei,\n\nKiitos tilauksesta. Ennen kuin luomme {company} "
        "-arvonmaaritysraportin, voit tarkistaa ja halutessasi muokata "
        "liikevaihto- ja EBIT-ennusteita.\n\n"
        f"Avaa ja vahvista ennusteet: {link}\n\n"
        "Raportti luodaan vasta kun olet vahvistanut ennusteet. "
        "Voit myos jatkaa suoraan meidan ennusteillamme.\n\n"
        "Ystavallisin terveisin,\nValuatum"
    )

    message = EmailMessage(policy=policy.SMTP)
    message["From"] = sender
    message["To"] = to
    message["Subject"] = subject
    message.set_content(text_body)
    message.add_alternative(html_body, subtype="html")

    return await _dispatch(message, region=region, sender=sender, to=to, rid=rid)
