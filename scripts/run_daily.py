#!/usr/bin/env python3
"""Daily newsletter digest pipeline.

Fetches new emails, filters to subscribed senders, summarizes each with
Claude, clusters themes across newsletters, builds a digest, and sends it.

Usage:
    python scripts/run_daily.py                  # Full run
    python scripts/run_daily.py --dry-run        # Skip sending, save HTML to data/
    python scripts/run_daily.py --hours 48       # Look back 48 hours
    python scripts/run_daily.py --force           # Re-process already-processed emails
"""
import argparse
import logging
import sys
from datetime import date
from pathlib import Path

import yaml

# Add project root to path so we can import src
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.config import DATA_DIR, CONFIG_DIR, DIGEST_TO_ADDRESS
from src.database import (
    IntegrityError,
    init_db,
    get_connection,
    get_or_create_newsletter,
    save_email,
    save_summary,
    save_cluster,
    save_digest,
    get_todays_summaries,
    get_summaries_by_email_ids,
    get_email_by_id,
    update_email_status,
    get_subscribed_sender_emails,
)
from src.models import Email, Summary, Cluster
from src.ingestion.imap_client import (
    fetch_new_emails,
    IMAPError,
    IMAPAuthError,
    IMAPConnectionError,
)
from src.ingestion.gmail_api_client import fetch_emails_for_user, GmailAPIError
from src.ingestion.parser import extract_forwarded_sender
from src.processing.summarizer import summarize_email
from src.processing.clusterer import cluster_summaries
from src.delivery.digest_builder import build_digest
from src.delivery.email_sender import send_digest, send_digest_gmail_api

logger = logging.getLogger(__name__)


def setup_logging():
    """Configure logging to both console and a daily log file."""
    log_dir = DATA_DIR / "logs"
    log_dir.mkdir(exist_ok=True)
    log_file = log_dir / "digest_{}.log".format(date.today().isoformat())

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        handlers=[
            logging.StreamHandler(sys.stdout),
            logging.FileHandler(log_file),
        ],
    )


def load_interests():
    """Load interests from config/interests.yaml."""
    config_path = CONFIG_DIR / "interests.yaml"
    if not config_path.exists():
        logger.warning("No interests.yaml found at %s, using empty list", config_path)
        return []
    with open(config_path, "r") as f:
        data = yaml.safe_load(f)
    return data.get("interests", [])


def email_already_stored(message_id):
    """Check if a message_id already exists in the database."""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT id, status FROM emails WHERE message_id = ?", (message_id,))
    row = cursor.fetchone()
    conn.close()
    if row:
        return {"id": row["id"], "status": row["status"]}
    return None


