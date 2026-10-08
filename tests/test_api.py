"""Web layer: import, crawl endpoints, page rendering, end-to-end via UI routes."""

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.database import get_session
from app.main import app
from app.models import Website
from app.crawler.pipeline import CrawlResult
from app.crawler.runner import RunSummary


@pytest.fixture()
def client():
    return TestClient(app)  # no context manager: lifespan/init_db not needed (db fixture)


def rows() -> list[Website]:
    with get_session() as session:
        sites = list(session.scalars(select(Website).order_by(Website.id)))
        session.expunge_all()
        return sites


# --- page rendering --------------------------------------------------------


def test_home_renders_empty_state_with_buttons(client):
    response = client.get("/")
    assert response.status_code == 200
    assert "Save the URL list" in response.text
    assert "Crawl pending websites" in response.text
    assert "No websites yet" in response.text


def test_home_shows_rows_preview_and_error_tooltip(make_website, client):
    site_id = make_website("https://shown.test", status="crawled",
                           clean_text="CLEAN BODY TEXT " * 30)
    make_website("https://pending.test")  # clean_text stays NULL

    response = client.get("/")

    assert "https://shown.test" in response.text
    assert "crawled" in response.text
    assert "CLEAN BODY TEXT" in response.text            # 200-char preview
    assert f"… {len('CLEAN BODY TEXT ' * 30)} chars" in response.text
    assert f'action="/crawl/{site_id}"' in response.text  # per-row re-crawl
    assert "empty" in response.text                       # NULL clean_text row


def test_home_shows_crawl_error_on_status_badge(make_website, client):
    make_website("https://blocked.test", status="blocked",
                 crawl_error="HTTP 403 from https://blocked.test")

    response = client.get("/")

    assert "blocked" in response.text
    assert "HTTP 403 from https://blocked.test" in response.text


def test_crawl_notice_shown_after_trigger(client):
    response = client.get("/", params={"crawl": "1"})
    assert "Crawl run started in the background" in response.text


# --- import endpoint -------------------------------------------------------


def test_import_endpoint_adds_rows(client):
    response = client.post(
        "/import",
        data={"urls": "https://alpha.test\nhttps://beta.test", "source_batch": "team"},
        follow_redirects=True,
    )

    assert "Added 2" in response.text
    saved = rows()
    assert len(saved) == 2
    assert all(site.status == "pending" for site in saved)
    assert all(site.source_batch == "team" for site in saved)


def test_import_endpoint_reports_duplicates_and_invalid(client):
    payload = {"urls": "https://alpha.test\nnot-a-website"}
    client.post("/import", data=payload, follow_redirects=True)
    response = client.post("/import", data=payload, follow_redirects=True)

    assert "Added 0" in response.text
    assert "Skipped 1 duplicates" in response.text
    assert "Rejected 1" in response.text


def test_import_sample_button(client):
    response = client.post("/import-sample", follow_redirects=True)
    # sample file: 4 unique sites, 1 in-file duplicate, 1 invalid line
    assert "Added 4" in response.text
    assert "Skipped 1 duplicates" in response.text
    assert "Rejected 1" in response.text


# --- crawl endpoints -------------------------------------------------------


def test_crawl_button_starts_runner_with_limit(monkeypatch, client):
    captured = {}

    async def fake_run(**kwargs):
        captured.update(kwargs)
        return RunSummary(total=0)

    monkeypatch.setattr("app.main.run_crawl", fake_run)

    response = client.post("/crawl", data={"limit": "5"}, follow_redirects=True)

    assert "Crawl run started in the background" in response.text
    assert captured == {"limit": 5, "site_ids": None, "force": False}


def test_crawl_button_without_limit_crawls_everything(monkeypatch, client):
    captured = {}

    async def fake_run(**kwargs):
        captured.update(kwargs)
        return RunSummary(total=0)

    monkeypatch.setattr("app.main.run_crawl", fake_run)

    client.post("/crawl", follow_redirects=True)

    assert captured == {"limit": None, "site_ids": None, "force": False}


def test_re_crawl_single_row_targets_only_that_id(monkeypatch, client):
    captured = {}

    async def fake_run(**kwargs):
        captured.update(kwargs)
        return RunSummary(total=1)

    monkeypatch.setattr("app.main.run_crawl", fake_run)

    response = client.post("/crawl/7", follow_redirects=True)

    assert response.status_code == 200
    assert captured == {"limit": None, "site_ids": [7], "force": True}


def test_crawl_button_full_flow_fills_clean_text(make_website, monkeypatch, client):
    """UI click → background runner → DB row → refreshed page shows the text."""
    site_id = make_website("https://e2e.test")
    profile = "=== PAGE: homepage | https://e2e.test\nHello from the crawler"

    async def fake_crawl(_client, _url, _cache):
        return CrawlResult("crawled", profile, None, 1)

    monkeypatch.setattr("app.crawler.runner.crawl_one", fake_crawl)

    client.post("/crawl", follow_redirects=True)   # background task runs for real

    saved = rows()[0]
    assert saved.id == site_id
    assert saved.status == "crawled"
    assert saved.clean_text == profile
    assert saved.last_crawled_at is not None

    page = client.get("/")
    assert "Hello from the crawler" in page.text   # preview visible after refresh
