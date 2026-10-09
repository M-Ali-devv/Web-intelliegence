"""Time local search and export shaping on a temporary database.

This does not crawl websites and does not open data/websites.db.
The numbers it returns are only for the row count it just inserted.
"""

from __future__ import annotations

import time
from dataclasses import dataclass

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.models import Base, Website
from app.records import export_row, filtered_select


@dataclass
class BenchmarkResult:
    rows: int
    matched: int
    filter_ms: float
    export_ms: float
    note: str = "Local SQLite only. No websites were crawled. Not a 60,000-site measurement."


def measure(row_count: int = 200) -> BenchmarkResult:
    if row_count < 1:
        raise ValueError("row_count must be at least 1")
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    session_factory = sessionmaker(bind=engine)
    with session_factory() as session:
        for number in range(row_count):
            name = "ACME Robotics" if number % 2 == 0 else "Other Co"
            session.add(
                Website(
                    original_url=f"https://site{number}.test",
                    normalized_website=f"https://site{number}.test",
                    status="classified",
                    company_name=name,
                    confidence=80,
                )
            )
        session.commit()

        started = time.perf_counter()
        matched = list(session.scalars(filtered_select(q="ACME")))
        filter_ms = (time.perf_counter() - started) * 1000

        started = time.perf_counter()
        for site in session.scalars(select_all()):
            export_row(site)
        export_ms = (time.perf_counter() - started) * 1000

    return BenchmarkResult(
        rows=row_count,
        matched=len(matched),
        filter_ms=round(filter_ms, 3),
        export_ms=round(export_ms, 3),
    )


def select_all():
    from sqlalchemy import select

    return select(Website).order_by(Website.id)
