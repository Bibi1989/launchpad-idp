"""Transactional email helpers (Resend preferred, SMTP fallback, log fallback)."""

from __future__ import annotations

import html
import smtplib
from email.message import EmailMessage

import httpx

from app.core.config import Settings, get_settings
from app.core.logging import get_logger

logger = get_logger(__name__)


def _invite_html(
    *,
    title: str,
    eyebrow: str,
    body_lines: list[str],
    cta_label: str,
    invite_url: str,
) -> str:
    paragraphs = "".join(
        f'<p style="margin:0 0 12px;color:#a8b3c7;font-size:15px;line-height:1.55;">'
        f"{html.escape(line)}</p>"
        for line in body_lines
    )
    safe_url = html.escape(invite_url, quote=True)
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>{html.escape(title)}</title>
</head>
<body style="margin:0;padding:0;background:#0b1017;font-family:Inter,Segoe UI,Helvetica,Arial,sans-serif;">
  <table role="presentation" width="100%" cellspacing="0" cellpadding="0" style="background:#0b1017;padding:32px 16px;">
    <tr>
      <td align="center">
        <table role="presentation" width="100%" cellspacing="0" cellpadding="0" style="max-width:560px;background:#121a24;border:1px solid #243041;border-radius:16px;overflow:hidden;">
          <tr>
            <td style="padding:28px 28px 8px;">
              <p style="margin:0 0 8px;font-size:11px;letter-spacing:0.16em;text-transform:uppercase;color:#2dd4bf;font-weight:600;">
                {html.escape(eyebrow)}
              </p>
              <h1 style="margin:0 0 16px;font-size:24px;line-height:1.25;color:#f3f6fb;font-weight:650;">
                {html.escape(title)}
              </h1>
              {paragraphs}
              <p style="margin:24px 0 8px;">
                <a href="{safe_url}" style="display:inline-block;background:#2dd4bf;color:#041016;text-decoration:none;font-weight:700;font-size:14px;padding:12px 20px;border-radius:10px;">
                  {html.escape(cta_label)}
                </a>
              </p>
              <p style="margin:16px 0 0;color:#6b778c;font-size:12px;line-height:1.5;word-break:break-all;">
                Or open this link:<br />{html.escape(invite_url)}
              </p>
            </td>
          </tr>
          <tr>
            <td style="padding:16px 28px 24px;border-top:1px solid #243041;">
              <p style="margin:0;color:#6b778c;font-size:12px;line-height:1.5;">
                Launchpad IDP · If you did not expect this invitation, you can ignore this email.
              </p>
            </td>
          </tr>
        </table>
      </td>
    </tr>
  </table>
