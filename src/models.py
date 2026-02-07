from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional


@dataclass
class Newsletter:
    sender_email: str
    sender_name: str
    notes: str = ""
    id: Optional[int] = None
    created_at: Optional[datetime] = None


@dataclass
class Email:
    newsletter_id: int
    message_id: str
    subject: str
    received_at: datetime
    raw_html: str
    plain_text: str
    status: str = "pending"  # pending, processed, failed
    id: Optional[int] = None
    created_at: Optional[datetime] = None


@dataclass
class Summary:
    email_id: int
    key_points: list[str] = field(default_factory=list)
    entities: list[str] = field(default_factory=list)
    topic_tags: list[str] = field(default_factory=list)
    notable_links: list[dict] = field(default_factory=list)  # [{"url": ..., "title": ...}]
    importance_score: int = 5  # 1-10 scale
    one_line_summary: str = ""
    id: Optional[int] = None


@dataclass
class Cluster:
    digest_date: str  # YYYY-MM-DD format
    cluster_name: str
    summary: str
    email_ids: list[int] = field(default_factory=list)
    source_count: int = 0
    id: Optional[int] = None


@dataclass
class Subscription:
    user_id: int
    sender_email: str
    sender_name: str
    is_active: bool = True
    id: Optional[int] = None
    created_at: Optional[datetime] = None
