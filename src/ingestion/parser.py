import logging
import re
from typing import Dict, List, Optional

from bs4 import BeautifulSoup
import html2text

logger = logging.getLogger(__name__)

# Footer patterns to strip (case-insensitive)
_FOOTER_PATTERNS = [
    re.compile(r"unsubscribe", re.IGNORECASE),
    re.compile(r"view\s+(this\s+)?(email\s+)?in\s+(your\s+)?browser", re.IGNORECASE),
    re.compile(r"manage\s+(your\s+)?(email\s+)?preferences", re.IGNORECASE),
    re.compile(r"opt[\s-]?out", re.IGNORECASE),
    re.compile(r"email\s+preferences", re.IGNORECASE),
    re.compile(r"update\s+(your\s+)?subscription", re.IGNORECASE),
    re.compile(r"you('re|\s+are)\s+receiving\s+this", re.IGNORECASE),
    re.compile(r"sent\s+to\s+\S+@\S+", re.IGNORECASE),
    re.compile(r"no\s+longer\s+wish\s+to\s+receive", re.IGNORECASE),
    re.compile(r"click\s+here\s+to\s+unsubscribe", re.IGNORECASE),
    re.compile(r"powered\s+by\s+(mailchimp|substack|convertkit|beehiiv|buttondown)", re.IGNORECASE),
]


def extract_forwarded_sender(text: str) -> Optional[Dict[str, str]]:
    """Extract the original sender from a forwarded email body.

    Looks for the "From:" line that appears in forwarded email bodies, e.g.:
        From: **Dan Primack** <dan@axios.com>
        From: **The Rundown AI** <news@daily.therundown.ai>
        From: Dan Primack <dan@axios.com>

    Args:
        text: The email body text (plain text or parsed HTML).

    Returns:
        {"name": str, "email": str} if a forwarded sender is found,
        None otherwise.
    """
    if not text:
        return None

    # Match "From: **Name** <email>" or "From: Name <email>"
    # The ** markers come from markdown-bold rendering of forwarded headers
    match = re.search(
        r"From:\s*\*{0,2}(.+?)\*{0,2}\s*<([^>]+@[^>]+)>",
        text,
    )
    if match:
        name = match.group(1).strip()
        email = match.group(2).strip()
        if name and email:
            return {"name": name, "email": email}

    return None


def parse_email_html(html: str) -> dict:
    """Parse email HTML into clean text and extracted links.

    Args:
        html: Raw HTML string from the email. Can also be plain text.

    Returns:
        Dict with keys:
            - "clean_text": readable plain text with paragraph breaks preserved
            - "links": list of {"url": str, "text": str}
    """
    if not html or not html.strip():
        return {"clean_text": "", "links": []}

    # Detect if the content is plain text (no HTML tags)
    if not _looks_like_html(html):
        return {
            "clean_text": html.strip(),
            "links": _extract_plain_text_links(html),
        }

    try:
        return _parse_html(html)
    except Exception as e:
        logger.warning("HTML parsing failed, falling back to plain text: %s", e)
        # Last resort: strip all tags with a basic regex
        text = re.sub(r"<[^>]+>", " ", html)
        text = re.sub(r"\s+", " ", text).strip()
        return {"clean_text": text, "links": []}


def _looks_like_html(text: str) -> bool:
    """Check whether text contains HTML markup."""
    return bool(re.search(r"<\s*(html|body|div|p|table|a|span|br)\b", text, re.IGNORECASE))


def _parse_html(html: str) -> dict:
    """Core HTML parsing logic."""
    soup = BeautifulSoup(html, "html.parser")

    # Remove non-content tags
    for tag_name in ("script", "style", "head", "meta", "link", "noscript"):
        for tag in soup.find_all(tag_name):
            tag.decompose()

    # Remove tracking pixels (1x1 images)
    for img in soup.find_all("img"):
        width = img.get("width", "")
        height = img.get("height", "")
        if _is_tracking_pixel(width, height):
            img.decompose()

    # Extract links before stripping footer
    links = _extract_links(soup)

    # Remove footer-like elements
    _remove_footer_content(soup)

    # Convert to plain text via html2text
    converter = html2text.HTML2Text()
    converter.ignore_links = True
    converter.ignore_images = True
    converter.ignore_emphasis = False
    converter.body_width = 0  # No wrapping
    converter.unicode_snob = True

    clean = converter.handle(str(soup))

    # Normalize whitespace: collapse 3+ blank lines into 2
    clean = re.sub(r"\n{3,}", "\n\n", clean)
    clean = clean.strip()

    return {"clean_text": clean, "links": links}


def _is_tracking_pixel(width: str, height: str) -> bool:
    """Return True if dimensions indicate a tracking pixel."""
    try:
        w = int(re.sub(r"[^\d]", "", str(width))) if width else None
        h = int(re.sub(r"[^\d]", "", str(height))) if height else None
    except (ValueError, TypeError):
        return False
    if w is not None and h is not None and w <= 1 and h <= 1:
        return True
    return False


def _extract_links(soup: BeautifulSoup) -> List[dict]:
    """Extract all meaningful links from the soup."""
    links = []
    seen_urls: set = set()

    for a in soup.find_all("a", href=True):
        url = a["href"].strip()

        # Skip anchors, mailto, tel, and empty links
        if not url or url.startswith(("#", "mailto:", "tel:")):
            continue

        # Skip common tracking / unsubscribe links
        lower_url = url.lower()
        if any(kw in lower_url for kw in ("unsubscribe", "manage-preferences", "tracking", "list-manage")):
            continue

        if url in seen_urls:
            continue
        seen_urls.add(url)

        link_text = a.get_text(strip=True) or ""
        links.append({"url": url, "text": link_text})

    return links


def _extract_plain_text_links(text: str) -> List[dict]:
    """Extract URLs from plain text content."""
    urls = re.findall(r"https?://[^\s<>\"']+", text)
    seen: set = set()
    links = []
    for url in urls:
        # Strip trailing punctuation
        url = url.rstrip(".,;:!?)")
        if url not in seen:
            seen.add(url)
            links.append({"url": url, "text": ""})
    return links


def _remove_footer_content(soup: BeautifulSoup) -> None:
    """Remove elements that match common email footer patterns."""
    # Check common footer containers
    for tag in soup.find_all(["div", "td", "tr", "table", "p", "span"]):
        text = tag.get_text(strip=True)
        if not text:
            continue

        # Only consider shorter text blocks as potential footer lines
        if len(text) > 500:
            continue

        match_count = sum(1 for pat in _FOOTER_PATTERNS if pat.search(text))
        if match_count >= 1:
            tag.decompose()
