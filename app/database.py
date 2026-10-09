"""Open the local database file and create the websites table if it is missing.

This step uses a SQLite file (data/websites.db) so you can run it with no
separate database server. The table shape is the same one we can later move
to PostgreSQL.
"""

import hashlib
from pathlib import Path

from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import Session, sessionmaker

from app.models import Base

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
DB_PATH = DATA_DIR / "websites.db"

engine = create_engine(f"sqlite:///{DB_PATH}", echo=False)
SessionLocal = sessionmaker(bind=engine)


# Added onto an existing websites table. create_all does not alter old tables.
EXTRA_COLUMNS = {
    "job_id": "INTEGER",
    "clean_text": "TEXT",
    "crawl_error": "TEXT",
    "pages_crawled": "INTEGER",
    "last_crawled_at": "DATETIME",
    "company_name": "VARCHAR(512)",
    "description": "TEXT",
    "business_type": "VARCHAR(64)",
    "industry": "VARCHAR(128)",
    "niche": "VARCHAR(128)",
    "products": "TEXT",
    "services": "TEXT",
    "confidence": "INTEGER",
    "evidence": "TEXT",
    "classification_meta": "TEXT",
    "claimed_at": "DATETIME",
    "crawl_status": "VARCHAR(32)",
    "classification_status": "VARCHAR(32)",
    "content_hash": "VARCHAR(64)",
    "secondary_industry": "VARCHAR(255)",
    "sub_niche": "VARCHAR(255)",
    "business_model": "VARCHAR(255)",
    "geographic_markets": "VARCHAR(512)",
}

JOB_EXTRA_COLUMNS = {
    "note": "TEXT",
}

INDEX_STATEMENTS = (
    "CREATE INDEX IF NOT EXISTS ix_websites_status ON websites (status)",
    "CREATE INDEX IF NOT EXISTS ix_websites_industry ON websites (industry)",
    "CREATE INDEX IF NOT EXISTS ix_websites_business_type ON websites (business_type)",
    "CREATE INDEX IF NOT EXISTS ix_websites_confidence ON websites (confidence)",
    "CREATE INDEX IF NOT EXISTS ix_jobs_status ON jobs (status)",
    "CREATE INDEX IF NOT EXISTS ix_jobs_kind ON jobs (kind)",
    "CREATE INDEX IF NOT EXISTS ix_websites_crawl_status ON websites (crawl_status)",
    "CREATE INDEX IF NOT EXISTS ix_websites_classification_status ON websites (classification_status)",
    "CREATE INDEX IF NOT EXISTS ix_evidence_website ON evidence_records (website_id)",
)

_AI_STATUSES = ("classified", "needs_review", "insufficient_evidence", "classify_failed")


def _add_missing(connection, table: str, columns: dict[str, str]) -> None:
    existing = {column["name"] for column in inspect(engine).get_columns(table)}
    for name, column_type in columns.items():
        if name not in existing:
            connection.execute(text(f"ALTER TABLE {table} ADD COLUMN {name} {column_type}"))


def init_db() -> None:
    DATA_DIR.mkdir(exist_ok=True)
    Base.metadata.create_all(engine)
    with engine.begin() as connection:
        _add_missing(connection, "websites", EXTRA_COLUMNS)
        _add_missing(connection, "jobs", JOB_EXTRA_COLUMNS)
        for statement in INDEX_STATEMENTS:
            connection.execute(text(statement))
        _backfill_statuses(connection)


def _backfill_statuses(connection) -> None:
    """Fill the split status columns on rows saved before they existed."""
    ai_list = ", ".join(f"'{status}'" for status in _AI_STATUSES)
    connection.execute(
        text(
            f"""
            UPDATE websites
            SET classification_status = status,
                crawl_status = CASE
                    WHEN clean_text IS NOT NULL AND clean_text != '' THEN 'crawled'
                    ELSE ''
                END
            WHERE crawl_status IS NULL AND status IN ({ai_list})
            """
        )
    )
    connection.execute(
        text(
            """
            UPDATE websites
            SET crawl_status = status
            WHERE crawl_status IS NULL
            """
        )
    )
    rows = connection.execute(
        text(
            """
            SELECT id, clean_text FROM websites
            WHERE content_hash IS NULL AND clean_text IS NOT NULL AND clean_text != ''
            """
        )
    ).all()
    for site_id, clean_text in rows:
        digest = hashlib.sha256(clean_text.encode("utf-8")).hexdigest()
        connection.execute(
            text("UPDATE websites SET content_hash = :digest WHERE id = :site_id"),
            {"digest": digest, "site_id": site_id},
        )


def get_session() -> Session:
    return SessionLocal()
