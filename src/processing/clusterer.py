import json
import logging
import time
from typing import Dict, List, Optional

import anthropic

from ..config import ANTHROPIC_API_KEY
from .prompts import CLUSTER_NEWSLETTERS_PROMPT
from .summarizer import _extract_json

logger = logging.getLogger(__name__)

MODEL = "claude-sonnet-4-5-20250929"
MAX_RETRIES = 3


def cluster_summaries(summaries: List[dict]) -> Optional[Dict]:
    """Cluster multiple newsletter summaries to find cross-newsletter themes.

    Args:
        summaries: List of dicts, each with "subject", "sender_name",
            and summary fields (key_points, topic_tags, one_line_summary, etc.).

    Returns:
        Parsed cluster dict with clusters, top_story, unique_finds,
        contradictions. Returns None on failure or if fewer than 2 summaries.
    """
    if len(summaries) < 2:
        logger.warning("Need 2+ summaries to cluster, got %d", len(summaries))
        return None

    if not ANTHROPIC_API_KEY:
        logger.error("ANTHROPIC_API_KEY not set — check .env file")
        return None

    # Build the summaries JSON for the prompt
    summaries_json = json.dumps(summaries, indent=2, default=str)

    prompt = CLUSTER_NEWSLETTERS_PROMPT.format(summaries_json=summaries_json)

    # Call Claude with retries
    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

    for attempt in range(1, MAX_RETRIES + 1):
        try:
            response = client.messages.create(
                model=MODEL,
                max_tokens=2048,
                messages=[{"role": "user", "content": prompt}],
            )

            if not response.content:
                logger.error(
                    "Claude returned empty content array (stop_reason=%s)",
                    response.stop_reason,
                )
                return None

            response_text = response.content[0].text

            logger.info(
                "Claude API usage — input: %d tokens, output: %d tokens",
                response.usage.input_tokens,
                response.usage.output_tokens,
            )

            logger.debug("Raw Claude clustering response:\n%s", response_text)

            if not response_text or not response_text.strip():
                logger.error(
                    "Claude returned blank text (stop_reason=%s)",
                    response.stop_reason,
                )
                return None

            # Strip markdown code fences if present
            cleaned = _extract_json(response_text)

            clusters = json.loads(cleaned)
            return clusters

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
                "Invalid JSON from Claude during clustering: %s\nRaw text was:\n%s",
                e,
                response_text,
            )
            return None

    return None
