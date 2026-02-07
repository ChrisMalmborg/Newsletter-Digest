"""Build a digest email from summaries and cluster data."""

import logging
from datetime import date
from pathlib import Path
from typing import Dict, List, Optional

from jinja2 import Environment, FileSystemLoader

logger = logging.getLogger(__name__)

TEMPLATES_DIR = Path(__file__).parent / "templates"


def build_digest(
    summaries: List[dict],
    clusters: dict,
    digest_date: date,
) -> Dict[str, str]:
    """Build HTML and plain-text digest from summaries and cluster data.

    Returns {"html": str, "text": str, "subject": str}.
    """
    # Extract cluster sub-sections
    top_story = clusters.get("top_story") or {}
    theme_list = clusters.get("clusters") or []
    unique_finds = clusters.get("unique_finds") or []
    contradictions = clusters.get("contradictions") or []

    # Format date for display
    date_str = digest_date.strftime("%b %-d, %Y")
    newsletter_count = len(summaries)
    theme_count = len(theme_list)

    # Build subject line
    subject = "Your Newsletter Digest \u2014 {}".format(
        digest_date.strftime("%b %-d")
    )
    parts = []
    parts.append("{} newsletter{}".format(
        newsletter_count, "s" if newsletter_count != 1 else ""
    ))
    if theme_count:
        parts.append("{} theme{}".format(
            theme_count, "s" if theme_count != 1 else ""
        ))
    subject += " ({})".format(", ".join(parts))

    # Render HTML
    env = Environment(
        loader=FileSystemLoader(str(TEMPLATES_DIR)),
        autoescape=True,
    )
    template = env.get_template("digest.html")
    html = template.render(
        subject=subject,
        digest_date=date_str,
        newsletter_count=newsletter_count,
        top_story=top_story if top_story else None,
        clusters=theme_list,
        summaries=summaries,
        unique_finds=unique_finds,
        contradictions=contradictions,
    )

    # Build plain-text fallback
    text = _build_plain_text(
        date_str=date_str,
        newsletter_count=newsletter_count,
        top_story=top_story,
        clusters=theme_list,
        summaries=summaries,
        unique_finds=unique_finds,
        contradictions=contradictions,
    )

    return {"html": html, "text": text, "subject": subject}


def _build_plain_text(
    date_str: str,
    newsletter_count: int,
    top_story: Optional[dict],
    clusters: List[dict],
    summaries: List[dict],
    unique_finds: List[dict],
    contradictions: List[dict],
) -> str:
    """Generate a plain-text version of the digest."""
    lines = []
    lines.append("NEWSLETTER DIGEST")
    lines.append("{} - {} newsletter{}".format(
        date_str, newsletter_count, "s" if newsletter_count != 1 else ""
    ))
    lines.append("=" * 60)

    if top_story and top_story.get("name"):
        lines.append("")
        lines.append("TOP STORY")
        lines.append("-" * 40)
        lines.append(top_story["name"])
        if top_story.get("why"):
            lines.append(top_story["why"])
        if top_story.get("sources"):
            lines.append("From: {}".format(", ".join(top_story["sources"])))

    if clusters:
        lines.append("")
        lines.append("THEMES ACROSS YOUR NEWSLETTERS")
        lines.append("-" * 40)
        for i, cluster in enumerate(clusters, 1):
            importance = cluster.get("importance", "")
            imp_str = " [{}/10]".format(importance) if importance else ""
            lines.append("")
            lines.append("{}. {}{}".format(i, cluster.get("name", ""), imp_str))
            if cluster.get("synthesis"):
                lines.append("   {}".format(cluster["synthesis"]))
            if cluster.get("sources"):
                lines.append("   Sources: {}".format(", ".join(cluster["sources"])))

    if summaries:
        lines.append("")
        lines.append("INDIVIDUAL NEWSLETTERS")
        lines.append("-" * 40)
        for summary in summaries:
            lines.append("")
            lines.append("{} - {}".format(
                summary.get("sender_name", ""),
                summary.get("subject", ""),
            ))
            if summary.get("one_line_summary"):
                lines.append("  {}".format(summary["one_line_summary"]))
            for point in summary.get("key_points", []):
                lines.append("  * {}".format(point))

    if unique_finds:
        lines.append("")
        lines.append("UNIQUE FINDS")
        lines.append("-" * 40)
        for find in unique_finds:
            lines.append("")
            lines.append("From {}:".format(find.get("source", "")))
            lines.append("  {}".format(find.get("insight", "")))
            if find.get("why_notable"):
                lines.append("  Why: {}".format(find["why_notable"]))

    if contradictions:
        lines.append("")
        lines.append("CONTRADICTIONS")
        lines.append("-" * 40)
        for item in contradictions:
            lines.append("")
            lines.append(item.get("topic", ""))
            for pos in item.get("positions", []):
                lines.append("  {}: {}".format(
                    pos.get("source", ""), pos.get("position", "")
                ))

    lines.append("")
    lines.append("---")
    lines.append("Generated by Newsletter Digest")
    return "\n".join(lines)
