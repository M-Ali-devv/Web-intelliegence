"""Phase 5: recovery, limits, indexes, backup, and measured reports."""

import json
import sqlite3
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, inspect, select
from sqlalchemy.orm import sessionmaker

import app.database as database
from app.backup import backup_database
from app.benchmark import measure
from app.classifier import run as classify_run
from app.crawler import runner
from app.crawler.pipeline import CrawlResult
from app.database import get_session
from app.main import app
from app.models import Job, Website
from app.observe import log_event
from app.recovery import recover_abandoned, utcnow
from app.report import build_report


@pytest.fixture()
def client():
    return TestClient(app)


def test_health_and_security_headers(client):
    response = client.get("/health")

    assert response.status_code == 200
    assert response.json()["ok"] is True
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["x-frame-options"] == "DENY"
    assert "frame-ancestors 'none'" in response.headers["content-security-policy"]


def test_oversized_upload_is_refused(client, monkeypatch):
    monkeypatch.setattr("app.main.max_upload_bytes", lambda: 8)

    response = client.post(
        "/import",
        data={"urls": ""},
        files={"file": ("urls.txt", b"https://too-big.test\n", "text/plain")},
        follow_redirects=True,
    )

    assert response.status_code == 200
    assert "upload limit" in response.text
    assert "Import stopped" in response.text
    with get_session() as session:
        assert session.scalars(select(Website)).first() is None


def test_log_redacts_secret_fields(tmp_path, monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "super-secret-value")
    monkeypatch.setenv("OBSERVE_LOG", str(tmp_path / "events.jsonl"))
    monkeypatch.setenv("OBSERVE_ECHO", "0")

    log_event("provider_error", api_key="super-secret-value", error="key super-secret-value leaked")

    line = (tmp_path / "events.jsonl").read_text(encoding="utf-8")
    assert "super-secret-value" not in line
    payload = json.loads(line)
    assert payload["api_key"] == "[redacted]"
    assert "[redacted]" in payload["error"]


def test_fresh_claim_is_not_stolen(make_website, monkeypatch):
    site_id = make_website("https://busy.test", status="crawling", claimed_at=utcnow())
    monkeypatch.setattr(runner, "crawl_one", _fake_crawl())

    summary = _run()

    assert summary.total == 0
    with get_session() as session:
        assert session.get(Website, site_id).status == "crawling"


def test_old_claim_returns_to_the_queue(make_website, monkeypatch):
    site_id = make_website(
        "https://stuck.test",
        status="crawling",
        claimed_at=utcnow() - timedelta(minutes=45),
    )
    monkeypatch.setattr(runner, "crawl_one", _fake_crawl())

    summary = _run()

    assert summary.total == 1
    with get_session() as session:
        site = session.get(Website, site_id)
        assert site.status == "crawled"
        assert site.claimed_at is None
        job = session.scalars(select(Job).where(Job.kind == "crawl")).one()
        assert job.status == "completed"
        assert job.added == 1


def test_restart_recovers_a_live_claim(make_website):
    site_id = make_website("https://busy.test", status="crawling", claimed_at=utcnow())
    with get_session() as session:
        session.add(Job(kind="crawl", status="running", added=1))
        session.commit()

    result = recover_abandoned()

    assert result == {"websites_reset": 1, "jobs_failed": 1}
    with get_session() as session:
        assert session.get(Website, site_id).status == "pending"
        assert session.scalars(select(Job)).one().status == "failed"


def test_report_uses_only_saved_rows(make_website):
    make_website(
        "https://ok.test",
        status="crawled",
        clean_text="Retrieved via browser: https://ok.test\nWe build robots.",
        confidence=80,
    )
    make_website("https://blocked.test", status="blocked")
    make_website("https://waiting.test", status="pending")
    make_website("https://unsure.test", status="needs_review", confidence=60, clean_text="Some text.")
    make_website("https://thin.test", status="insufficient_evidence", confidence=30, clean_text="Short.")

    with get_session() as session:
        report = build_report(session)

    assert report["websites"] == 5
    assert report["crawl_finished"] == 4
    assert report["useful_text"] == 3
    assert report["crawl_success_rate"] == 0.75
    assert report["browser_fallback_sites"] == 1
    assert report["classification_attempted"] == 2
    assert report["needs_review_rate"] == 0.5
    assert report["insufficient_evidence_rate"] == 0.5
    assert report["scale"]["stage"] == "pilot"
    assert report["scale"]["production_ready"] is False


def test_empty_report_does_not_invent_a_rate():
    with get_session() as session:
        report = build_report(session)

    assert report["websites"] == 0
    assert report["crawl_success_rate"] is None
    assert report["scale"]["production_ready"] is False


def test_benchmark_measures_the_rows_it_inserts():
    result = measure(20)

    assert result.rows == 20
    assert result.matched == 10
    assert result.filter_ms >= 0
    assert result.export_ms >= 0
    assert "60,000" in result.note


def test_backup_keeps_only_the_newest_copies(tmp_path):
    first = backup_database(directory=tmp_path, keep=2)
    second = backup_database(directory=tmp_path, keep=2)
    third = backup_database(directory=tmp_path, keep=2)

    kept = sorted(path.name for path in tmp_path.glob("websites-*.db"))
    assert first.name not in kept
    assert second.name in kept
    assert third.name in kept
    assert len(kept) == 2


def test_existing_database_gains_indexes_and_claim_column(tmp_path, monkeypatch):
    path = tmp_path / "old.db"
    raw = sqlite3.connect(path)
    raw.execute(
        "CREATE TABLE websites (id INTEGER PRIMARY KEY, original_url TEXT, normalized_website TEXT, status TEXT)"
    )
    raw.execute(
        "CREATE TABLE jobs (id INTEGER PRIMARY KEY, kind TEXT, status TEXT, added INTEGER, "
        "duplicates INTEGER, invalid INTEGER, created_at DATETIME, finished_at DATETIME)"
    )
    raw.commit()
    raw.close()

    engine = create_engine(f"sqlite:///{path}")
    monkeypatch.setattr(database, "engine", engine)
    monkeypatch.setattr(database, "SessionLocal", sessionmaker(bind=engine))
    database.init_db()

    columns = {column["name"] for column in inspect(engine).get_columns("websites")}
    indexes = {index["name"] for index in inspect(engine).get_indexes("websites")}
    job_columns = {column["name"] for column in inspect(engine).get_columns("jobs")}
    assert "claimed_at" in columns
    assert "classification_meta" in columns
    assert "ix_websites_status" in indexes
    assert "note" in job_columns


def test_classify_run_records_a_job(make_website, monkeypatch):
    make_website("https://acme.test", status="crawled", clean_text="ACME builds robots for factories worldwide.")

    class Fake:
        name = "script"
        model = "fake"

    def mark(site, provider=None):
        site.status = "classified"
        site.company_name = "ACME"

    monkeypatch.setattr("app.classifier.get_provider", lambda: Fake())
    monkeypatch.setattr("app.classifier.classify_site", mark)

    classify_run()

    with get_session() as session:
        job = session.scalars(select(Job).where(Job.kind == "classify")).one()
        assert job.status == "completed"
        assert job.added == 1


def _fake_crawl():
    async def _fake(client, url, cache):
        return CrawlResult("crawled", "CLEAN TEXT", None, 1)

    return _fake


def _run():
    import asyncio

    return asyncio.run(runner.run(log=lambda *args, **kwargs: None))
