"""Open the local database file and create the websites table if it is missing.

This step uses a SQLite file (data/websites.db) so you can run it with no
separate database server. The table shape is the same one we can later move
to PostgreSQL.
"""

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
}


def init_db() -> None:
    DATA_DIR.mkdir(exist_ok=True)
    Base.metadata.create_all(engine)
    existing = {column["name"] for column in inspect(engine).get_columns("websites")}
    with engine.begin() as connection:
        for name, column_type in EXTRA_COLUMNS.items():
            if name not in existing:
                connection.execute(text(f"ALTER TABLE websites ADD COLUMN {name} {column_type}"))


def get_session() -> Session:
    return SessionLocal()
