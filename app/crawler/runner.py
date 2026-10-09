"""Queue runner: claim pending rows, crawl them concurrently, write results.

All HTTP runs concurrently (Semaphore); database writes happen one at a
time in the event loop, which keeps SQLite happy.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from datetime import datetime

import httpx
from sqlalchemy import select

from app.crawler import config
from app.crawler.fetcher import RobotsRules
from app.crawler.pipeline import CrawlResult, crawl_one
from app.database import get_session
from app.limits import crawl_concurrency
from app.models import Job, Website
from app.observe import log_event
from app.profile import apply_crawl_result, record_domain
from app.recovery import release_stale_claims, utcnow


@dataclass
class RunSummary:
    total: int = 0
    counts: dict[str, int] = field(default_factory=dict)
    seconds: float = 0.0


# Only one run at a time per process: the 'crawling' claim would otherwise
# be reset by a second run and rows would be crawled twice.
_RUNNING = False


def _utcnow() -> datetime:
    return utcnow()


def _claim(site_ids: list[int] | None, limit: int | None, force: bool) -> tuple[list[tuple[int, str]], int | None]:
    """Mark target rows as 'crawling' so a second runner skips them.

    Returns plain (id, normalized_website) tuples — no ORM instances leave
    this function, so nothing can hit a detached-session error later.
    A crawl job id is returned when at least one row was claimed.
    """
    with get_session() as session:
        # Rows with no claim time, or a claim older than the stuck window,
        # belong to a run that did not finish.
        release_stale_claims(session)

        stmt = select(Website).order_by(Website.id)
        if site_ids:
            stmt = stmt.where(Website.id.in_(site_ids))
        elif not force:
            stmt = stmt.where(Website.status == config.STATUS_PENDING)
        if limit:
            stmt = stmt.limit(limit)

        claimed_at = _utcnow()
        claims: list[tuple[int, str]] = []
        for site in session.scalars(stmt):
            site.status = config.STATUS_CRAWLING
            site.crawl_status = config.STATUS_CRAWLING
            site.claimed_at = claimed_at
            claims.append((site.id, site.normalized_website))
        job_id = None
        if claims:
            job = Job(kind="crawl", status="running", added=len(claims))
            session.add(job)
            session.flush()
            job_id = job.id
        session.commit()
    return claims, job_id


def _save(site_id: int, result: CrawlResult) -> None:
    website = ""
    with get_session() as session:
        site = session.get(Website, site_id)
        if site is None:
            return
        website = site.normalized_website
        apply_crawl_result(site, result, _utcnow())
        session.commit()
    if website:
        record_domain(website, result, _utcnow())


def _is_cancelled(job_id: int | None) -> bool:
    if job_id is None:
        return False
    with get_session() as session:
        job = session.get(Job, job_id)
        return job is not None and job.status == "cancelled"


def _close_job(job_id: int | None, status: str, summary: RunSummary, note: str = "") -> None:
    if job_id is None:
        return
    with get_session() as session:
        job = session.get(Job, job_id)
        if job is None:
            return
        job.status = status
        job.finished_at = _utcnow()
        job.added = summary.total
        job.note = note or ", ".join(f"{name}={count}" for name, count in sorted(summary.counts.items()))
        session.commit()


def _release_unfinished(site_ids: list[int]) -> None:
    if not site_ids:
        return
    with get_session() as session:
        sites = session.scalars(
            select(Website).where(Website.id.in_(site_ids), Website.status == config.STATUS_CRAWLING)
        )
        for site in sites:
            site.claimed_at = None
            if site.classification_status:
                site.status = site.classification_status
                site.crawl_status = site.crawl_status or config.STATUS_CRAWLED
            else:
                site.status = config.STATUS_PENDING
                site.crawl_status = config.STATUS_PENDING
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
    targets, job_id = _claim(site_ids, limit, force)
    summary.total = len(targets)
    if not targets:
        log("Nothing to crawl.")
        return summary

    log_event("crawl_started", sites=len(targets), job_id=job_id)
    closed = False
    cancelled = False
    try:
        semaphore = asyncio.Semaphore(concurrency or crawl_concurrency())
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
                if _is_cancelled(job_id):
                    cancelled = True
                    break
                site_id, result = await coro
                _save(site_id, result)
                summary.counts[result.status] = summary.counts.get(result.status, 0) + 1
                done += 1
                if done % 10 == 0 or done == len(tasks):
                    log(f"  {done}/{len(tasks)} done — {result.status} (site #{site_id})")
            if cancelled:
                for task in tasks:
                    task.cancel()
                await asyncio.gather(*tasks, return_exceptions=True)

        summary.seconds = round(time.monotonic() - started, 1)
        if cancelled:
            _close_job(job_id, "cancelled", summary, "Cancelled by an operator.")
            log_event("crawl_cancelled", sites=summary.total, job_id=job_id)
        else:
            _close_job(job_id, "completed", summary)
            log_event("crawl_finished", sites=summary.total, seconds=summary.seconds, counts=summary.counts, job_id=job_id)
        closed = True
        return summary
    finally:
        _release_unfinished([site_id for site_id, _url in targets])
        if not closed:
            summary.seconds = round(time.monotonic() - started, 1)
            _close_job(job_id, "failed", summary, "Crawl stopped before it finished.")
            log_event("crawl_failed", sites=summary.total, job_id=job_id)
