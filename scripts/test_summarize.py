#!/usr/bin/env python3
"""Test script to fetch a recent newsletter and summarize it with Claude.

Skips Google security alert emails, picks the most recent newsletter,
loads interests from config/interests.yaml, and prints the summary.
"""
import json
import sys
from pathlib import Path

import yaml

# Add project root to path so we can import src
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.ingestion.imap_client import (
    fetch_new_emails,
    IMAPError,
    IMAPAuthError,
    IMAPConnectionError,
)
from src.processing.summarizer import summarize_email
from src.database import get_subscribed_sender_emails


def load_interests() -> list:
    """Load interests from config/interests.yaml."""
    config_path = Path(__file__).parent.parent / "config" / "interests.yaml"
    with open(config_path, "r") as f:
        data = yaml.safe_load(f)
    return data.get("interests", [])


def find_newsletter(emails: list, subscribed_emails: set) -> dict:
    """Return the most recent email from a subscribed sender."""
    for em in reversed(emails):  # reversed = most recent first
        if em["sender_email"].lower() in subscribed_emails:
            return em
    return None


def print_summary(email_data: dict, summary: dict) -> None:
    """Pretty-print the email summary."""
    print("=" * 70)
    print(f"Subject:    {email_data['subject']}")
    print(f"From:       {email_data['sender_name']} <{email_data['sender_email']}>")
    print(f"Date:       {email_data['received_at'].strftime('%Y-%m-%d %H:%M')}")
    print("=" * 70)

    print(f"\nOne-line summary: {summary.get('one_line_summary', 'N/A')}")
    print(f"Importance score: {summary.get('importance_score', 'N/A')}/10")

    print("\nKey points:")
    for i, point in enumerate(summary.get("key_points", []), 1):
        print(f"  {i}. {point}")

    print(f"\nTopic tags: {', '.join(summary.get('topic_tags', []))}")

    entities = summary.get("entities", [])
    if entities:
        print("\nEntities:")
        for ent in entities:
            print(f"  - {ent['name']} ({ent['type']})")

    links = summary.get("notable_links", [])
    if links:
        print("\nNotable links:")
        for link in links:
            print(f"  - {link.get('description', '')}: {link.get('url', '')}")

    print()


def main():
    # Load interests
    interests = load_interests()
    print(f"Loaded {len(interests)} interests: {', '.join(interests)}\n")

    # Fetch emails
    print("Fetching emails from the last 48 hours...")
    try:
        emails = fetch_new_emails(since_hours=48)
    except IMAPConnectionError as e:
        print(f"Connection failed: {e}")
        sys.exit(1)
    except IMAPAuthError as e:
        print(f"Authentication failed: {e}")
        sys.exit(1)
    except IMAPError as e:
        print(f"IMAP error: {e}")
        sys.exit(1)

    print(f"Found {len(emails)} emails total\n")

    if not emails:
        print("No emails found. Try increasing the time window.")
        return

    # Filter to subscribed senders only
    subscribed = get_subscribed_sender_emails()
    if not subscribed:
        print("No active subscriptions found.")
        print("Run: python scripts/manage_subscriptions.py auto-detect")
        return

    newsletter = find_newsletter(emails, subscribed)
    if not newsletter:
        print("No emails from subscribed senders found.")
        return

    print(f"Selected: \"{newsletter['subject']}\" from {newsletter['sender_name']}\n")
    print("Summarizing with Claude...\n")

    summary = summarize_email(newsletter, interests)

    if summary is None:
        print("Summarization failed. Check logs for details.")
        sys.exit(1)

    print_summary(newsletter, summary)

    # Also dump raw JSON for debugging
    print("-" * 70)
    print("Raw JSON response:")
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
