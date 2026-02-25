import base64
import re
from html import unescape
from typing import List, Dict, Optional

from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import Flow
from googleapiclient.discovery import build

from src.config import GOOGLE_CLIENT_ID, GOOGLE_CLIENT_SECRET, GOOGLE_REDIRECT_URI

SCOPES = [
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/gmail.send",
]


def _build_flow() -> Flow:
    """Create an OAuth flow using client credentials from config."""
    client_config = {
        "web": {
            "client_id": GOOGLE_CLIENT_ID,
            "client_secret": GOOGLE_CLIENT_SECRET,
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
            "redirect_uris": [GOOGLE_REDIRECT_URI],
        }
    }
    flow = Flow.from_client_config(client_config, scopes=SCOPES)
    flow.redirect_uri = GOOGLE_REDIRECT_URI
    return flow


def get_authorization_url() -> str:
    """Return the Google OAuth consent screen URL."""
    flow = _build_flow()
    auth_url, _ = flow.authorization_url(
        access_type="offline",
        include_granted_scopes="true",
        prompt="consent",
    )
    return auth_url


def exchange_code(code: str) -> dict:
    """Exchange an authorization code for credentials. Returns serialized creds."""
    flow = _build_flow()
    flow.fetch_token(code=code)
    creds = flow.credentials
    return {
        "token": creds.token,
        "refresh_token": creds.refresh_token,
        "token_uri": creds.token_uri,
        "client_id": creds.client_id,
        "client_secret": creds.client_secret,
        "scopes": list(creds.scopes or []),
    }


def _get_credentials(creds_data: dict) -> Credentials:
    """Reconstruct Credentials from a serialized dict."""
    return Credentials(
        token=creds_data["token"],
        refresh_token=creds_data.get("refresh_token"),
        token_uri=creds_data["token_uri"],
        client_id=creds_data["client_id"],
        client_secret=creds_data["client_secret"],
        scopes=creds_data.get("scopes"),
    )


def get_user_email(creds_data: dict) -> str:
    """Return the authenticated user's email address."""
    creds = _get_credentials(creds_data)
    service = build("gmail", "v1", credentials=creds)
    profile = service.users().getProfile(userId="me").execute()
    return profile["emailAddress"]


def _strip_html(text: str) -> str:
    """Remove HTML tags and decode entities, returning clean text."""
    clean = re.sub(r"<[^>]+>", " ", text)
    clean = unescape(clean)
    clean = re.sub(r"\s+", " ", clean).strip()
    return clean


def _parse_sender(from_header: str) -> tuple[str, str]:
    """Extract (sender_email, sender_name) from a From header like 'Name <email>'."""
    match = re.match(r"^(.*?)\s*<([^>]+)>", from_header)
    if match:
        name = match.group(1).strip().strip('"')
        email = match.group(2).strip()
        return email, name or email
    # Bare email address
    return from_header.strip(), from_header.strip()


def fetch_recent_emails(creds_data: dict, max_results: int = 50) -> List[Dict]:
    """Fetch recent emails from the user's inbox via the Gmail API."""
    creds = _get_credentials(creds_data)
    service = build("gmail", "v1", credentials=creds)

    results = (
        service.users()
        .messages()
        .list(userId="me", labelIds=["INBOX"], maxResults=max_results)
        .execute()
    )

    messages = results.get("messages", [])
    emails = []

    for msg_ref in messages:
        msg = (
            service.users()
            .messages()
            .get(userId="me", id=msg_ref["id"], format="full")
            .execute()
        )

        headers = {h["name"]: h["value"] for h in msg["payload"].get("headers", [])}
        body_text = _extract_body(msg["payload"])

        emails.append(
            {
                "id": msg_ref["id"],
                "subject": headers.get("Subject", "(no subject)"),
                "from": headers.get("From", ""),
                "date": headers.get("Date", ""),
                "list_unsubscribe": headers.get("List-Unsubscribe", ""),
                "body_snippet": body_text[:500] if body_text else "",
                "body_full": body_text or "",
            }
        )

    return emails


def _extract_body(payload: dict) -> Optional[str]:
    """Recursively extract the plain-text body from a Gmail message payload."""
    if payload.get("mimeType") == "text/plain" and payload.get("body", {}).get("data"):
        return base64.urlsafe_b64decode(payload["body"]["data"]).decode("utf-8", errors="replace")

    for part in payload.get("parts", []):
        text = _extract_body(part)
        if text:
            return text

    # Fall back to HTML if no plain text found
    if payload.get("mimeType") == "text/html" and payload.get("body", {}).get("data"):
        return base64.urlsafe_b64decode(payload["body"]["data"]).decode("utf-8", errors="replace")

    for part in payload.get("parts", []):
        if part.get("mimeType") == "text/html" and part.get("body", {}).get("data"):
            return base64.urlsafe_b64decode(part["body"]["data"]).decode("utf-8", errors="replace")

    return None


_TRANSACTIONAL_SUBJECT_PATTERNS = re.compile(
    r"(welcome\s+to|verify\s+your|confirm\s+your|reset\s+your\s+password"
    r"|order\s+(confirm|receip|ship)|payment\s+(confirm|receip)"
    r"|your\s+receipt|invoice\s+#|sign[\s-]?in\s+(attempt|alert)"
    r"|account\s+(created|activated|security)|login\s+(alert|notification)"
    r"|two[\s-]?factor|one[\s-]?time\s+(code|password)|verification\s+code)",
    re.IGNORECASE,
)

