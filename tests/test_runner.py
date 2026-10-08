"""Runner: queue claiming, result persistence, force/limit/id selection, guards."""

import asyncio

import pytest

from app.crawler import runner
from app.crawler.pipeline import CrawlResult
from app.database import get_session
from app.models import Website
from sqlalchemy import select


def quiet(**overrides):
    kwargs = {"log": lambda *a, **k: None}
    kwargs.update(overrides)
    return kwargs


def run_sync(**kwargs):
    return asyncio.run(runner.run(**quiet(**kwargs)))


def fake_crawl(result=None, error=None, delay=0.0):
    async def _fake(client, url, cache):
        if delay:
            await asyncio.sleep(delay)
        if error:
            raise error
        return result or CrawlResult("crawled", "CLEAN TEXT", None, 3)

    return _fake


def get_site(site_id) -> Website:
    with get_session() as session:
        site = session.get(Website, site_id)
        session.expunge(site)
        return site


def test_only_pending_rows_are_claimed(make_website, monkeypatch):
    pending = make_website("https://pending.test")
    failed = make_website("https://failed.test", status="failed")
    done = make_website("https://done.test", status="crawled")
    monkeypatch.setattr(runner, "crawl_one", fake_crawl())

    summary = run_sync()

    assert summary.total == 1
    assert summary.counts == {"crawled": 1}
    assert get_site(pending).status == "crawled"
    assert get_site(failed).status == "failed"      # untouched
    assert get_site(done).status == "crawled"       # untouched


def test_result_fields_persisted(make_website, monkeypatch):
    site_id = make_website("https://a.test")
    monkeypatch.setattr(runner, "crawl_one", fake_crawl())

    run_sync()

    site = get_site(site_id)
    assert site.status == "crawled"
    assert site.clean_text == "CLEAN TEXT"
    assert site.crawl_error is None
    assert site.pages_crawled == 3
    assert site.last_crawled_at is not None


def test_failure_result_persists_error(make_website, monkeypatch):
    site_id = make_website("https://a.test")
    monkeypatch.setattr(
        runner, "crawl_one",
        fake_crawl(result=CrawlResult("unreachable", None, "connection error", 0)),
    )

    run_sync()

    site = get_site(site_id)
    assert site.status == "unreachable"
    assert site.clean_text is None
    assert site.crawl_error == "connection error"


def test_stuck_crawling_rows_are_reset_and_processed(make_website, monkeypatch):
    site_id = make_website("https://stuck.test", status="crawling")
    monkeypatch.setattr(runner, "crawl_one", fake_crawl())

    summary = run_sync()

    assert summary.total == 1
    assert get_site(site_id).status == "crawled"


def test_limit_bounds_the_run(make_website, monkeypatch):
    for i in range(3):
        make_website(f"https://site{i}.test")
    monkeypatch.setattr(runner, "crawl_one", fake_crawl())

    summary = run_sync(limit=2)

    assert summary.total == 2
    with get_session() as session:
        pending = len(session.scalars(select(Website).where(Website.status == "pending")).all())
    assert pending == 1


def test_site_ids_select_specific_rows_even_when_not_pending(make_website, monkeypatch):
    site_id = make_website("https://retry.test", status="failed")
    other = make_website("https://other.test")
    monkeypatch.setattr(runner, "crawl_one", fake_crawl())

    summary = run_sync(site_ids=[site_id])

    assert summary.total == 1
    assert get_site(site_id).status == "crawled"
    assert get_site(other).status == "pending"


def test_force_crawls_every_status(make_website, monkeypatch):
    a = make_website("https://a.test")
    b = make_website("https://b.test", status="failed")
    c = make_website("https://c.test", status="crawled")
    monkeypatch.setattr(runner, "crawl_one", fake_crawl())

    summary = run_sync(force=True)

    assert summary.total == 3
    assert summary.counts == {"crawled": 3}
    for site_id in (a, b, c):
        assert get_site(site_id).status == "crawled"


def test_unexpected_exception_marks_row_failed(make_website, monkeypatch):
    site_id = make_website("https://boom.test")
    monkeypatch.setattr(runner, "crawl_one", fake_crawl(error=RuntimeError("kaput")))

    summary = run_sync()

    assert summary.counts == {"failed": 1}
    site = get_site(site_id)
    assert site.status == "failed"
    assert "unexpected error" in site.crawl_error
    assert "kaput" in site.crawl_error


def test_second_run_refused_while_first_is_active(make_website, monkeypatch):
    make_website("https://slow.test")
    monkeypatch.setattr(runner, "crawl_one", fake_crawl(delay=0.2))

    async def scenario():
        first = asyncio.create_task(runner.run(**quiet()))
        await asyncio.sleep(0.05)          # let the first run claim the lock
        second = await runner.run(**quiet())
        first_summary = await first
        return first_summary, second

    first_summary, second = asyncio.run(scenario())
    assert first_summary.total == 1
    assert second.total == 0                # refused without claiming anything


def test_save_for_unknown_row_is_a_no_op():
    runner._save(99999, CrawlResult("crawled", "text", None, 1))  # must not raise


def test_empty_queue_is_a_clean_no_op(make_website, monkeypatch):
    monkeypatch.setattr(runner, "crawl_one", fake_crawl())
    summary = run_sync()
    assert summary.total == 0
    assert summary.counts == {}
