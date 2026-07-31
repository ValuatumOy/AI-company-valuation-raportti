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
import unicodedata
from email import policy
from email.message import EmailMessage
from pathlib import Path

import boto3
from botocore.config import Config
from botocore.exceptions import BotoCoreError, ClientError, NoCredentialsError

from . import report, store

# Client-site path that renders a run for its buyer. Every customer-facing link
# we mint points here — report emails, forecast-review emails, and the paid
# round-2 Stripe redirects in main.py. Kept in one place because those links are
# permanent: they live in inboxes long after a rename. The client keeps the old
# /testi path alive as a redirect for links already sent.
REPORT_PATH = "/raportti"

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


_TRANSLITERATE = str.maketrans({"ø": "o", "Ø": "O", "æ": "ae", "Æ": "AE", "ß": "ss"})


def _safe_filename(value: str) -> str:
    """ASCII attachment filename, transliterated rather than gutted.

    The MIME layer would carry "Mäkelä Oy.pdf" correctly (RFC 2231), but the name
    still has to survive the recipient's mail client and filesystem, so keep it
    ASCII. Decompose first so Finnish names degrade to "Makela-Oy" instead of the
    "M-kel-Oy" the bare regex produced — every umlaut was becoming a dash.
    """
    decomposed = unicodedata.normalize("NFKD", value.translate(_TRANSLITERATE))
    ascii_only = "".join(c for c in decomposed if not unicodedata.combining(c))
    slug = re.sub(r"[^A-Za-z0-9_.-]+", "-", ascii_only).strip("-")
    return slug[:80] or "raportti"


def _report_version(run: dict) -> str:
    return "tarkennettu versio" if run.get("parent_run_id") else "ensimmäinen versio"


def _recipient(run: dict) -> str | None:
    email = ((run.get("params") or {}).get("delivery_email") or "").strip()
    return email or None


def _company_name(run: dict) -> str:
    name = ((run.get("params") or {}).get("company_name") or "").strip()
    return name or "tuntematon yritys"


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
    return f"{site}{REPORT_PATH}?key={key}&rid={rid}"


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

    subject = f"Arvonmääritysraportti valmis: {company}"
    escaped_company = html.escape(str(company))
    link_html = (
        f'<p><a href="{html.escape(link)}">Avaa raportti ja jatka tarkennuksia</a></p>'
        if link else ""
    )
    link_text = f"\n\nAvaa raportti ja jatka tarkennuksia: {link}" if link else ""
    html_body = (
        "<p>Hei,</p>"
        f"<p>{escaped_company} -arvonmääritysraportin {html.escape(version)} on valmis.</p>"
        "<p>Raportti on tämän viestin liitteenä.</p>"
        f"{link_html}"
        "<p>Ystävällisin terveisin,<br>Valuatum</p>"
    )
    text_body = (
        f"Hei,\n\n{company} -arvonmääritysraportin {version} on valmis. "
        f"Raportti on tämän viestin liitteenä.{link_text}\n\nYstävällisin terveisin,\nValuatum"
    )

    message = EmailMessage(policy=policy.SMTP)
    message["From"] = sender
    message["To"] = to
    message["Subject"] = subject
    message.set_content(text_body, cte="quoted-printable")
    message.add_alternative(html_body, subtype="html", cte="quoted-printable")
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
    subject = f"Tarkista ennusteet: {company} -arvonmääritys"
    escaped_company = html.escape(str(company))
    esc_link = html.escape(link)
    html_body = (
        "<p>Hei,</p>"
        f"<p>Kiitos tilauksesta. Ennen kuin luomme {escaped_company} "
        "-arvonmääritysraportin, voit tarkistaa ja halutessasi muokata "
        "liikevaihto- ja EBIT-ennusteita.</p>"
        f'<p><a href="{esc_link}">Avaa ja vahvista ennusteet</a></p>'
        "<p>Raportti luodaan vasta kun olet vahvistanut ennusteet linkin takana. "
        "Voit myös jatkaa suoraan meidän ennusteillamme.</p>"
        "<p>Ystävällisin terveisin,<br>Valuatum</p>"
    )
    text_body = (
        f"Hei,\n\nKiitos tilauksesta. Ennen kuin luomme {company} "
        "-arvonmääritysraportin, voit tarkistaa ja halutessasi muokata "
        "liikevaihto- ja EBIT-ennusteita.\n\n"
        f"Avaa ja vahvista ennusteet: {link}\n\n"
        "Raportti luodaan vasta kun olet vahvistanut ennusteet. "
        "Voit myös jatkaa suoraan meidän ennusteillamme.\n\n"
        "Ystävällisin terveisin,\nValuatum"
    )

    message = EmailMessage(policy=policy.SMTP)
    message["From"] = sender
    message["To"] = to
    message["Subject"] = subject
    message.set_content(text_body, cte="quoted-printable")
    message.add_alternative(html_body, subtype="html", cte="quoted-printable")

    return await _dispatch(message, region=region, sender=sender, to=to, rid=rid)