# Known newsletter platforms — emails from these domains are always treated as
# potential newsletters and never excluded by the transactional-sender filter.
NEWSLETTER_DOMAINS = {
    "news.bloomberg.com",
    "substack.com",
    "beehiiv.com",
    "mailchimp.com",
    "convertkit.com",
    "buttondown.email",
    "revue.email",
    "ghost.io",
}

_TRANSACTIONAL_SENDER_PATTERNS = re.compile(
    r"(support@|billing@|receipts?@|orders?@"
    r"|notifications?@|security@|verify@|mailer-daemon)",
    re.IGNORECASE,
)


def _sender_domain(sender_email: str) -> str:
    """Extract the domain portion from an email address."""
    if "@" in sender_email:
        return sender_email.split("@", 1)[1].lower()
    return ""


def _is_transactional(email: Dict) -> bool:
    """Return True if the email looks like a transactional / one-off message."""
    subject = email.get("subject", "")
    sender = email.get("from", "")
    sender_email_addr, _ = _parse_sender(sender)

    # Never exclude emails from known newsletter platforms
    if _sender_domain(sender_email_addr) in NEWSLETTER_DOMAINS:
        return False

    if _TRANSACTIONAL_SUBJECT_PATTERNS.search(subject):
        return True

    # Sender addresses that almost never send real newsletters
    if _TRANSACTIONAL_SENDER_PATTERNS.search(sender):
        return True

    return False


def _newsletter_score(email: Dict) -> int:
    """Return a heuristic score indicating how likely an email is a newsletter.

    Higher score = more likely a newsletter. Threshold of 2 is used to accept.
    """
    score = 0
    body = email.get("body_full", "")

    # Strong signal: sender is a known newsletter platform
    sender_email_addr, _ = _parse_sender(email.get("from", ""))
    if _sender_domain(sender_email_addr) in NEWSLETTER_DOMAINS:
        score += 3

    # Strong signal: List-Unsubscribe header (set by mailing-list software)
    if email.get("list_unsubscribe"):
        score += 2

    # Moderate signal: body mentions unsubscribe
    if re.search(r"unsubscribe", body, re.IGNORECASE):
        score += 1

    # Moderate signal: newsletter-style subject keywords
    subject = email.get("subject", "")
    if re.search(
        r"(newsletter|digest|weekly|daily|monthly|issue\s*#?\d|briefing|roundup|recap)",
        subject,
        re.IGNORECASE,
    ):
        score += 2

    # Weak signal: body contains typical newsletter phrases
    if re.search(
        r"(view\s+(in|this\s+email)\s+(browser|online)|email\s+preferences|manage\s+subscriptions)",
        body,
        re.IGNORECASE,
    ):
        score += 1

    return score


def detect_newsletters(emails: List[Dict], user_email: Optional[str] = None) -> List[Dict]:
    """Identify likely newsletters, deduplicated by sender email.

    Heuristics (combined score >= 2 to qualify):
    - Has a List-Unsubscribe header (+2)
    - Body contains 'unsubscribe' (+1)
    - Subject contains newsletter-style keywords (+2)
    - Body contains 'view in browser' / 'manage subscriptions' (+1)
    Bonus: multiple emails from the same sender in the batch (+1 each extra)

    Transactional emails (welcome, verify, receipts, etc.) are excluded.
    Self-emails and TLDRead emails are always excluded.
    """
    user_email_lower = user_email.lower() if user_email else None

    # --- Phase 1: score each email, count sender frequency ---
    sender_frequency: Dict[str, int] = {}
    scored_emails: List[tuple] = []  # (email, sender_email, sender_name, score)

    for email in emails:
        sender_email_addr, sender_name = _parse_sender(email["from"])

        # Skip emails from the user's own address
        if user_email_lower and sender_email_addr.lower() == user_email_lower:
            continue

        # Skip TLDRead emails (the app's own output)
        subject = email.get("subject", "")
        if re.search(r"newsletter\s+digest", subject, re.IGNORECASE):
            continue

        if _is_transactional(email):
            continue

        sender_frequency[sender_email_addr] = sender_frequency.get(sender_email_addr, 0) + 1
        score = _newsletter_score(email)
        scored_emails.append((email, sender_email_addr, sender_name, score))

    # --- Phase 2: boost score for repeated senders, deduplicate ---
    seen_senders: Dict[str, Dict] = {}  # sender_email -> best candidate dict

    for email, sender_email, sender_name, score in scored_emails:
        # Bonus for seeing the same sender more than once in the batch
        if sender_frequency[sender_email] > 1:
            score += 1

        if score < 2:
            continue

        # Keep the highest-scoring (or most recent) email per sender
        if sender_email not in seen_senders or score > seen_senders[sender_email]["_score"]:
            clean_text = _strip_html(email["body_snippet"])
            seen_senders[sender_email] = {
                "id": email["id"],
                "subject": email["subject"],
                "from": email["from"],
                "date": email["date"],
                "snippet": clean_text[:100],
                "sender_email": sender_email,
                "sender_name": sender_name,
                "email_count": sender_frequency[sender_email],
                "_score": score,
            }

    # Strip internal _score before returning
    results = []
    for entry in seen_senders.values():
        entry.pop("_score", None)
        results.append(entry)

    return results
