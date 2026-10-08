"""Read a pasted list or a CSV and insert new websites.

A row is skipped when:
- it is not a real website address
- the same website is already in the database
- the same website appears twice in this import
"""

import csv
import io
from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Website
from app.normalize import normalize_website

URL_COLUMNS = {"url", "original_url", "website", "website_url", "link"}


@dataclass
class ImportResult:
    added: int = 0
    duplicates: list[str] = field(default_factory=list)
    invalid: list[str] = field(default_factory=list)


def extract_urls(text: str) -> list[str]:
    """Pull website addresses out of plain lines or a simple CSV."""
    text = text.strip()
    if not text:
        return []

    first_line = text.splitlines()[0]
    headers = [part.strip().lower() for part in next(csv.reader([first_line]))]
    url_column = next((name for name in headers if name in URL_COLUMNS), None)

    if url_column is not None:
        reader = csv.DictReader(io.StringIO(text))
        urls: list[str] = []
        for row in reader:
            value = (row.get(url_column) or row.get(url_column.title()) or "").strip()
            # DictReader keys keep the original header text. Match case-insensitively.
            if not value:
                for key, cell in row.items():
                    if key and key.strip().lower() == url_column:
                        value = (cell or "").strip()
                        break
            if value:
                urls.append(value)
        return urls

    urls = []
    for line in text.splitlines():
        cell = next(csv.reader([line.strip()]), [""])[0].strip()
        if cell:
            urls.append(cell)
    return urls


def import_urls(session: Session, text: str, source_batch: str | None = None) -> ImportResult:
    result = ImportResult()
    batch = source_batch.strip() if source_batch and source_batch.strip() else None
    seen_in_this_batch: set[str] = set()

    for raw in extract_urls(text):
        normalized = normalize_website(raw)
        if normalized is None:
            result.invalid.append(raw)
            continue

        if normalized in seen_in_this_batch:
            result.duplicates.append(normalized)
            continue

        already_saved = session.scalar(
            select(Website.id).where(Website.normalized_website == normalized)
        )
        if already_saved is not None:
            result.duplicates.append(normalized)
            seen_in_this_batch.add(normalized)
            continue

        session.add(
            Website(
                original_url=raw.strip(),
                normalized_website=normalized,
                status="pending",
                source_batch=batch,
            )
        )
        seen_in_this_batch.add(normalized)
        result.added += 1

    session.commit()
    return result
