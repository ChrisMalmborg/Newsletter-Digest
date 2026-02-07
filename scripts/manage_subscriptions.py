#!/usr/bin/env python3
"""Manage newsletter subscriptions.

CLI tool to add, remove, list, and auto-detect newsletter subscriptions.
"""
import argparse
import sys
from collections import Counter
from pathlib import Path

# Add project root to path so we can import src
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.database import (
    init_db,
    get_all_subscriptions,
    add_subscription,
    deactivate_subscription,
)
from src.ingestion.imap_client import (
    fetch_new_emails,
    IMAPError,
    IMAPAuthError,
    IMAPConnectionError,
)

# Senders to exclude from auto-detect suggestions
AUTO_DETECT_SKIP = {
    "no-reply@accounts.google.com",
    "noreply@google.com",
    "security-noreply@google.com",
}


def cmd_list(args):
    """Show all subscriptions."""
    subs = get_all_subscriptions()

    if not subs:
        print("No subscriptions yet.")
        print("Run: python scripts/manage_subscriptions.py auto-detect")
        return

    print(f"{'Status':<10} {'Email':<40} {'Name':<25} {'Created'}")
    print("-" * 95)
    for sub in subs:
        status = "active" if sub.is_active else "inactive"
        created = sub.created_at.strftime("%Y-%m-%d") if sub.created_at else "N/A"
        print(f"{status:<10} {sub.sender_email:<40} {sub.sender_name:<25} {created}")

    active = sum(1 for s in subs if s.is_active)
    print(f"\n{active} active, {len(subs) - active} inactive")


def cmd_add(args):
    """Add a subscription."""
    email = args.email.lower()
    name = args.name if args.name else email.split("@")[0]

    sub_id = add_subscription(email, name)
    print(f"Subscribed to {name} <{email}> (id={sub_id})")


def cmd_remove(args):
    """Deactivate a subscription."""
    email = args.email.lower()

    if deactivate_subscription(email):
        print(f"Deactivated subscription for {email}")
    else:
        print(f"No active subscription found for {email}")


def cmd_auto_detect(args):
    """Scan recent emails and suggest newsletters to subscribe to."""
    print("Fetching emails from the last 72 hours...")
    try:
        emails = fetch_new_emails(since_hours=72)
    except IMAPConnectionError as e:
        print(f"Connection failed: {e}")
        sys.exit(1)
    except IMAPAuthError as e:
        print(f"Authentication failed: {e}")
        sys.exit(1)
    except IMAPError as e:
        print(f"IMAP error: {e}")
        sys.exit(1)

    if not emails:
        print("No emails found in the last 72 hours.")
        return

    # Group by sender, count occurrences
    sender_counts = Counter()
    sender_names = {}
    for em in emails:
        addr = em["sender_email"].lower()
        if addr not in AUTO_DETECT_SKIP:
            sender_counts[addr] += 1
            sender_names[addr] = em["sender_name"]

    if not sender_counts:
        print("No candidate senders found after filtering.")
        return

    print(f"\nFound {len(sender_counts)} unique senders:\n")
    print(f"  {'Count':<7} {'Email':<40} {'Name'}")
    print("  " + "-" * 80)

    # Sort by count descending
    sorted_senders = sorted(sender_counts.items(), key=lambda x: x[1], reverse=True)
    for addr, count in sorted_senders:
        print(f"  {count:<7} {addr:<40} {sender_names[addr]}")

    print()

    # Interactive: prompt for each sender
    added = 0
    for addr, count in sorted_senders:
        name = sender_names[addr]
        try:
            answer = input(f"Subscribe to {name} <{addr}>? (y/n) ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            print("\nAborted.")
            return
        if answer == "y":
            add_subscription(addr, name)
            added += 1
            print(f"  Added {name}")

    print(f"\nAdded {added} subscription(s).")


def main():
    init_db()

    parser = argparse.ArgumentParser(description="Manage newsletter subscriptions")
    subparsers = parser.add_subparsers(dest="command")

    # list
    subparsers.add_parser("list", help="Show all subscriptions")

    # add
    add_parser = subparsers.add_parser("add", help="Add a subscription")
    add_parser.add_argument("email", help="Sender email address")
    add_parser.add_argument("--name", help="Sender display name (default: email local part)")

    # remove
    remove_parser = subparsers.add_parser("remove", help="Deactivate a subscription")
    remove_parser.add_argument("email", help="Sender email address to deactivate")

    # auto-detect
    subparsers.add_parser("auto-detect", help="Scan recent emails and suggest subscriptions")

    args = parser.parse_args()

    if args.command == "list":
        cmd_list(args)
    elif args.command == "add":
        cmd_add(args)
    elif args.command == "remove":
        cmd_remove(args)
    elif args.command == "auto-detect":
        cmd_auto_detect(args)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
