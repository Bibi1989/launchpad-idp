"""Transactional email helpers (SMTP with structured-log fallback)."""

from __future__ import annotations

import smtplib
from email.message import EmailMessage

from app.core.config import Settings, get_settings
from app.core.logging import get_logger

logger = get_logger(__name__)


class EmailService:
    def __init__(self, settings: Settings | None = None) -> None:
        self._settings = settings or get_settings()

    @property
    def configured(self) -> bool:
        return bool((self._settings.smtp_host or "").strip() and (self._settings.smtp_from or "").strip())

    def send_org_invite(
        self,
        *,
        to_email: str,
        org_name: str,
        role: str,
        invite_url: str,
        invited_by: str,
    ) -> bool:
        subject = f"You're invited to {org_name} on Launchpad"
        body = (
            f"{invited_by} invited you to join {org_name} as {role}.\n\n"
            f"Accept the invitation:\n{invite_url}\n\n"
            "If you did not expect this email, you can ignore it.\n"
        )
        return self._send(to_email=to_email, subject=subject, body=body)

    def _send(self, *, to_email: str, subject: str, body: str) -> bool:
        if not self.configured:
            logger.info(
                "email_skipped_smtp_unconfigured",
                to_email=to_email,
                subject=subject,
                body_preview=body[:200],
            )
            return False

        message = EmailMessage()
        message["Subject"] = subject
        message["From"] = self._settings.smtp_from
        message["To"] = to_email
        message.set_content(body)

        host = self._settings.smtp_host or ""
        port = self._settings.smtp_port
        try:
            if self._settings.smtp_use_tls:
                with smtplib.SMTP(host, port, timeout=20) as smtp:
                    smtp.starttls()
                    self._login(smtp)
                    smtp.send_message(message)
            else:
                with smtplib.SMTP(host, port, timeout=20) as smtp:
                    self._login(smtp)
                    smtp.send_message(message)
        except OSError as exc:
            logger.warning("email_send_failed", to_email=to_email, error=str(exc))
            return False

        logger.info("email_sent", to_email=to_email, subject=subject)
        return True

    def _login(self, smtp: smtplib.SMTP) -> None:
        user = (self._settings.smtp_username or "").strip()
        password = self._settings.smtp_password or ""
        if user:
            smtp.login(user, password)
