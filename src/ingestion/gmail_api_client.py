"""Fetch emails for a user via the Gmail API using stored OAuth tokens.

Works as an alternative to ``imap_client`` — returns emails in the same dict
format so the rest of the digest pipeline can consume them unchanged.
"""

import base64
import logging
import re
from datetime import datetime, timedelta, timezone
from typing import List, Optional

from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build

from src.database import get_subscribed_sender_emails
from src.web.token_storage import get_user_tokens, get_user_id_by_email

logger = logging.getLogger(__name__)


class GmailAPIError(Exception):
    """Raised when we cannot fetch emails via the Gmail API."""
    pass


def _build_service(creds_data: dict):
    """Build an authenticated Gmail API service from a credentials dict."""
    creds = Credentials(
        token=creds_data["token"],
        refresh_token=creds_data.get("refresh_token"),
        token_uri=creds_data["token_uri"],
        client_id=creds_data["client_id"],
        client_secret=creds_data["client_secret"],
        scopes=creds_data.get("scopes"),
    )
    return build("gmail", "v1", credentials=creds)


def _parse_sender(from_header: str) -> tuple:
    """Extract (sender_email, sender_name) from a From header."""
    match = re.match(r"^(.*?)\s*<([^>]+)>", from_header)
    if match:
        name = match.group(1).strip().strip('"')
        email_addr = match.group(2).strip()
        return email_addr, name or email_addr
    return from_header.strip(), from_header.strip()


def _extract_body_parts(payload: dict) -> tuple:
    """Recursively extract (html_body, plain_body) from a Gmail message payload."""
    html_body = None
    plain_body = None

    mime = payload.get("mimeType", "")

    if mime == "text/plain" and payload.get("body", {}).get("data"):
        plain_body = base64.urlsafe_b64decode(payload["body"]["data"]).decode(
            "utf-8", errors="replace"
        )
    elif mime == "text/html" and payload.get("body", {}).get("data"):
        html_body = base64.urlsafe_b64decode(payload["body"]["data"]).decode(
            "utf-8", errors="replace"
        )

    for part in payload.get("parts", []):
        sub_html, sub_plain = _extract_body_parts(part)
        if sub_html and html_body is None:
            html_body = sub_html
        if sub_plain and plain_body is None:
            plain_body = sub_plain

    return html_body, plain_body


def fetch_emails_for_user(
    user_email: str,
    since_hours: int = 24,
) -> List[dict]:
    """Fetch recent emails for *user_email* using stored OAuth tokens.

    Only emails from senders the user has subscribed to are returned.  Each
    dict mirrors the format produced by ``imap_client.fetch_new_emails``:

        message_id, sender_email, sender_name, subject,
        received_at, html_body, plain_body
    """
    creds_data = get_user_tokens(user_email)
    if not creds_data:
        raise GmailAPIError(
            "No stored OAuth tokens for {}. "
            "Please connect via the web UI first.".format(user_email)
        )

    user_id = get_user_id_by_email(user_email)
    if user_id is None:
        raise GmailAPIError("User {} not found in database.".format(user_email))

    subscribed = get_subscribed_sender_emails(user_id=user_id)
    if not subscribed:
        logger.warning("No active subscriptions for %s", user_email)
        return []

    # Build Gmail API service
    service = _build_service(creds_data)

    # Gmail search query: emails newer than since_hours
    since_dt = datetime.now(timezone.utc) - timedelta(hours=since_hours)
    epoch_seconds = int(since_dt.timestamp())
    query = "after:{}".format(epoch_seconds)

    logger.info(
        "Fetching Gmail messages for %s (since %s)...",
        user_email,
        since_dt.strftime("%Y-%m-%d %H:%M UTC"),
    )

    # Paginate through message list
    all_msg_refs = []  # type: List[dict]
    page_token = None

    while True:
        kwargs = {
            "userId": "me",
            "q": query,
            "labelIds": ["INBOX"],
            "maxResults": 100,
        }
        if page_token:
            kwargs["pageToken"] = page_token

        results = service.users().messages().list(**kwargs).execute()
        all_msg_refs.extend(results.get("messages", []))
        page_token = results.get("nextPageToken")
        if not page_token:
            break

    logger.info("Found %d messages in inbox", len(all_msg_refs))

    # Fetch full messages and filter to subscribed senders
    emails = []  # type: List[dict]

    for msg_ref in all_msg_refs:
        msg = (
            service.users()
            .messages()
            .get(userId="me", id=msg_ref["id"], format="full")
            .execute()
        )

        headers = {h["name"]: h["value"] for h in msg["payload"].get("headers", [])}
        from_header = headers.get("From", "")
        sender_email_addr, sender_name = _parse_sender(from_header)

        # Skip if not subscribed
        if sender_email_addr.lower() not in subscribed:
            continue

        # Parse message ID
        message_id = headers.get("Message-ID", "").strip()
        if not message_id:
            message_id = "<gmail-{}>".format(msg_ref["id"])

        subject = headers.get("Subject", "(no subject)")

        # Parse date — prefer internalDate (millis since epoch) for accuracy
        internal_date_ms = msg.get("internalDate")
        if internal_date_ms:
            received_at = datetime.fromtimestamp(
                int(internal_date_ms) / 1000, tz=timezone.utc
            )
        else:
            received_at = datetime.now(timezone.utc)

        html_body, plain_body = _extract_body_parts(msg["payload"])

        emails.append({
            "message_id": message_id,
            "sender_email": sender_email_addr,
            "sender_name": sender_name,
            "subject": subject,
            "received_at": received_at,
            "html_body": html_body,
            "plain_body": plain_body,
        })

    logger.info(
        "Filtered to %d emails from subscribed senders", len(emails)
    )
    return emails
