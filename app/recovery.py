"""Return interrupted crawl rows and jobs to a state a new run can continue.

A website left in `crawling` with no claim time, or a claim older than
CRAWL_STUCK_MINUTES, is put back to `pending`. Crawl and classify jobs left
`running` past that same window are marked failed. Process startup resets
every in-progress claim, because this process does not own them.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.limits import stuck_minutes
from app.models import Job, Website

ACTIVE_KINDS = ("crawl", "classify")


def utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _fail_jobs(session: Session, jobs: list[Job], now: datetime, note: str) -> int:
    for job in jobs:
        job.status = "failed"
        job.finished_at = now
        job.note = note
    return len(jobs)


def release_stale_claims(session: Session, now: datetime | None = None) -> dict[str, int]:
    """Reset claims that are old enough that their worker is no longer running."""
    now = now or utcnow()
    cutoff = now - timedelta(minutes=stuck_minutes())
    sites = list(
        session.scalars(
            select(Website).where(
                Website.status == "crawling",
                or_(Website.claimed_at.is_(None), Website.claimed_at < cutoff),
            )
        )
    )
    for site in sites:
        site.status = "pending"
        site.claimed_at = None
    jobs = list(
        session.scalars(
            select(Job).where(
                Job.kind.in_(ACTIVE_KINDS),
                Job.status == "running",
                Job.created_at < cutoff,
            )
        )
    )
    failed = _fail_jobs(session, jobs, now, "Interrupted. Stuck website rows were returned to the queue.")
    return {"websites_reset": len(sites), "jobs_failed": failed}


def recover_abandoned(now: datetime | None = None) -> dict[str, int]:
    """Reset every in-progress claim. Call this when a process starts."""
    from app.database import get_session

    now = now or utcnow()
    with get_session() as session:
        sites = list(session.scalars(select(Website).where(Website.status == "crawling")))
        for site in sites:
            site.status = "pending"
            site.claimed_at = None
        jobs = list(
            session.scalars(
                select(Job).where(Job.kind.in_(ACTIVE_KINDS), Job.status == "running")
            )
        )
        failed = _fail_jobs(
            session,
            jobs,
            now,
            "Interrupted by a restart. Website rows were returned to the queue.",
        )
        session.commit()
        return {"websites_reset": len(sites), "jobs_failed": failed}
