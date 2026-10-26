"""Optional SMTP email adapter. Never reports delivery unless SMTP accepted the message."""
from email.message import EmailMessage
import smtplib
import ssl

from .config import Config


def configured(config=Config) -> bool:
    return bool(config.SMTP_HOST and config.EMAIL_FROM)


def send_email(to: str, subject: str, text: str, config=Config) -> bool:
    if not configured(config):
        return False
    msg = EmailMessage()
    msg["From"] = config.EMAIL_FROM
    msg["To"] = to
    msg["Subject"] = subject
    msg.set_content(text)
    with smtplib.SMTP(config.SMTP_HOST, config.SMTP_PORT, timeout=12) as server:
        if config.SMTP_STARTTLS:
            server.starttls(context=ssl.create_default_context())
        if config.SMTP_USERNAME:
            server.login(config.SMTP_USERNAME, config.SMTP_PASSWORD)
        server.send_message(msg)
    return True
