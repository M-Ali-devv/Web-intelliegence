"""Phase 1: normalize, dedupe, reject unsafe targets, and persist an import job."""

import io

import pytest
from fastapi.testclient import TestClient
from openpyxl import Workbook
from sqlalchemy import select

from app.database import get_session
from app.importer import excel_to_text, import_urls
from app.main import app
from app.models import Job, Website
from app.normalize import normalize_website


@pytest.fixture()
def client():
    return TestClient(app)


def test_normalize_collapses_the_same_company():
    assert normalize_website("https://www.Stripe.com/about") == "https://stripe.com"
    assert normalize_website("http://stripe.com/") == "https://stripe.com"
    assert normalize_website("stripe.com") == "https://stripe.com"


def test_normalize_rejects_non_websites():
    assert normalize_website("not a website") is None
    assert normalize_website("# comment") is None
    assert normalize_website("") is None


def test_import_dedupes_within_the_batch_and_the_database():
    with get_session() as session:
        first = import_urls(session, "https://alpha.test\nhttps://www.alpha.test/about\nhttps://beta.test")
    assert first.added == 2
    assert first.duplicates == ["https://alpha.test"]

    with get_session() as session:
        second = import_urls(session, "https://beta.test\nhttps://gamma.test")
    assert second.added == 1
    assert second.duplicates == ["https://beta.test"]


def test_import_rejects_private_addresses_and_does_not_store_them():
    with get_session() as session:
        result = import_urls(
            session,
            "https://ok.test\nhttp://127.0.0.1/admin\nhttp://169.254.169.254/latest\nhttp://10.0.0.5",
        )
    assert result.added == 1
    assert len(result.invalid) == 3

    with get_session() as session:
        saved = list(session.scalars(select(Website.normalized_website)))
    assert saved == ["https://ok.test"]


def test_import_creates_a_queued_job_linked_to_new_rows():
    with get_session() as session:
        result = import_urls(session, "https://alpha.test\nnot-a-website")
    assert result.added == 1
    assert result.invalid == ["not-a-website"]

    with get_session() as session:
        job = session.scalars(select(Job)).one()
        site = session.scalars(select(Website)).one()
        assert job.kind == "import"
        assert job.status == "queued"
        assert job.added == 1
        assert job.invalid == 1
        assert job.finished_at is not None
        assert site.job_id == job.id
        assert site.status == "pending"


def test_import_with_nothing_new_completes_the_job():
    with get_session() as session:
        import_urls(session, "https://alpha.test")
    with get_session() as session:
        result = import_urls(session, "https://alpha.test\nnope")
    assert result.added == 0

    with get_session() as session:
        jobs = list(session.scalars(select(Job).order_by(Job.id)))
    assert jobs[1].status == "completed"
    assert jobs[1].added == 0
    assert jobs[1].duplicates == 1
    assert jobs[1].invalid == 1


def test_excel_upload_is_read_as_urls(client):
    book = Workbook()
    sheet = book.active
    sheet.append(["website"])
    sheet.append(["https://from-excel.test"])
    buffer = io.BytesIO()
    book.save(buffer)

    response = client.post(
        "/import",
        files={"file": ("companies.xlsx", buffer.getvalue(), "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
        follow_redirects=True,
    )
    assert "Added 1" in response.text
    assert excel_to_text(buffer.getvalue()).splitlines()[0].lower().startswith("website")