def run(dry_run=False, hours=24, force=False, user=None):
    """Main pipeline orchestrator.

    If *user* is provided (an email address), emails are fetched via the Gmail
    API using that user's stored OAuth tokens and subscriptions.  Otherwise the
    legacy IMAP path is used.
    """
    setup_logging()
    logger.info("=" * 60)
    logger.info("Newsletter Digest Pipeline — %s", date.today().isoformat())
    logger.info(
        "Options: dry_run=%s, hours=%d, force=%s, user=%s",
        dry_run, hours, force, user or "(IMAP)",
    )
    logger.info("=" * 60)

    # Step 1: Initialize database
    init_db()
    logger.info("Database initialized")

    # Step 2: Load interests
    interests = load_interests()
    logger.info("Loaded %d interests", len(interests))

    # Step 3: Fetch new emails
    logger.info("Fetching emails from the last %d hours...", hours)

    if user:
        # --- Gmail API path (per-user OAuth) ---
        try:
            newsletters = fetch_emails_for_user(user, since_hours=hours)
        except GmailAPIError as e:
            logger.error("Gmail API error for %s: %s", user, e)
            return
        except Exception as e:
            logger.error("Unexpected error fetching Gmail for %s: %s", user, e)
            return

        raw_emails = newsletters  # already filtered to subscribed senders
        logger.info("Fetched %d subscribed newsletter emails via Gmail API", len(newsletters))

        if not newsletters:
            logger.info("No subscribed newsletter emails found. Nothing to do.")
            return
    else:
        # --- Legacy IMAP path ---
        try:
            raw_emails = fetch_new_emails(since_hours=hours)
        except (IMAPConnectionError, IMAPAuthError) as e:
            logger.error("Could not connect to email: %s", e)
            return
        except IMAPError as e:
            logger.error("IMAP error: %s", e)
            return

        logger.info("Fetched %d emails total", len(raw_emails))

        if not raw_emails:
            logger.info("No new emails found. Nothing to do.")
            return

        # Step 4: Filter to subscribed senders
        subscribed = get_subscribed_sender_emails()
        if not subscribed:
            logger.warning(
                "No active subscriptions. "
                "Run: python scripts/manage_subscriptions.py auto-detect"
            )
            return

        newsletters = [
            em for em in raw_emails
            if em["sender_email"].lower() in subscribed
        ]
        logger.info(
            "Filtered to %d subscribed newsletters (skipped %d)",
            len(newsletters),
            len(raw_emails) - len(newsletters),
        )

        if not newsletters:
            logger.info("No subscribed newsletter emails found. Nothing to do.")
            return

    # Resolve forwarded senders
    for em in newsletters:
        body = em.get("plain_body") or em.get("html_body") or ""
        if "Forwarded message" in body:
            original = extract_forwarded_sender(body)
            if original:
                logger.info(
                    "Forwarded email detected: %s -> %s",
                    em["sender_name"],
                    original["name"],
                )
                em["sender_name"] = original["name"]
                em["sender_email"] = original["email"]

    # Step 5: Process each email
    processed_count = 0
    skipped_count = 0
    failed_count = 0
    processed_email_ids = []  # Track all email IDs with summaries from this run

    for i, em in enumerate(newsletters, 1):
        msg_id = em["message_id"]
        label = "[{}/{}] {} — {}".format(
            i, len(newsletters), em["sender_name"], em["subject"]
        )
        logger.info("Processing %s", label)

        # Check for duplicates
        existing = email_already_stored(msg_id)
        if existing and not force:
            if existing["status"] == "processed":
                logger.info("  Already processed (id=%d), skipping", existing["id"])
                processed_email_ids.append(existing["id"])
                skipped_count += 1
                continue
            elif existing["status"] == "failed":
                logger.info("  Previously failed (id=%d), re-trying", existing["id"])
                email_id = existing["id"]
            else:
                # pending — pick it up
                logger.info("  Found pending email (id=%d), processing", existing["id"])
                email_id = existing["id"]
        elif existing and force:
            logger.info("  Already exists (id=%d) but --force set, re-processing", existing["id"])
            email_id = existing["id"]
        else:
            # Save new email to database
            newsletter_id = get_or_create_newsletter(
                em["sender_email"], em["sender_name"]
            )
            email_obj = Email(
                newsletter_id=newsletter_id,
                message_id=msg_id,
                subject=em["subject"],
                received_at=em["received_at"],
                raw_html=em.get("html_body") or "",
                plain_text=em.get("plain_body") or "",
                status="pending",
            )
            try:
                email_id = save_email(email_obj)
                logger.info("  Saved to database (id=%d)", email_id)
            except IntegrityError:
                # Race condition: another process inserted it
                existing = email_already_stored(msg_id)
                if existing:
                    email_id = existing["id"]
                    logger.info("  Already in DB (race), id=%d", email_id)
                    if existing["status"] == "processed" and not force:
                        processed_email_ids.append(email_id)
                        skipped_count += 1
                        continue
                else:
                    logger.error("  IntegrityError but message not found — skipping")
                    failed_count += 1
                    continue

        # Summarize with Claude
        try:
            summary_result = summarize_email(em, interests)
        except Exception as e:
            logger.error("  Summarization error: %s", e)
            update_email_status(email_id, "failed")
            failed_count += 1
            continue

        if summary_result is None:
            logger.warning("  Summarization returned None, marking as failed")
            update_email_status(email_id, "failed")
            failed_count += 1
            continue

        # Save summary to database
        summary_obj = Summary(
            email_id=email_id,
            key_points=summary_result.get("key_points", []),
            entities=summary_result.get("entities", []),
            topic_tags=summary_result.get("topic_tags", []),
            notable_links=summary_result.get("notable_links", []),
            importance_score=summary_result.get("importance_score", 5),
            one_line_summary=summary_result.get("one_line_summary", ""),
        )
        try:
            save_summary(summary_obj)
        except IntegrityError:
            # Summary already exists for this email_id (e.g. --force re-run)
            logger.info("  Summary already exists for email %d, skipping save", email_id)

        update_email_status(email_id, "processed")
        processed_email_ids.append(email_id)
        processed_count += 1

        logger.info("  -> %s", summary_result.get("one_line_summary", "(no summary)"))

    logger.info(
        "Processing complete: %d processed, %d skipped, %d failed",
        processed_count,
        skipped_count,
        failed_count,
    )

    # Step 6: Gather summaries for the digest
    # Use email IDs from this run so we include all emails in the fetch window,
    # regardless of when they were originally received.
    all_summaries = get_summaries_by_email_ids(processed_email_ids)
    logger.info("Total summaries available for today's digest: %d", len(all_summaries))

    if not all_summaries:
        logger.info("No summaries for today. Nothing to digest.")
        return

    # Build summary dicts for digest builder (needs sender_name and subject)
    digest_summaries = []
    for s in all_summaries:
        email_obj = get_email_by_id(s.email_id)
        if email_obj is None:
            logger.warning("Email id=%d not found for summary id=%d, skipping", s.email_id, s.id)
            continue
        # Look up sender name from newsletters table
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute(
            "SELECT sender_name FROM newsletters WHERE id = ?",
            (email_obj.newsletter_id,),
        )
        row = cursor.fetchone()
        conn.close()
        sender_name = row["sender_name"] if row else "Unknown"

        digest_summaries.append({
            "sender_name": sender_name,
            "subject": email_obj.subject,
            "key_points": s.key_points,
            "entities": s.entities,
            "topic_tags": s.topic_tags,
            "notable_links": s.notable_links,
            "importance_score": s.importance_score,
            "one_line_summary": s.one_line_summary,
        })

    if not digest_summaries:
        logger.info("No valid summaries to build digest from.")
        return

    # Step 7: Cluster if 2+ summaries
    cluster_data = None
    if len(digest_summaries) >= 2:
        logger.info("Clustering %d summaries...", len(digest_summaries))
        try:
            cluster_data = cluster_summaries(digest_summaries)
        except Exception as e:
            logger.error("Clustering failed: %s", e)
            cluster_data = None

        if cluster_data:
            # Save clusters to database
            today_str = date.today().isoformat()
            for cl in cluster_data.get("clusters", []):
                cluster_obj = Cluster(
                    digest_date=today_str,
                    cluster_name=cl.get("name", ""),
                    summary=cl.get("synthesis", ""),
                    email_ids=[],  # Could map source names to IDs if needed
                    source_count=len(cl.get("sources", [])),
                )
                try:
                    save_cluster(cluster_obj)
                except Exception as e:
                    logger.error("Failed to save cluster: %s", e)
            logger.info(
                "Found %d themes, top story: %s",
                len(cluster_data.get("clusters", [])),
                cluster_data.get("top_story", {}).get("name", "N/A"),
            )
        else:
            logger.warning("Clustering returned no results")
    else:
        logger.info("Only %d summary — skipping clustering", len(digest_summaries))

    # Provide empty cluster data if clustering was skipped or failed
    if cluster_data is None:
        cluster_data = {
            "clusters": [],
            "top_story": {},
            "unique_finds": [],
            "contradictions": [],
        }

    # Step 8: Build the digest
    today = date.today()
    digest = build_digest(digest_summaries, cluster_data, today)
    logger.info("Digest built — subject: %s", digest["subject"])

    # Step 8b: Save digest to database for the dashboard
    digest_user = user or DIGEST_TO_ADDRESS or "unknown"
    try:
        save_digest(
            user_email=digest_user,
            digest_date=today.isoformat(),
            subject=digest["subject"],
            html_content=digest["html"],
            themes_count=len(cluster_data.get("clusters", [])),
            newsletters_count=len(digest_summaries),
        )
        logger.info("Digest saved to database for %s", digest_user)
    except Exception as e:
        logger.error("Failed to save digest to database: %s", e)

    # Step 9: Send or save
    if dry_run:
        output_path = DATA_DIR / "digest_{}.html".format(today.isoformat())
        with open(output_path, "w") as f:
            f.write(digest["html"])
        logger.info("Dry run — digest saved to %s", output_path)

        text_path = DATA_DIR / "digest_{}.txt".format(today.isoformat())
        with open(text_path, "w") as f:
            f.write(digest["text"])
        logger.info("Dry run — plain text saved to %s", text_path)
    else:
        to_addr = user or DIGEST_TO_ADDRESS
        if not to_addr:
            logger.error(
                "DIGEST_TO_ADDRESS not set in .env — "
                "cannot send. Use --dry-run to save locally."
            )
            # Still save the HTML so the work isn't lost
            output_path = DATA_DIR / "digest_{}.html".format(today.isoformat())
            with open(output_path, "w") as f:
                f.write(digest["html"])
            logger.info("Digest saved to %s (sending skipped)", output_path)
            return

        # Use Gmail API for OAuth users, fall back to SMTP for legacy IMAP users
        if user:
            logger.info("Sending digest to %s via Gmail API...", to_addr)
            sent = send_digest_gmail_api(
                user_email=user,
                to_address=to_addr,
                subject=digest["subject"],
                html_content=digest["html"],
                text_content=digest["text"],
            )
        else:
            logger.info("Sending digest to %s via SMTP...", to_addr)
            sent = send_digest(digest["html"], digest["text"], digest["subject"], to_addr)

        if sent:
            logger.info("Digest sent successfully!")
        else:
            logger.error("Failed to send digest")
            # Save locally as fallback
            output_path = DATA_DIR / "digest_{}.html".format(today.isoformat())
            with open(output_path, "w") as f:
                f.write(digest["html"])
            logger.info("Digest saved to %s as fallback", output_path)

    # Step 10: Final summary
    logger.info("=" * 60)
    logger.info("Pipeline complete")
    logger.info("  Emails fetched:    %d", len(raw_emails))
    logger.info("  Newsletters found: %d", len(newsletters))
    logger.info("  Processed:         %d", processed_count)
    logger.info("  Skipped (dupes):   %d", skipped_count)
    logger.info("  Failed:            %d", failed_count)
    logger.info("  Summaries in digest: %d", len(digest_summaries))
    logger.info("  Themes found:      %d", len(cluster_data.get("clusters", [])))
    logger.info("=" * 60)


def main():
    parser = argparse.ArgumentParser(
        description="Run the daily newsletter digest pipeline"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Do everything except send the email; save HTML to data/",
    )
    parser.add_argument(
        "--hours",
        type=int,
        default=24,
        help="Look back N hours for new emails (default: 24)",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Re-process emails even if already processed today",
    )
    parser.add_argument(
        "--user",
        type=str,
        default=None,
        help="User email address — fetch via Gmail API using stored OAuth tokens",
    )
    args = parser.parse_args()

    run(dry_run=args.dry_run, hours=args.hours, force=args.force, user=args.user)


if __name__ == "__main__":
    main()
