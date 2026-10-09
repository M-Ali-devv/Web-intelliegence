"""One row per company.

The address and status are filled when a URL is saved.
clean_text is filled by the crawler.
The company fields are filled by the AI classification.
Until then those columns stay empty.
"""

from datetime import datetime, timezone

from sqlalchemy import DateTime, ForeignKey, Index, Integer, String, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class Job(Base):
    """One durable record per import. Survives a restart; websites point at it."""

    __tablename__ = "jobs"

    id: Mapped[int] = mapped_column(primary_key=True)
    kind: Mapped[str] = mapped_column(String(32), default="import")
    status: Mapped[str] = mapped_column(String(32), default="queued")
    added: Mapped[int] = mapped_column(Integer, default=0)
    duplicates: Mapped[int] = mapped_column(Integer, default=0)
    invalid: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=lambda: datetime.now(timezone.utc).replace(tzinfo=None),
    )
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)

    __table_args__ = (
        Index("ix_jobs_status", "status"),
        Index("ix_jobs_kind", "kind"),
    )


class Website(Base):
    __tablename__ = "websites"

    id: Mapped[int] = mapped_column(primary_key=True)
    original_url: Mapped[str] = mapped_column(String(2048))
    normalized_website: Mapped[str] = mapped_column(String(2048), unique=True, index=True)
    status: Mapped[str] = mapped_column(String(32), default="pending")
    job_id: Mapped[int | None] = mapped_column(ForeignKey("jobs.id"), nullable=True, index=True)
    source_batch: Mapped[str | None] = mapped_column(String(255), nullable=True)
    clean_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    crawl_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    pages_crawled: Mapped[int | None] = mapped_column(Integer, nullable=True)
    last_crawled_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    claimed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    company_name: Mapped[str | None] = mapped_column(String(512), nullable=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    business_type: Mapped[str | None] = mapped_column(String(64), nullable=True)
    industry: Mapped[str | None] = mapped_column(String(128), nullable=True)
    niche: Mapped[str | None] = mapped_column(String(128), nullable=True)
    products: Mapped[str | None] = mapped_column(Text, nullable=True)
    services: Mapped[str | None] = mapped_column(Text, nullable=True)
    confidence: Mapped[int | None] = mapped_column(Integer, nullable=True)
    evidence: Mapped[str | None] = mapped_column(Text, nullable=True)
    classification_meta: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=lambda: datetime.now(timezone.utc).replace(tzinfo=None),
    )

    __table_args__ = (
        Index("ix_websites_status", "status"),
        Index("ix_websites_industry", "industry"),
        Index("ix_websites_business_type", "business_type"),
        Index("ix_websites_confidence", "confidence"),
    )
