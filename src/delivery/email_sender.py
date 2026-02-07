"""Send digest emails via SMTP."""

import logging
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

from ..config import SMTP_HOST, SMTP_PORT, SMTP_USERNAME, SMTP_PASSWORD

logger = logging.getLogger(__name__)


def send_digest(html: str, text: str, subject: str, to_address: str) -> bool:
    """Send a digest email via SMTP with HTML and plain-text parts.

    Returns True on success, False on failure.
    """
    if not all([SMTP_HOST, SMTP_USERNAME, SMTP_PASSWORD]):
        logger.error("SMTP not configured. Set SMTP_HOST, SMTP_USERNAME, SMTP_PASSWORD in .env")
        return False

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = SMTP_USERNAME
    msg["To"] = to_address

    # Attach plain text first, then HTML (email clients prefer the last part)
    msg.attach(MIMEText(text, "plain", "utf-8"))
    msg.attach(MIMEText(html, "html", "utf-8"))

    try:
        with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as server:
            server.ehlo()
            server.starttls()
            server.ehlo()
            server.login(SMTP_USERNAME, SMTP_PASSWORD)
            server.sendmail(SMTP_USERNAME, to_address, msg.as_string())
        logger.info("Digest sent to %s", to_address)
        return True
    except smtplib.SMTPAuthenticationError as e:
        logger.error("SMTP authentication failed: %s", e)
        return False
    except smtplib.SMTPException as e:
        logger.error("SMTP error: %s", e)
        return False
    except OSError as e:
        logger.error("Connection error: %s", e)
        return False
