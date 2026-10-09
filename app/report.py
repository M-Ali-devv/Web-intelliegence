"""Measured pilot figures from the rows already saved.

Rates are omitted when the denominator is zero. Nothing here is a forecast
for 60,000 websites, and production_ready stays false.
"""

from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import Website

UNFINISHED = ("pending", "crawling")
PILOT_TARGET = 100
EXPAND_TARGET = 1000


def _count(session: Session, *filters) -> int:
    stmt = select(func.count()).select_from(Website)
    for item in filters:
        stmt = stmt.where(item)
    return session.scalar(stmt) or 0


def _rate(numerator: int, denominator: int) -> float | None:
    if denominator == 0:
        return None
    return round(numerator / denominator, 3)


def scale_plan(websites: int) -> dict:
    if websites < PILOT_TARGET:
        stage = "pilot"
        next_step = (
            f"{websites} websites are saved. Stay on the pilot until about "
            f"{PILOT_TARGET} representative sites have been crawled and a person "
            "has checked classification accuracy and evidence."
        )
    elif websites < EXPAND_TARGET:
        stage = "expand"
        next_step = (
            "The saved set is past the first pilot size. Review the measured "
            "rates before raising concurrency. Do not treat this as a 60,000-site result."
        )
    else:
        stage = "controlled"
        next_step = (
            "Increase in controlled batches while watching block rates and errors. "
            "This report does not establish a runtime for 60,000 websites."
        )
    return {"stage": stage, "production_ready": False, "next_step": next_step}


def build_report(session: Session) -> dict:
    total = _count(session)
    finished = _count(session, Website.status.notin_(UNFINISHED))
    useful = _count(
        session,
        Website.status.notin_(UNFINISHED),
        Website.clean_text.is_not(None),
        Website.clean_text != "",
    )
    classified = _count(session, Website.status == "classified")
    needs_review = _count(session, Website.status == "needs_review")
    insufficient = _count(session, Website.status == "insufficient_evidence")
    classify_failed = _count(session, Website.status == "classify_failed")
    classified_attempted = classified + needs_review + insufficient + classify_failed
    browser_sites = _count(session, Website.clean_text.contains("Retrieved via browser:"))
    average = session.scalar(select(func.avg(Website.confidence)).where(Website.confidence.is_not(None)))
    counts = dict(
        session.execute(
            select(Website.status, func.count()).group_by(Website.status)
        ).all()
    )
    return {
        "websites": total,
        "by_status": counts,
        "crawl_finished": finished,
        "useful_text": useful,
        "crawl_success_rate": _rate(useful, finished),
        "browser_fallback_sites": browser_sites,
        "classification_attempted": classified_attempted,
        "insufficient_evidence_rate": _rate(insufficient, classified_attempted),
        "needs_review_rate": _rate(needs_review, classified_attempted),
        "average_confidence": None if average is None else round(float(average), 1),
        "scale": scale_plan(total),
    }
