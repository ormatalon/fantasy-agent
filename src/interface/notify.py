"""Email notifier.

Gmail SMTP with an app password. Note this is deliberately NOT the Gmail
MCP connector (PLAN.md §2): that one is read/draft-only and cannot send.

App passwords are 16 characters (Google shows them as four groups of four).
Spaces are stripped here because Google displays them grouped and they get
copied that way more often than not.
"""

import smtplib
import sys
from email.message import EmailMessage

import config

SMTP_HOST = "smtp.gmail.com"
SMTP_PORT = 465


class NotifyError(RuntimeError):
    pass


def _credentials() -> tuple[str, str]:
    address = (config.GMAIL_ADDRESS or "").strip()
    password = (config.GMAIL_APP_PASSWORD or "").replace(" ", "").strip()
    if not address or not password:
        raise NotifyError("Set GMAIL_ADDRESS and GMAIL_APP_PASSWORD in .env (see .env.example).")
    if len(password) != 16:
        raise NotifyError(
            f"GMAIL_APP_PASSWORD looks wrong: expected a 16-character app password, got {len(password)}. "
            "A regular account password will not work - generate an app password at "
            "https://myaccount.google.com/apppasswords"
        )
    return address, password


def notify(subject: str, body: str, to: str | None = None) -> None:
    """Send a plain-text email. Raises NotifyError with an actionable message
    rather than leaking smtplib's exception types to callers."""
    address, password = _credentials()
    recipient = to or address

    message = EmailMessage()
    message["Subject"] = subject
    message["From"] = address
    message["To"] = recipient
    message.set_content(body)

    try:
        with smtplib.SMTP_SSL(SMTP_HOST, SMTP_PORT, timeout=30) as smtp:
            smtp.login(address, password)
            smtp.send_message(message)
    except smtplib.SMTPAuthenticationError as e:
        raise NotifyError(
            "Gmail rejected the login. Check that GMAIL_APP_PASSWORD is an app password "
            f"(not your account password) and that 2-Step Verification is on. ({e.smtp_code})"
        ) from e
    except (smtplib.SMTPException, OSError) as e:
        raise NotifyError(f"Could not send mail: {e}") from e

    print(f"Emailed '{subject}' to {recipient}.", file=sys.stderr)
