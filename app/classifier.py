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
from app.models import Website

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
    site.products = outcome.products
    site.confidence = outcome.confidence
    site.evidence = json.dumps(outcome.evidence, ensure_ascii=False)
    site.classification_meta = json.dumps(outcome.meta, ensure_ascii=False)
    site.status = outcome.status


def run(limit: int | None = None, only_id: int | None = None, retry_failed: bool = False) -> None:
    init_db()
    provider = get_provider()
    skip = {STATUS_CLASSIFIED, STATUS_NEEDS_REVIEW, STATUS_INSUFFICIENT}
    if not retry_failed:
        skip.add(STATUS_FAILED)

    done = failed = 0
    with get_session() as session:
        query = select(Website).where(Website.clean_text.is_not(None), Website.clean_text != "")
        if only_id is not None:
            query = query.where(Website.id == only_id)
        query = query.order_by(Website.id)

        sites = [site for site in session.scalars(query) if site.status not in skip]
        if limit:
            sites = sites[:limit]
        print(f"{len(sites)} website(s) to classify with {provider.name}:{provider.model}")

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
            session.commit()

    print(f"Finished. Classified: {done}, failed: {failed}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Classify websites using their clean_text.")
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--id", type=int, default=None, dest="only_id")
    parser.add_argument("--retry-failed", action="store_true")
    args = parser.parse_args()
    run(args.limit, args.only_id, args.retry_failed)