# ---- internal (Valuatum-facing) mail -----------------------------------------
# Nobody here watches the Railway logs, so the things that used to be a lone
# print() — a report held back by its readiness checks, a run that died with a
# paying customer waiting, an order that needs fulfilling by hand — get emailed
# to the shared inbox instead. Modelled on Osakeanalyysi-nettisivut's
# server/email.js (sendAdminNotification / sendAdminDeliveryNotice /
# sendCoverageRequest / sendAdminAlert): metadata tables, never attachments,
# and never allowed to disturb the customer-facing path.

ADMIN_SUBJECT_PREFIX = "[Arvonmääritys]"
DEFAULT_ADMIN_EMAIL = "arvonmaaritys26@valuatum.com"


def _admin_recipient() -> str:
    return (os.getenv("ADMIN_EMAIL") or "").strip() or DEFAULT_ADMIN_EMAIL


def _ses_tag(value: str, fallback: str = "admin") -> str:
    """SES EmailTags values accept only [A-Za-z0-9_-] and must be non-empty."""
    cleaned = re.sub(r"[^A-Za-z0-9_-]", "-", str(value or ""))[:32].strip("-")
    return cleaned or fallback


def _admin_ready() -> tuple[str, str, str] | dict:
    """(region, sender, recipient) when internal mail can be sent, otherwise the
    same sent=False dict shape the customer senders return.

    REPORT_EMAIL_ENABLED gates internal mail too: it is the one kill switch for
    everything this module sends, so an unconfigured deploy stays silent."""
    if not _truthy_env("REPORT_EMAIL_ENABLED", "1"):
        return {"sent": False, "reason": "disabled"}
    region = _aws_region()
    if not region:
        return {"sent": False, "reason": "missing-aws-region"}
    sender = _sender()
    if not sender:
        return {"sent": False, "reason": "missing-sender"}
    return region, sender, _admin_recipient()


_EMAIL_VALUE = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")


def _cell_html(value: str) -> str:
    """Clickable where it makes sense. A report link pasted as bare text is a
    link the reader has to copy out by hand, and mail clients only autolink the
    plain-text part — which is not the part they are looking at."""
    escaped = html.escape(value)
    if value.startswith(("https://", "http://")):
        return f'<a href="{escaped}">{escaped}</a>'
    if _EMAIL_VALUE.match(value):
        return f'<a href="mailto:{escaped}">{escaped}</a>'
    return escaped


def _admin_message(
    subject: str,
    intro: str,
    rows: list[tuple[str, object]],
    *,
    sender: str,
    to: str,
) -> EmailMessage:
    text_lines = [intro, ""]
    html_rows = []
    for label, value in rows:
        shown = str(value).strip() if value not in (None, "") else "—"
        text_lines.append(f"{label}: {shown}")
        html_rows.append(
            f'<tr><td style="color:#666;padding:4px 16px 4px 0;vertical-align:top;">'
            f"{html.escape(str(label))}</td><td>{_cell_html(shown)}</td></tr>"
        )
    html_body = (
        f"<p><strong>{html.escape(intro)}</strong></p>"
        '<table cellpadding="0" cellspacing="0" '
        'style="border-collapse:collapse;font-family:Arial,sans-serif;font-size:14px;">'
        f'{"".join(html_rows)}</table>'
    )

    message = EmailMessage(policy=policy.SMTP)
    message["From"] = sender
    message["To"] = to
    message["Subject"] = f"{ADMIN_SUBJECT_PREFIX} {subject}"
    message.set_content("\n".join(text_lines), cte="quoted-printable")
    message.add_alternative(html_body, subtype="html", cte="quoted-printable")
    return message


async def send_admin_alert(
    subject: str,
    intro: str,
    rows: list[tuple[str, object]],
    *,
    tag: str = "alert",
) -> dict:
    """Generic internal notification. Returns the usual status dict; provider
    errors come back as sent=False rather than raising."""
    config = _admin_ready()
    if isinstance(config, dict):
        return config
    region, sender, to = config
    message = _admin_message(subject, intro, rows, sender=sender, to=to)
    return await _dispatch(message, region=region, sender=sender, to=to, rid=_ses_tag(tag))


def _run_rows(rid: str, run: dict) -> list[tuple[str, object]]:
    return [
        ("Yritys", _company_name(run)),
        ("Versio", _report_version(run)),
        ("Asiakas", _recipient(run)),
        ("Run id", rid),
        ("Raporttilinkki", _report_link(rid, run)),
    ]


