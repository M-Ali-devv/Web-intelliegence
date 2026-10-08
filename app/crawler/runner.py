"""Queue runner: claim pending rows, crawl them concurrently, write results.

All HTTP runs concurrently (Semaphore); database writes happen one at a
time in the event loop, which keeps SQLite happy.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone

import httpx
from sqlalchemy import select, update

from app.crawler import config
from app.crawler.fetcher import RobotsRules
from app.crawler.pipeline import CrawlResult, crawl_one
from app.database import get_session
from app.models import Website


@dataclass
class RunSummary:
    total: int = 0
    counts: dict[str, int] = field(default_factory=dict)
    seconds: float = 0.0


# Only one run at a time per process: the 'crawling' claim would otherwise
# be reset by a second run and rows would be crawled twice.
_RUNNING = False


def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _claim(site_ids: list[int] | None, limit: int | None, force: bool) -> list[tuple[int, str]]:
    """Mark target rows as 'crawling' so a second runner skips them.

    Returns plain (id, normalized_website) tuples — no ORM instances leave
    this function, so nothing can hit a detached-session error later.
    """
    with get_session() as session:
        # A crashed run must not leave rows stuck in 'crawling' forever.
        session.execute(
            update(Website)
            .where(Website.status == config.STATUS_CRAWLING)
            .values(status=config.STATUS_PENDING)
        )

        stmt = select(Website).order_by(Website.id)
        if site_ids:
            stmt = stmt.where(Website.id.in_(site_ids))
        elif not force:
            stmt = stmt.where(Website.status == config.STATUS_PENDING)
        if limit:
            stmt = stmt.limit(limit)

        claims: list[tuple[int, str]] = []
        for site in session.scalars(stmt):
            site.status = config.STATUS_CRAWLING
            claims.append((site.id, site.normalized_website))
        session.commit()
    return claims


def _save(site_id: int, result: CrawlResult) -> None:
    with get_session() as session:
        site = session.get(Website, site_id)
        if site is None:
            return
        site.status = result.status
        site.clean_text = result.clean_text
        site.crawl_error = result.error
        site.pages_crawled = result.pages_crawled
        site.last_crawled_at = _utcnow()
        session.commit()


async def run(
    limit: int | None = None,
    site_ids: list[int] | None = None,
    force: bool = False,
    concurrency: int | None = None,
    log=print,
) -> RunSummary:
    """Crawl pending websites and fill clean_text. Returns a summary."""
    global _RUNNING
    if _RUNNING:
        log("A crawl run is already active — wait for it to finish.")
        return RunSummary()

    started = time.monotonic()
    summary = RunSummary()
    _RUNNING = True
    try:
        return await _run_inside(limit, site_ids, force, concurrency, log, started, summary)
    finally:
        _RUNNING = False


async def _run_inside(
    limit: int | None,
    site_ids: list[int] | None,
    force: bool,
    concurrency: int | None,
    log,
    started: float,
    summary: RunSummary,
) -> RunSummary:
    targets = _claim(site_ids, limit, force)
    summary.total = len(targets)
    if not targets:
        log("Nothing to crawl.")
        return summary

    semaphore = asyncio.Semaphore(concurrency or config.CONCURRENCY)
    robots_cache: dict[str, RobotsRules] = {}

    async with httpx.AsyncClient() as client:
        async def worker(site_id: int, normalized_url: str) -> tuple[int, CrawlResult]:
            async with semaphore:
                try:
                    result = await crawl_one(client, normalized_url, robots_cache)
                except Exception as exc:  # noqa: BLE001 — one site must never kill the batch
                    result = CrawlResult(
                        config.STATUS_FAILED, None, f"unexpected error: {exc}", 0
                    )
                return site_id, result

        tasks = [
            asyncio.create_task(worker(site_id, normalized_url))
            for site_id, normalized_url in targets
        ]
        done = 0
        for coro in asyncio.as_completed(tasks):
            site_id, result = await coro
            _save(site_id, result)
            summary.counts[result.status] = summary.counts.get(result.status, 0) + 1
            done += 1
            if done % 10 == 0 or done == len(tasks):
                log(f"  {done}/{len(tasks)} done — {result.status} (site #{site_id})")

    summary.seconds = round(time.monotonic() - started, 1)
    return summary