</body>
</html>
"""


class EmailService:
    def __init__(self, settings: Settings | None = None) -> None:
        self._settings = settings or get_settings()

    @property
    def configured(self) -> bool:
        return self.resend_configured or self.smtp_configured

    @property
    def resend_configured(self) -> bool:
        return bool((self._settings.resend_api_key or "").strip())

    @property
    def smtp_configured(self) -> bool:
        return bool(
            (self._settings.smtp_host or "").strip()
            and (self._settings.smtp_from or "").strip()
        )

    def send_org_invite(
        self,
        *,
        to_email: str,
        org_name: str,
        role: str,
        invite_url: str,
        invited_by: str,
    ) -> tuple[bool, str | None]:
        subject = f"You're invited to {org_name} on Launchpad"
        text = (
            f"{invited_by} invited you to join {org_name} as {role}.\n\n"
            f"Accept the invitation:\n{invite_url}\n\n"
            "If you did not expect this email, you can ignore it.\n"
        )
        html_body = _invite_html(
            title=f"Join {org_name}",
            eyebrow="Organization invite",
            body_lines=[
                f"{invited_by} invited you to join {org_name} on Launchpad.",
                f"Your role will be {role}.",
                "Accept to open the organization and start collaborating.",
            ],
            cta_label="Accept invitation",
            invite_url=invite_url,
        )
        return self._send(
            to_email=to_email,
            subject=subject,
            body=text,
            html_body=html_body,
        )

    def send_project_invite(
        self,
        *,
        to_email: str,
        org_name: str,
        project_name: str,
        role: str,
        invite_url: str,
        invited_by: str,
    ) -> tuple[bool, str | None]:
        subject = f"You're invited to {project_name} on Launchpad"
        text = (
            f"{invited_by} invited you to the project {project_name} "
            f"in {org_name} as {role}.\n\n"
            f"Accept the invitation:\n{invite_url}\n\n"
            "If you did not expect this email, you can ignore it.\n"
        )
        html_body = _invite_html(
            title=f"Join project {project_name}",
            eyebrow="Project invite",
            body_lines=[
                f"{invited_by} invited you to {project_name} in {org_name}.",
                f"Your project role will be {role}.",
                "Accept to open the project and its workspaces.",
            ],
            cta_label="Accept project invite",
            invite_url=invite_url,
        )
        return self._send(
            to_email=to_email,
            subject=subject,
            body=text,
            html_body=html_body,
        )

    def _send(
        self,
        *,
        to_email: str,
        subject: str,
        body: str,
        html_body: str | None = None,
    ) -> tuple[bool, str | None]:
        if self.resend_configured:
            return self._send_resend(
                to_email=to_email,
                subject=subject,
                body=body,
                html_body=html_body,
            )
        if self.smtp_configured:
            return self._send_smtp(
                to_email=to_email,
                subject=subject,
                body=body,
                html_body=html_body,
            )
        logger.info(
            "email_skipped_unconfigured",
            to_email=to_email,
            subject=subject,
            body_preview=body[:200],
        )
        return False, "Email not configured (set RESEND_API_KEY + RESEND_FROM_EMAIL or SMTP_*)"

    def _from_address(self) -> str:
        return (
            (self._settings.resend_from or "").strip()
            or (self._settings.smtp_from or "").strip()
            or "Launchpad <onboarding@resend.dev>"
        )

    def _send_resend(
        self,
        *,
        to_email: str,
        subject: str,
        body: str,
        html_body: str | None,
    ) -> tuple[bool, str | None]:
        api_key = (self._settings.resend_api_key or "").strip()
        payload: dict[str, object] = {
            "from": self._from_address(),
            "to": [to_email],
            "subject": subject,
            "text": body,
        }
        if html_body:
            payload["html"] = html_body
        try:
            response = httpx.post(
                "https://api.resend.com/emails",
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                },
                json=payload,
                timeout=20.0,
            )
        except httpx.HTTPError as exc:
            logger.warning("email_resend_failed", to_email=to_email, error=str(exc))
            return False, f"Resend request failed: {exc}"
        if response.status_code >= 400:
            detail = (response.text or "")[:300]
            logger.warning(
                "email_resend_rejected",
                to_email=to_email,
                status_code=response.status_code,
                body=detail,
            )
            return False, f"Resend rejected ({response.status_code}): {detail}"
        logger.info("email_sent_resend", to_email=to_email, subject=subject)
        return True, None

    def _send_smtp(
        self,
        *,
        to_email: str,
        subject: str,
        body: str,
        html_body: str | None,
    ) -> tuple[bool, str | None]:
        message = EmailMessage()
        message["Subject"] = subject
        message["From"] = self._settings.smtp_from or self._from_address()
        message["To"] = to_email
        message.set_content(body)
        if html_body:
            message.add_alternative(html_body, subtype="html")

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
            return False, f"SMTP send failed: {exc}"

        logger.info("email_sent_smtp", to_email=to_email, subject=subject)
        return True, None

    def _login(self, smtp: smtplib.SMTP) -> None:
        user = (self._settings.smtp_username or "").strip()
        password = self._settings.smtp_password or ""
        if user:
            smtp.login(user, password)
