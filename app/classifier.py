"""Classify crawled websites. This process does not fetch pages.

Default model is a local Ollama model. Set AI_PROVIDER=gemini to use the
optional cloud model instead.
"""

import argparse
import json

from dotenv import load_dotenv
from sqlalchemy import select

from app.ai.pipeline import classify_text
from app.ai.provider import get_provider
from app.database import get_session, init_db
from app.models import Job, Website
from app.observe import log_event
from app.profile import replace_evidence
from app.recovery import utcnow

load_dotenv()

STATUS_CLASSIFIED = "classified"
STATUS_FAILED = "classify_failed"
STATUS_NEEDS_REVIEW = "needs_review"
STATUS_INSUFFICIENT = "insufficient_evidence"


def classify_site(site: Website, provider=None) -> None:
    outcome = classify_text(site.clean_text or "", site.pages_crawled or 0, provider or get_provider())
    site.company_name = outcome.company_name
    site.description = outcome.description
    site.business_type = outcome.business_type
    site.industry = outcome.industry
    site.niche = outcome.niche
    site.secondary_industry = outcome.secondary_industry
    site.sub_niche = outcome.sub_niche
    site.business_model = outcome.business_model
    site.geographic_markets = outcome.geographic_markets
    site.products = outcome.products
    site.confidence = outcome.confidence
    quotes = replace_evidence(site, outcome.evidence, outcome.confidence)
    site.evidence = json.dumps(quotes, ensure_ascii=False)
    site.classification_meta = json.dumps(outcome.meta, ensure_ascii=False)
    site.classification_status = outcome.status
    site.status = outcome.status
    if site.clean_text and not site.crawl_status:
        site.crawl_status = "crawled"


def _open_classify_job(total: int) -> int:
    with get_session() as session:
        job = Job(kind="classify", status="running", added=total)
        session.add(job)
        session.commit()
        return job.id


def _close_classify_job(job_id: int, status: str, done: int, failed: int) -> None:
    with get_session() as session:
        job = session.get(Job, job_id)
        if job is None:
            return
        job.status = status
        job.added = done
        job.invalid = failed
        job.finished_at = utcnow()
        job.note = f"classified={done}, failed={failed}"
        session.commit()


def run(limit: int | None = None, only_id: int | None = None, retry_failed: bool = False) -> None:
    init_db()
    provider = get_provider()
    skip = {STATUS_CLASSIFIED, STATUS_NEEDS_REVIEW, STATUS_INSUFFICIENT}
    if not retry_failed:
        skip.add(STATUS_FAILED)

    done = failed = 0
    job_id = None
    try:
        with get_session() as session:
            query = select(Website).where(Website.clean_text.is_not(None), Website.clean_text != "")
            if only_id is not None:
                query = query.where(Website.id == only_id)
            query = query.order_by(Website.id)

            sites = [site for site in session.scalars(query) if site.status not in skip]
            if limit:
                sites = sites[:limit]
            print(f"{len(sites)} website(s) to classify with {provider.name}:{provider.model}")
            if sites:
                job_id = _open_classify_job(len(sites))
                log_event("classify_started", sites=len(sites), provider=provider.name, model=provider.model, job_id=job_id)

            for site in sites:
                try:
                    classify_site(site, provider)
                    done += 1
                    print(
                        f"  OK   #{site.id} {site.normalized_website} -> "
                        f"{site.industry} ({site.confidence}%) [{site.status}]"
                    )
                except RuntimeError as error:
                    if "GEMINI_API_KEY" in str(error):
                        raise
                    site.status = STATUS_FAILED
                    failed += 1
                    print(f"  FAIL #{site.id} {site.normalized_website}: {error}")
                    log_event("classify_failed", site_id=site.id, error=str(error))
                session.commit()
        if job_id is not None:
            _close_classify_job(job_id, "completed", done, failed)
            log_event("classify_finished", classified=done, failed=failed, job_id=job_id)
            job_id = None
    finally:
        if job_id is not None:
            _close_classify_job(job_id, "failed", done, failed)

    print(f"Finished. Classified: {done}, failed: {failed}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Classify websites using their clean_text.")
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--id", type=int, default=None, dest="only_id")
    parser.add_argument("--retry-failed", action="store_true")
    args = parser.parse_args()
    run(args.limit, args.only_id, args.retry_failed)
