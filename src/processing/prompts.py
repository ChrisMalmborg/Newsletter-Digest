SUMMARIZE_NEWSLETTER_PROMPT = '''
You are analyzing a newsletter to extract its key information efficiently.

<newsletter>
From: {sender_name} <{sender_email}>
Subject: {subject}
Date: {received_date}

{content}
</newsletter>

<user_interests>
{interests}
</user_interests>

Analyze this newsletter and respond with a JSON object containing:

1. "key_points": Array of 3-5 concise takeaways (1-2 sentences each). Focus on what's NEW or ACTIONABLE.

2. "entities": Array of {{"name": str, "type": str}} for notable people, companies, products, events. Type is one of: person, company, product, event, policy, other.

3. "topic_tags": Array of 2-4 short topic tags.

4. "notable_links": Array of {{"url": str, "description": str}} — only genuinely valuable links, max 3.

5. "importance_score": Integer 1-10 based on relevance to interests, timeliness, actionability, uniqueness.

6. "one_line_summary": Single sentence capturing the main point.

Respond ONLY with valid JSON.
'''

CLUSTER_NEWSLETTERS_PROMPT = '''
You are analyzing summaries from multiple newsletters to identify themes.

<summaries>
{summaries_json}
</summaries>

Respond with JSON containing:

1. "clusters": Array of topics mentioned in 2+ newsletters. Each: {{"name": str, "sources": [str], "synthesis": str (2-3 sentences), "importance": int 1-10}}. Order by importance.

2. "top_story": {{"name": str, "why": str, "sources": [str]}} — single most important topic today.

3. "unique_finds": Array (max 3) of {{"source": str, "insight": str, "why_notable": str}} — interesting points only one newsletter covered.

4. "contradictions": Array of {{"topic": str, "positions": [{{"source": str, "position": str}}]}} — may be empty.

Respond ONLY with valid JSON.
'''
