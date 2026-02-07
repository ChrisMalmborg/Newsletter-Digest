import email
import email.message
import imaplib
import logging
from datetime import datetime, timedelta, timezone
from email.header import decode_header
from email.utils import parseaddr, parsedate_to_datetime
from typing import Optional

from ..config import IMAP_HOST, IMAP_PORT, IMAP_USERNAME, IMAP_PASSWORD

logger = logging.getLogger(__name__)


class IMAPError(Exception):
    """Base exception for IMAP operations."""
    pass


class IMAPAuthError(IMAPError):
    """Authentication failed."""
    pass


class IMAPConnectionError(IMAPError):
    """Could not connect to IMAP server."""
    pass


def connect_to_inbox() -> imaplib.IMAP4_SSL:
    """Connect to the IMAP server and select the inbox.

    Returns an authenticated IMAP connection with INBOX selected.
    Raises IMAPConnectionError or IMAPAuthError on failure.
    """
    missing = []
    if not IMAP_HOST:
        missing.append("IMAP_HOST")
    if not IMAP_USERNAME:
        missing.append("IMAP_USERNAME")
    if not IMAP_PASSWORD:
        missing.append("IMAP_PASSWORD")
    if missing:
        raise IMAPError(f"Missing config: {', '.join(missing)}")

    try:
        conn = imaplib.IMAP4_SSL(IMAP_HOST, IMAP_PORT)
    except (OSError, imaplib.IMAP4.error) as e:
        raise IMAPConnectionError(f"Failed to connect to {IMAP_HOST}:{IMAP_PORT}: {e}") from e

    try:
        conn.login(IMAP_USERNAME, IMAP_PASSWORD)
    except imaplib.IMAP4.error as e:
        conn.logout()
        raise IMAPAuthError(f"Authentication failed for {IMAP_USERNAME}: {e}") from e

    conn.select("INBOX", readonly=True)
    return conn


def fetch_new_emails(since_hours: int = 24) -> list[dict]:
    """Fetch emails from the last N hours.

    Returns a list of dicts with keys:
        message_id, sender_email, sender_name, subject,
        received_at, html_body, plain_body
    """
    conn = connect_to_inbox()
    try:
        return _fetch_emails(conn, since_hours)
    finally:
        try:
            conn.close()
            conn.logout()
        except Exception:
            pass


def _fetch_emails(conn: imaplib.IMAP4_SSL, since_hours: int) -> list[dict]:
    """Internal: search and parse emails from the connection."""
    since_date = datetime.now(timezone.utc) - timedelta(hours=since_hours)
    # IMAP SINCE uses date only (no time), format: DD-Mon-YYYY
    date_str = since_date.strftime("%d-%b-%Y")

    status, data = conn.search(None, f'(SINCE "{date_str}")')
    if status != "OK":
        logger.error("IMAP search failed: %s", status)
        return []

    message_ids = data[0].split()
    if not message_ids:
        return []

    logger.info("Found %d emails since %s", len(message_ids), date_str)
    emails = []

    for msg_id in message_ids:
        try:
            parsed = _fetch_single_email(conn, msg_id)
            if parsed:
                # Filter by actual timestamp since IMAP SINCE is date-granular
                if parsed["received_at"] >= since_date:
                    emails.append(parsed)
        except Exception as e:
            logger.warning("Failed to parse email %s: %s", msg_id, e)
            continue

    return emails


def _fetch_single_email(conn: imaplib.IMAP4_SSL, msg_id: bytes) -> Optional[dict]:
    """Fetch and parse a single email. Uses PEEK to avoid marking as read."""
    status, data = conn.fetch(msg_id, "(BODY.PEEK[])")
    if status != "OK" or not data or not data[0]:
        return None

    raw_bytes = data[0][1]
    msg = email.message_from_bytes(raw_bytes)

    message_id = msg.get("Message-ID", "").strip()
    if not message_id:
        # Generate a fallback ID from date + subject
        message_id = f"<no-id-{msg_id.decode()}@fallback>"

    sender_name, sender_email = parseaddr(msg.get("From", ""))
    sender_name = _decode_header_value(sender_name) or sender_email
    subject = _decode_header_value(msg.get("Subject", "")) or "(no subject)"

    received_at = _parse_date(msg)

    html_body, plain_body = _extract_bodies(msg)

    return {
        "message_id": message_id,
        "sender_email": sender_email,
        "sender_name": sender_name,
        "subject": subject,
        "received_at": received_at,
        "html_body": html_body,
        "plain_body": plain_body,
    }


def _decode_header_value(value: Optional[str]) -> str:
    """Decode RFC 2047 encoded header values."""
    if not value:
        return ""
    parts = decode_header(value)
    decoded = []
    for part, charset in parts:
        if isinstance(part, bytes):
            decoded.append(part.decode(charset or "utf-8", errors="replace"))
        else:
            decoded.append(part)
    return "".join(decoded)


def _parse_date(msg: email.message.Message) -> datetime:
    """Extract and parse the date from an email message."""
    date_str = msg.get("Date")
    if date_str:
        try:
            dt = parsedate_to_datetime(date_str)
            # Ensure timezone-aware (assume UTC if naive)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt
        except Exception:
            pass

    # Fallback: current time
    return datetime.now(timezone.utc)


def _extract_bodies(msg: email.message.Message) -> tuple[Optional[str], Optional[str]]:
    """Extract HTML and plain text bodies from an email."""
    html_body = None
    plain_body = None

    if msg.is_multipart():
        for part in msg.walk():
            content_type = part.get_content_type()
            disposition = str(part.get("Content-Disposition", ""))

            # Skip attachments
            if "attachment" in disposition:
                continue

            try:
                payload = part.get_payload(decode=True)
                if payload is None:
                    continue
                charset = part.get_content_charset() or "utf-8"
                text = payload.decode(charset, errors="replace")
            except Exception:
                continue

            if content_type == "text/html" and html_body is None:
                html_body = text
            elif content_type == "text/plain" and plain_body is None:
                plain_body = text
    else:
        try:
            payload = msg.get_payload(decode=True)
            if payload:
                charset = msg.get_content_charset() or "utf-8"
                text = payload.decode(charset, errors="replace")
                if msg.get_content_type() == "text/html":
                    html_body = text
                else:
                    plain_body = text
        except Exception:
            pass

    return html_body, plain_body
