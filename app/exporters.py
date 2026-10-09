"""Stream company rows as CSV, JSON, or Excel without loading the full table."""

from __future__ import annotations

import csv
import io
import json
from collections.abc import Iterator

from openpyxl import Workbook
from sqlalchemy.sql import Select

from app.database import get_session
from app.records import EXPORT_COLUMNS, export_row


def iter_csv(stmt: Select) -> Iterator[str]:
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(EXPORT_COLUMNS)
    yield buffer.getvalue()
    with get_session() as session:
        for site in session.scalars(stmt).yield_per(200):
            buffer.seek(0)
            buffer.truncate()
            row = export_row(site)
            writer.writerow([row[column] for column in EXPORT_COLUMNS])
            yield buffer.getvalue()


def iter_json(stmt: Select) -> Iterator[str]:
    yield "["
    first = True
    with get_session() as session:
        for site in session.scalars(stmt).yield_per(200):
            prefix = "" if first else ","
            first = False
            yield prefix + json.dumps(export_row(site), ensure_ascii=False)
    yield "]"


def iter_xlsx(stmt: Select) -> Iterator[bytes]:
    book = Workbook(write_only=True)
    sheet = book.create_sheet("companies")
    sheet.append(list(EXPORT_COLUMNS))
    with get_session() as session:
        for site in session.scalars(stmt).yield_per(200):
            row = export_row(site)
            sheet.append([row[column] for column in EXPORT_COLUMNS])
    buffer = io.BytesIO()
    book.save(buffer)
    buffer.seek(0)
    while True:
        chunk = buffer.read(64 * 1024)
        if not chunk:
            break
        yield chunk
