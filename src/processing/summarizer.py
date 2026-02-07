import json
import logging
import re
import time
from typing import List, Optional

import anthropic

from ..config import ANTHROPIC_API_KEY
from .prompts import SUMMARIZE_NEWSLETTER_PROMPT
from ..ingestion.parser import parse_email_html

logger = logging.getLogger(__name__)

MODEL = "claude-sonnet-4-5-20250929"
MAX_CONTENT_CHARS = 12000  # Truncate very long newsletters to control token usage
MAX_RETRIES = 3


def _extract_json(text: str) -> str:
    """Strip markdown code fences if Claude wrapped the JSON in them."""
    # Match ```json ... ``` or ``` ... ```
    match = re.search(r"```(?:json)?\s*\n?(.*?)\n?\s*```", text, re.DOTALL)
    if match:
        return match.group(1).strip()
    return text.strip()


def summarize_email(email_data: dict, interests: List[str]) -> Optional[dict]:
    """Summarize a newsletter email using Claude.

    Args:
        email_data: Dict from fetch_new_emails with keys like
            sender_name, sender_email, subject, received_at,
            html_body, plain_body.
        interests: List of interest strings for relevance scoring.

    Returns:
        Parsed summary dict with key_points, entities, topic_tags,
        notable_links, importance_score, one_line_summary.
        Returns None on failure.
    """
    if not ANTHROPIC_API_KEY:
        logger.error("ANTHROPIC_API_KEY not set — check .env file")
        return None
    logger.debug("API key loaded: %s...%s", ANTHROPIC_API_KEY[:4], ANTHROPIC_API_KEY[-4:])

    # Parse email content
    raw_html = email_data.get("html_body") or email_data.get("plain_body") or ""
    parsed = parse_email_html(raw_html)
    content = parsed["clean_text"]

    if not content.strip():
        logger.warning("Empty content for email: %s", email_data.get("subject"))
        return None

    # Truncate if too long
    if len(content) > MAX_CONTENT_CHARS:
        content = content[:MAX_CONTENT_CHARS] + "\n\n[Content truncated...]"

    # Format the prompt
    received_date = email_data["received_at"].strftime("%Y-%m-%d %H:%M %Z")
    interests_str = "\n".join("- " + i for i in interests)

    prompt = SUMMARIZE_NEWSLETTER_PROMPT.format(
        sender_name=email_data.get("sender_name", "Unknown"),
        sender_email=email_data.get("sender_email", ""),
        subject=email_data.get("subject", "(no subject)"),
        received_date=received_date,
        content=content,
        interests=interests_str,
    )

    # Call Claude with retries
    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

    for attempt in range(1, MAX_RETRIES + 1):
        try:
            response = client.messages.create(
                model=MODEL,
                max_tokens=1024,
                messages=[{"role": "user", "content": prompt}],
            )

            # Extract text from response, guarding against empty content
            if not response.content:
                logger.error("Claude returned empty content array (stop_reason=%s)", response.stop_reason)
                return None

            response_text = response.content[0].text

            # Log token usage
            logger.info(
                "Claude API usage — input: %d tokens, output: %d tokens",
                response.usage.input_tokens,
                response.usage.output_tokens,
            )

            # Debug: show raw response before parsing
            logger.debug("Raw Claude response:\n%s", response_text)

            if not response_text or not response_text.strip():
                logger.error("Claude returned blank text (stop_reason=%s)", response.stop_reason)
                return None

            # Strip markdown code fences if present
            cleaned = _extract_json(response_text)

            # Parse JSON response
            summary = json.loads(cleaned)
            return summary

        except anthropic.APIError as e:
            logger.warning(
                "Claude API error (attempt %d/%d): %s", attempt, MAX_RETRIES, e
            )
            if attempt < MAX_RETRIES:
                wait = 2 ** attempt
                logger.info("Retrying in %ds...", wait)
                time.sleep(wait)
            else:
                logger.error("Claude API failed after %d attempts", MAX_RETRIES)
                return None

        except json.JSONDecodeError as e:
            logger.error(
                "Invalid JSON from Claude for '%s': %s\nRaw text was:\n%s",
                email_data.get("subject"),
                e,
                response_text,
            )
            return None

    return None