def _customer_run(rid: str) -> tuple[dict, str] | dict:
    """A run with somebody waiting on it, or a sent=False reason.

    "Somebody" is a delivery address OR an access key. Gating on the address
    alone silenced every expert self-serve run whose user never typed an email —
    the /raportti address field is optional, so the most common real failure was
    also the one nobody heard about. Admin experiments and local pipeline
    debugging have neither and still stay out of the shared inbox; those fail all
    the time by design."""
    run = store.get_run(rid)
    if not run:
        return {"sent": False, "reason": "run-not-found"}
    if not _recipient(run) and not run.get("access_key"):
        return {"sent": False, "reason": "no-recipient"}
    return run, _company_name(run)


async def send_admin_report_held(rid: str, issues: list[str]) -> dict:
    """The run finished but failed its readiness checks, so the customer was
    deliberately NOT sent anything. Someone has to look at it."""
    found = _customer_run(rid)
    if isinstance(found, dict):
        return found
    run, company = found
    rows = _run_rows(rid, run)
    rows.insert(4, ("Ongelmat", "; ".join(issues) if issues else "—"))
    return await send_admin_alert(
        f"Raportti pidätetty — {company}",
        "Raportti ei läpäissyt laatutarkistuksia eikä sitä lähetetty — "
        "tarkista se ja lähetä asiakkaalle käsin.",
        rows,
        tag=rid,
    )


def _stage_failure_reason(run: dict) -> str | None:
    """Why this run failed, in the words of the stage that failed.

    The database always knows — `stage_results.error_message` — but the alert
    used to report only `Tila: error`, so every diagnosis started by opening the
    run. Reports the FIRST failed stage: with stop_on_failure the later ones are
    consequences of it."""
    for result in sorted(run.get("results") or [], key=lambda r: r.get("order", 0)):
        if result.get("status") not in ("error", "validation_failed"):
            continue
        detail = (result.get("error_message") or "").strip().replace("\n", " ")
        label = f"vaihe {result.get('order')} ({result.get('name') or '?'})"
        if result.get("status") == "validation_failed" and not detail:
            detail = "ei läpäissyt numero-/johdonmukaisuustarkistuksia"
        return f"{label}: {detail[:400] or 'ei virheilmoitusta'}"
    return None


async def send_admin_run_failed(rid: str, reason: str | None = None) -> dict:
    """The run died. A customer paid and is waiting for nothing.

    `reason` is for causes the run row cannot express — an abandoned run records
    its explanation on the stage row, which nobody reading the alert can see.
    When it is not given, the failing stage speaks for itself."""
    found = _customer_run(rid)
    if isinstance(found, dict):
        return found
    run, company = found
    rows = _run_rows(rid, run)
    rows.insert(4, ("Tila", run.get("status")))
    reason = reason or _stage_failure_reason(run)
    if reason:
        rows.insert(5, ("Syy", reason))
    return await send_admin_alert(
        f"Ajo epäonnistui — {company}",
        "Automaattinen generointi epäonnistui — tee raportti käsin ja lähetä "
        "se asiakkaalle.",
        rows,
        tag=rid,
    )


async def send_admin_delivery_failed(rid: str, result: dict) -> dict:
    """The report was fine; handing it to the customer is what broke."""
    found = _customer_run(rid)
    if isinstance(found, dict):
        return found
    run, company = found
    rows = _run_rows(rid, run)
    reason = (result or {}).get("reason") or "tuntematon"
    detail = (result or {}).get("detail") or (result or {}).get("code") or ""
    rows.insert(4, ("Syy", f"{reason} {detail}".strip()))
    return await send_admin_alert(
        f"Raportin lähetys epäonnistui — {company}",
        "Raportin lähetys asiakkaalle epäonnistui — lähetä se käsin.",
        rows,
        tag=rid,
    )


async def send_admin_delivery_notice(rid: str) -> dict:
    """Success FYI — no action needed. Off with ADMIN_NOTIFY_ON_SUCCESS=0 for
    anyone who only wants to hear about problems."""
    if not _truthy_env("ADMIN_NOTIFY_ON_SUCCESS", "1"):
        return {"sent": False, "reason": "disabled-on-success"}
    found = _customer_run(rid)
    if isinstance(found, dict):
        return found
    run, company = found
    return await send_admin_alert(
        f"Raportti toimitettu — {company}",
        "Raportti luotiin ja lähetettiin asiakkaalle. Ei toimenpiteitä.",
        _run_rows(rid, run),
        tag=rid,
    )


async def send_admin_order_intake(
    order_id: str,
    company: str,
    email: str,
    user_input: str | None = None,
) -> dict:
    """A website order that does not auto-generate (upload/Creditsafe/yhteydenotto)
    — until now it appeared only as a row in Tilaukset that nobody was told about."""
    return await send_admin_alert(
        f"Uusi tilaus odottaa käsittelyä — {company or 'tuntematon yritys'}",
        "Sivustolta saapui tilaus, joka ei generoidu automaattisesti. "
        "Käsittele se Tilaukset-näkymässä.",
        [
            ("Yritys", company),
            ("Asiakas", email),
            ("Tilaus id", order_id),
            ("Viesti", user_input),
        ],
        tag=_ses_tag(order_id, "order"),
    )
