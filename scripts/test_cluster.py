#!/usr/bin/env python3
"""Test script to fetch recent newsletters, summarize each, and cluster them.

Fetches emails from the last 48 hours, skips non-newsletter senders,
summarizes each with Claude, then runs cross-newsletter clustering.
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
from src.processing.clusterer import cluster_summaries
from src.database import get_subscribed_sender_emails


def load_interests():
    # type: () -> list
    """Load interests from config/interests.yaml."""
    config_path = Path(__file__).parent.parent / "config" / "interests.yaml"
    with open(config_path, "r") as f:
        data = yaml.safe_load(f)
    return data.get("interests", [])


def filter_newsletters(emails, subscribed_emails):
    # type: (list, set) -> list
    """Return only emails from subscribed senders."""
    return [em for em in emails if em["sender_email"].lower() in subscribed_emails]


def print_clusters(cluster_data):
    # type: (dict) -> None
    """Pretty-print the clustering results."""
    # Top story
    top = cluster_data.get("top_story", {})
    if top:
        print("=" * 70)
        print("TOP STORY")
        print("=" * 70)
        print(f"  {top.get('name', 'N/A')}")
        print(f"  Why: {top.get('why', 'N/A')}")
        sources = top.get("sources", [])
        if sources:
            print(f"  Sources: {', '.join(sources)}")
        print()

    # Clusters
    clusters = cluster_data.get("clusters", [])
    if clusters:
        print("=" * 70)
        print(f"CROSS-NEWSLETTER THEMES ({len(clusters)} found)")
        print("=" * 70)
        for i, cluster in enumerate(clusters, 1):
            print(f"\n  {i}. {cluster.get('name', 'N/A')} "
                  f"[importance: {cluster.get('importance', 'N/A')}/10]")
            sources = cluster.get("sources", [])
            if sources:
                print(f"     Sources: {', '.join(sources)}")
            print(f"     {cluster.get('synthesis', 'N/A')}")
        print()

    # Unique finds
    unique = cluster_data.get("unique_finds", [])
    if unique:
        print("=" * 70)
        print(f"UNIQUE FINDS ({len(unique)})")
        print("=" * 70)
        for item in unique:
            print(f"\n  From: {item.get('source', 'N/A')}")
            print(f"  Insight: {item.get('insight', 'N/A')}")
            print(f"  Why notable: {item.get('why_notable', 'N/A')}")
        print()

    # Contradictions
    contradictions = cluster_data.get("contradictions", [])
    if contradictions:
        print("=" * 70)
        print(f"CONTRADICTIONS ({len(contradictions)})")
        print("=" * 70)
        for item in contradictions:
            print(f"\n  Topic: {item.get('topic', 'N/A')}")
            for pos in item.get("positions", []):
                print(f"    - {pos.get('source', '?')}: {pos.get('position', 'N/A')}")
        print()
    elif "contradictions" in cluster_data:
        print("No contradictions found.\n")


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

    newsletters = filter_newsletters(emails, subscribed)
    print(f"Found {len(newsletters)} newsletter emails "
          f"(filtered out {len(emails) - len(newsletters)} non-subscribed emails)\n")

    if len(newsletters) < 2:
        print("Need at least 2 newsletters for clustering.")
        if len(newsletters) == 1:
            print(f"Only found: \"{newsletters[0]['subject']}\" "
                  f"from {newsletters[0]['sender_name']}")
        return

    # Summarize each newsletter
    print("Summarizing each newsletter with Claude...\n")
    summaries = []
    for i, em in enumerate(newsletters, 1):
        print(f"  [{i}/{len(newsletters)}] {em['sender_name']}: {em['subject']}")
        summary = summarize_email(em, interests)
        if summary is not None:
            # Attach metadata so the clusterer knows which newsletter it came from
            summary["subject"] = em["subject"]
            summary["sender_name"] = em["sender_name"]
            summaries.append(summary)
            print(f"    -> {summary.get('one_line_summary', '(no summary)')}")
        else:
            print("    -> Summarization failed, skipping.")
        print()

    print(f"Successfully summarized {len(summaries)}/{len(newsletters)} newsletters\n")

    if len(summaries) < 2:
        print("Need at least 2 successful summaries for clustering.")
        return

    # Cluster the summaries
    print("Clustering across newsletters...\n")
    cluster_data = cluster_summaries(summaries)

    if cluster_data is None:
        print("Clustering failed. Check logs for details.")
        sys.exit(1)

    print_clusters(cluster_data)

    # Dump raw JSON for debugging
    print("-" * 70)
    print("Raw clustering JSON:")
    print(json.dumps(cluster_data, indent=2))


if __name__ == "__main__":
    main()
