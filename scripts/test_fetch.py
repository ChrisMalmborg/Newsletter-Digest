#!/usr/bin/env python3
"""Test script to verify IMAP email fetching and HTML parsing.

Connects to the configured inbox, fetches recent emails,
parses the HTML, and prints a summary with a text preview.
Does not modify anything in the inbox.
"""
import sys
from pathlib import Path

# Add project root to path so we can import src
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.ingestion.imap_client import (
    fetch_new_emails,
    IMAPError,
    IMAPAuthError,
    IMAPConnectionError,
)
from src.ingestion.parser import parse_email_html


def main():
    print("Connecting to inbox and fetching emails from the last 24 hours...\n")

    try:
        emails = fetch_new_emails(since_hours=24)
    except IMAPConnectionError as e:
        print(f"Connection failed: {e}")
        print("Check IMAP_HOST and IMAP_PORT in your .env file.")
        sys.exit(1)
    except IMAPAuthError as e:
        print(f"Authentication failed: {e}")
        print("Check IMAP_USERNAME and IMAP_PASSWORD in your .env file.")
        sys.exit(1)
    except IMAPError as e:
        print(f"IMAP error: {e}")
        sys.exit(1)

    print(f"Found {len(emails)} emails\n")

    if not emails:
        print("No emails in the last 24 hours.")
        return

    print("-" * 70)
    for i, em in enumerate(emails, 1):
        received = em["received_at"].strftime("%Y-%m-%d %H:%M")
        print(f"{i:3}. {em['subject']}")
        print(f"     From: {em['sender_name']} <{em['sender_email']}>")
        print(f"     Date: {received}")
        has_html = "yes" if em["html_body"] else "no"
        has_text = "yes" if em["plain_body"] else "no"
        print(f"     Body: html={has_html}, plain={has_text}")

        # Parse the email content
        raw = em["html_body"] or em["plain_body"] or ""
        parsed = parse_email_html(raw)

        preview = parsed["clean_text"][:500]
        if len(parsed["clean_text"]) > 500:
            preview += "..."
        print(f"     Links: {len(parsed['links'])} found")
        print(f"     Preview:\n{_indent(preview, 8)}")
        print()


def _indent(text: str, spaces: int) -> str:
    """Indent each line of text by the given number of spaces."""
    prefix = " " * spaces
    return "\n".join(prefix + line for line in text.splitlines())


if __name__ == "__main__":
    main()
