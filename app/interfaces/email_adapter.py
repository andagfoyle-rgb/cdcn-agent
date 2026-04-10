"""
Outbound-only email adapter for sending notifications to board members.

Unlike Discord/Telegram, this adapter doesn't receive messages — it's used
by the scheduler to send digests, governance reminders, and deadline alerts.

Requires aiosmtplib.  If SMTP settings are not configured the adapter
logs a warning and returns False for every send attempt.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import Any

from app.config import settings

log = logging.getLogger(__name__)

# Module-level singleton — set by main.py at startup.
_instance: EmailAdapter | None = None


def get_email_adapter() -> EmailAdapter | None:
    """Return the email adapter singleton (None if not yet initialised)."""
    return _instance

# ── HTML template ─────────────────────────────────────────────────────────────

_HTML_WRAPPER = """\
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<style>
  @import url('https://fonts.googleapis.com/css2?family=Rubik:wght@400;600&display=swap');
  body {{
    margin: 0; padding: 0;
    font-family: Rubik, Calibri, 'Segoe UI', Arial, sans-serif;
    color: #333; background: #f4f4f4;
  }}
  .header {{
    background: #2d2d2d; color: #ffffff; padding: 20px 28px;
    font-size: 18px; font-weight: 600;
  }}
  .header span {{ color: #8cb4d5; }}
  .content {{
    background: #ffffff; padding: 24px 28px;
    line-height: 1.6; font-size: 15px;
  }}
  .section {{ margin-bottom: 24px; }}
  .section h2 {{
    font-size: 16px; font-weight: 600;
    border-bottom: 2px solid #8cb4d5; padding-bottom: 4px;
    margin: 0 0 10px 0;
  }}
  .footer {{
    padding: 16px 28px; font-size: 12px;
    color: #888; text-align: center;
  }}
  .alert {{ color: #c0392b; font-weight: 600; }}
</style>
</head>
<body>
<div class="header"><span>CDCN</span> Agent</div>
<div class="content">{body}</div>
<div class="footer">
  Sent by CDCN Agent &middot; {timestamp}
</div>
</body>
</html>"""


# ── Adapter class ─────────────────────────────────────────────────────────────


class EmailAdapter:
    """
    Outbound-only email adapter for sending notifications to board members.

    Unlike Discord/Telegram, this adapter doesn't receive messages — it's used
    by the scheduler to send digests, governance reminders, and deadline alerts.
    """

    name = "email"

    def __init__(self) -> None:
        self._configured: bool = bool(
            settings.smtp_host
            and settings.smtp_username
            and settings.smtp_password
            and settings.smtp_from_address
        )
        if not self._configured:
            log.warning(
                "SMTP settings incomplete — email adapter disabled.  "
                "Set SMTP_HOST, SMTP_USERNAME, SMTP_PASSWORD, and "
                "SMTP_FROM_ADDRESS to enable."
            )

    # ── Helpers ────────────────────────────────────────────────────────────

    @property
    def recipients(self) -> list[str]:
        """Parse comma-separated EMAIL_RECIPIENTS into a list."""
        raw = settings.email_recipients
        if not raw:
            return []
        return [r.strip() for r in raw.split(",") if r.strip()]

    def _build_html(self, body_html: str) -> str:
        ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
        return _HTML_WRAPPER.format(body=body_html, timestamp=ts)

    def _build_message(
        self,
        subject: str,
        body_html: str,
        body_text: str,
        recipients: list[str] | None = None,
    ) -> MIMEMultipart:
        msg = MIMEMultipart("alternative")
        msg["Subject"] = subject
        msg["From"] = settings.smtp_from_address
        to_list = recipients or self.recipients
        msg["To"] = ", ".join(to_list)

        msg.attach(MIMEText(body_text, "plain", "utf-8"))
        msg.attach(MIMEText(self._build_html(body_html), "html", "utf-8"))
        return msg

    async def _audit(self, action: str, detail: str) -> None:
        """Write a row to the audit_log table."""
        try:
            from app.storage.audit_log import log_event

            await log_event(
                actor="email_adapter",
                action=action,
                target=", ".join(self.recipients),
                detail=detail,
            )
        except Exception as exc:
            log.warning("Failed to write audit log for email send: %s", exc)

    # ── Core send ──────────────────────────────────────────────────────────

    async def _smtp_send(self, msg: MIMEMultipart) -> bool:
        """Open an async SMTP connection and send *msg*.  Returns True on success."""
        import aiosmtplib

        try:
            await aiosmtplib.send(
                msg,
                hostname=settings.smtp_host,
                port=settings.smtp_port,
                username=settings.smtp_username,
                password=settings.smtp_password,
                start_tls=True,
            )
            return True
        except Exception as exc:
            log.error("SMTP send failed: %s", exc, exc_info=True)
            return False

    # ── Public methods ─────────────────────────────────────────────────────

    async def send_notification(
        self,
        subject: str,
        body_html: str,
        body_text: str,
    ) -> bool:
        """
        Send an arbitrary notification email.

        Returns True if the message was handed off to the SMTP server,
        False otherwise (config missing, no recipients, SMTP error).
        """
        if not self._configured:
            log.warning("send_notification called but SMTP is not configured.")
            return False

        to_list = self.recipients
        if not to_list:
            log.warning("send_notification called but EMAIL_RECIPIENTS is empty.")
            return False

        msg = self._build_message(subject, body_html, body_text, to_list)
        ok = await self._smtp_send(msg)

        await self._audit(
            "email_notification",
            f"subject={subject!r} ok={ok}",
        )
        if ok:
            log.info("Email sent: %r -> %s", subject, to_list)
        return ok

    async def send_digest(
        self,
        subject: str,
        sections: list[dict[str, Any]],
    ) -> bool:
        """
        Send a structured digest email.

        Each item in *sections* is a dict with keys:
          - title (str): section heading
          - body  (str): HTML content for the section
          - text  (str, optional): plain-text fallback for the section

        Returns True on success.
        """
        if not self._configured:
            log.warning("send_digest called but SMTP is not configured.")
            return False

        to_list = self.recipients
        if not to_list:
            log.warning("send_digest called but EMAIL_RECIPIENTS is empty.")
            return False

        # Build combined HTML
        html_parts: list[str] = []
        text_parts: list[str] = []
        for sec in sections:
            title = sec.get("title", "Untitled")
            body = sec.get("body", "")
            text = sec.get("text", title)
            html_parts.append(
                f'<div class="section"><h2>{title}</h2>{body}</div>'
            )
            text_parts.append(f"## {title}\n{text}")

        body_html = "\n".join(html_parts)
        body_text = "\n\n".join(text_parts)

        msg = self._build_message(subject, body_html, body_text, to_list)
        ok = await self._smtp_send(msg)

        section_titles = [s.get("title", "?") for s in sections]
        await self._audit(
            "email_digest",
            f"subject={subject!r} sections={section_titles!r} ok={ok}",
        )
        if ok:
            log.info("Digest email sent: %r (%d sections) -> %s", subject, len(sections), to_list)
        return ok

    async def send_deadline_alert(
        self,
        deadline_info: dict[str, Any],
    ) -> bool:
        """
        Send an urgent deadline-alert email.

        *deadline_info* should contain:
          - title (str):    short description of the deadline
          - date  (str):    deadline date (human-readable)
          - detail (str):   longer explanation / action items
          - url   (str, optional): link to the opportunity or filing

        Returns True on success.
        """
        if not self._configured:
            log.warning("send_deadline_alert called but SMTP is not configured.")
            return False

        to_list = self.recipients
        if not to_list:
            log.warning("send_deadline_alert called but EMAIL_RECIPIENTS is empty.")
            return False

        title = deadline_info.get("title", "Upcoming deadline")
        date = deadline_info.get("date", "TBD")
        detail = deadline_info.get("detail", "")
        url = deadline_info.get("url", "")

        subject = f"[CDCN Deadline] {title} — due {date}"

        link_html = f'<p><a href="{url}">{url}</a></p>' if url else ""
        link_text = f"\nLink: {url}" if url else ""

        body_html = (
            f'<div class="section">'
            f'<h2 class="alert">Deadline Alert: {title}</h2>'
            f"<p><strong>Due:</strong> {date}</p>"
            f"<p>{detail}</p>"
            f"{link_html}"
            f"</div>"
        )
        body_text = (
            f"DEADLINE ALERT: {title}\n"
            f"Due: {date}\n\n"
            f"{detail}"
            f"{link_text}"
        )

        msg = self._build_message(subject, body_html, body_text, to_list)
        ok = await self._smtp_send(msg)

        await self._audit(
            "email_deadline_alert",
            f"title={title!r} date={date!r} ok={ok}",
        )
        if ok:
            log.info("Deadline alert sent: %r (due %s) -> %s", title, date, to_list)
        return ok
