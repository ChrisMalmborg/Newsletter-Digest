#!/usr/bin/env python3
"""End-to-end test: fetch, summarize, cluster, build digest, optionally send.

Usage:
    python scripts/test_digest.py             # Build and save HTML preview
    python scripts/test_digest.py --send      # Also send via email
"""
import argparse
import sys
from datetime import date
from pathlib import Path

import yaml

# Add project root to path so we can import src
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.config import DATA_DIR, DIGEST_TO_ADDRESS
from src.database import get_subscribed_sender_emails
from src.ingestion.imap_client import (
    fetch_new_emails,
    IMAPError,
    IMAPAuthError,
    IMAPConnectionError,
)
from src.ingestion.parser import extract_forwarded_sender
from src.processing.summarizer import summarize_email
from src.processing.clusterer import cluster_summaries
from src.delivery.digest_builder import build_digest
from src.delivery.email_sender import send_digest


def load_interests():
    """Load interests from config/interests.yaml."""
    config_path = Path(__file__).parent.parent / "config" / "interests.yaml"
    with open(config_path, "r") as f:
        data = yaml.safe_load(f)
    return data.get("interests", [])


def main():
    parser = argparse.ArgumentParser(description="Build and preview a newsletter digest")
    parser.add_argument("--send", action="store_true", help="Send the digest via email")
    args = parser.parse_args()

    interests = load_interests()
    print("Loaded {} interests: {}\n".format(len(interests), ", ".join(interests)))

    # Fetch emails
    print("Fetching emails from the last 48 hours...")
    try:
        emails = fetch_new_emails(since_hours=48)
    except IMAPConnectionError as e:
        print("Connection failed: {}".format(e))
        sys.exit(1)
    except IMAPAuthError as e:
        print("Authentication failed: {}".format(e))
        sys.exit(1)
    except IMAPError as e:
        print("IMAP error: {}".format(e))
        sys.exit(1)

    print("Found {} emails total\n".format(len(emails)))

    if not emails:
        print("No emails found. Try increasing the time window.")
        return

    # Filter to subscribed senders
    subscribed = get_subscribed_sender_emails()
    if not subscribed:
        print("No active subscriptions found.")
        print("Run: python scripts/manage_subscriptions.py auto-detect")
        return

    newsletters = [
        em for em in emails
        if em["sender_email"].lower() in subscribed
    ]
    print("Found {} newsletter emails (filtered out {} non-subscribed emails)\n".format(
        len(newsletters), len(emails) - len(newsletters)
    ))

    # Resolve forwarded senders — replace the forwarding address with
    # the original newsletter author when the email was forwarded.
    for em in newsletters:
        body = em.get("plain_body") or em.get("html_body") or ""
        if "Forwarded message" in body:
            original = extract_forwarded_sender(body)
            if original:
                print("  Forwarded email detected: {} -> {}".format(
                    em["sender_name"], original["name"]
                ))
                em["sender_name"] = original["name"]
                em["sender_email"] = original["email"]

    if len(newsletters) < 2:
        print("Need at least 2 newsletters for a digest.")
        if len(newsletters) == 1:
            print('Only found: "{}" from {}'.format(
                newsletters[0]["subject"], newsletters[0]["sender_name"]
            ))
        return

    # Summarize each newsletter
    print("Summarizing each newsletter with Claude...\n")
    summaries = []
    for i, em in enumerate(newsletters, 1):
        print("  [{}/{}] {}: {}".format(i, len(newsletters), em["sender_name"], em["subject"]))
        summary = summarize_email(em, interests)
        if summary is not None:
            summary["subject"] = em["subject"]
            summary["sender_name"] = em["sender_name"]
            summaries.append(summary)
            print("    -> {}".format(summary.get("one_line_summary", "(no summary)")))
        else:
            print("    -> Summarization failed, skipping.")
        print()

    print("Successfully summarized {}/{} newsletters\n".format(len(summaries), len(newsletters)))

    if len(summaries) < 2:
        print("Need at least 2 successful summaries for clustering.")
        return

    # Cluster
    print("Clustering across newsletters...\n")
    cluster_data = cluster_summaries(summaries)

    if cluster_data is None:
        print("Clustering failed. Check logs for details.")
        sys.exit(1)

    # Build digest
    today = date.today()
    digest = build_digest(summaries, cluster_data, today)

    # Save HTML preview
    preview_path = DATA_DIR / "test_digest.html"
    with open(preview_path, "w") as f:
        f.write(digest["html"])
    print("Digest saved to {} for preview\n".format(preview_path))
    print("Subject: {}\n".format(digest["subject"]))

    # Optionally send
    if args.send:
        to_addr = DIGEST_TO_ADDRESS
        if not to_addr:
            print("DIGEST_TO_ADDRESS not set in .env - cannot send.")
            sys.exit(1)
        print("Sending digest to {}...".format(to_addr))
        if send_digest(digest["html"], digest["text"], digest["subject"], to_addr):
            print("Sent successfully!")
        else:
            print("Send failed. Check SMTP settings in .env.")
            sys.exit(1)
    else:
        print("To send via email, re-run with --send")


if __name__ == "__main__":
    main()
