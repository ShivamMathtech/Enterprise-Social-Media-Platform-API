from __future__ import annotations

import logging
import smtplib
from email.message import EmailMessage

from app.config import get_settings

settings = get_settings()
logger = logging.getLogger(__name__)


def send_email(recipient: str, subject: str, body: str) -> None:
    """Send through SMTP when configured; otherwise log in development/test."""
    if not settings.smtp_host:
        logger.info('EMAIL recipient=%s subject=%s body=%s', recipient, subject, body)
        return

    message = EmailMessage()
    message['From'] = settings.smtp_from_email
    message['To'] = recipient
    message['Subject'] = subject
    message.set_content(body)

    with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=15) as smtp:
        if settings.smtp_use_tls:
            smtp.starttls()
        if settings.smtp_username and settings.smtp_password:
            smtp.login(settings.smtp_username, settings.smtp_password)
        smtp.send_message(message)


def send_verification_email(email: str, token: str) -> None:
    url = f'{settings.frontend_url.rstrip("/")}/verify-email?token={token}'
    send_email(email, 'Verify your email', f'Open this one-time link to verify your account:\n\n{url}')


def send_password_reset_email(email: str, token: str) -> None:
    url = f'{settings.frontend_url.rstrip("/")}/reset-password?token={token}'
    send_email(email, 'Reset your password', f'Open this one-time link to reset your password:\n\n{url}')


def send_email_change_confirmation(email: str, token: str) -> None:
    url = f'{settings.frontend_url.rstrip("/")}/confirm-email-change?token={token}'
    send_email(email, 'Confirm your new email address', f'Open this one-time link to confirm your new address:\n\n{url}')


def send_invitation_email(email: str, organization_name: str, token: str) -> None:
    url = f'{settings.frontend_url.rstrip("/")}/accept-invitation?token={token}'
    send_email(email, f'Invitation to {organization_name}', f'You were invited to {organization_name}. Accept here:\n\n{url}')
