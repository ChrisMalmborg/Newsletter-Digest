"""Send digest emails via SMTP or Gmail API."""

import base64
import logging
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build

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


def send_digest_gmail_api(
    user_email: str,
    to_address: str,
    subject: str,
    html_content: str,
    text_content: str,
) -> bool:
    """Send a digest email via the Gmail API using stored OAuth credentials.

    Requires the ``gmail.send`` scope. Returns True on success, False on failure.
    """
    from ..web.token_storage import get_user_tokens

    creds_data = get_user_tokens(user_email)
    if not creds_data:
        logger.error("No stored OAuth tokens for %s", user_email)
        return False

    creds = Credentials(
        token=creds_data["token"],
        refresh_token=creds_data.get("refresh_token"),
        token_uri=creds_data["token_uri"],
        client_id=creds_data["client_id"],
        client_secret=creds_data["client_secret"],
        scopes=creds_data.get("scopes"),
    )

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = user_email
    msg["To"] = to_address
    msg.attach(MIMEText(text_content, "plain", "utf-8"))
    msg.attach(MIMEText(html_content, "html", "utf-8"))

    raw_message = base64.urlsafe_b64encode(msg.as_bytes()).decode("ascii")

    try:
        service = build("gmail", "v1", credentials=creds)
        service.users().messages().send(
            userId="me",
            body={"raw": raw_message},
        ).execute()
        logger.info("Digest sent via Gmail API to %s", to_address)
        return True
    except Exception as e:
        logger.error("Gmail API send failed: %s", e)
        return False
