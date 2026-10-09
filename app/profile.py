"""Crawl bookkeeping that sits beside the company row.

A content hash decides whether a new fetch should clear the classification.
Quotes are stored as evidence rows. Domain timing is stored per hostname.
"""

from __future__ import annotations

import hashlib
import re
from datetime import datetime
from urllib.parse import urlparse

from sqlalchemy import delete, select
from sqlalchemy.orm import object_session

from app.crawler.pipeline import CrawlResult
from app.database import get_session
from app.models import DomainStat, EvidenceRecord, Website
from app.recovery import utcnow

_PAGE = re.compile(r"^=== PAGE: (.*?) \| (\S+)\s*$", re.MULTILINE)
_METHOD = re.compile(r"Retrieved via (http|browser):")


def content_hash(text: str | None) -> str | None:
    if not text:
        return None
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def page_sources(clean_text: str | None) -> dict[str, tuple[str, str]]:
    """Map a page label to (url, retrieval method)."""
    if not clean_text:
        return {}
    matches = list(_PAGE.finditer(clean_text))
    found: dict[str, tuple[str, str]] = {}
    for index, match in enumerate(matches):
        end = matches[index + 1].start() if index + 1 < len(matches) else len(clean_text)
        section = clean_text[match.end() : end]
        method_match = _METHOD.search(section)
        method = method_match.group(1) if method_match else ""
        found[match.group(1).strip().casefold()] = (match.group(2), method)
    return found


def clear_classification(site: Website) -> None:
    site.classification_status = None
    site.company_name = None
    site.description = None
    site.business_type = None
    site.industry = None
    site.secondary_industry = None
    site.niche = None
    site.sub_niche = None
    site.products = None
    site.confidence = None
    site.evidence = None
    site.classification_meta = None
    site.business_model = None
    site.geographic_markets = None
    session = object_session(site)
    if session is not None and site.id is not None:
        session.execute(delete(EvidenceRecord).where(EvidenceRecord.website_id == site.id))


def apply_crawl_result(site: Website, result: CrawlResult, now: datetime | None = None) -> None:
    """Write crawl fields. Keep a classification when the text did not change."""
    now = now or utcnow()
    new_hash = content_hash(result.clean_text)
    previous = site.content_hash or content_hash(site.clean_text)
    changed = bool(previous and new_hash and previous != new_hash)
    site.crawl_status = result.status
    site.crawl_error = result.error
    site.pages_crawled = result.pages_crawled
    site.last_crawled_at = now
    site.claimed_at = None
    if result.clean_text:
        site.clean_text = result.clean_text
        site.content_hash = new_hash
        if changed:
            clear_classification(site)
            site.status = result.status
        elif site.classification_status:
            site.status = site.classification_status
        else:
            site.status = result.status
        return
    site.status = result.status


def record_domain(website: str, result: CrawlResult, now: datetime | None = None) -> None:
    host = (urlparse(website).hostname or "").lower().removeprefix("www.")
    if not host:
        return
    now = now or utcnow()
    ok = result.status == "crawled"
    with get_session() as session:
        stat = session.scalar(select(DomainStat).where(DomainStat.domain == host))
        if stat is None:
            stat = DomainStat(domain=host, success_count=0, failure_count=0)
            session.add(stat)
        stat.last_status = result.status
        if result.latency_ms is not None:
            stat.last_latency_ms = result.latency_ms
        if ok:
            stat.success_count += 1
            stat.last_success_at = now
        else:
            stat.failure_count += 1
        session.commit()


def replace_evidence(site: Website, items: list[dict], confidence: int | None, when: datetime | None = None) -> list[dict]:
    """Replace quote rows for this website and return items enriched with source and method."""
    when = when or utcnow()
    sources = page_sources(site.clean_text)
    enriched: list[dict] = []
    session = object_session(site)
    if session is not None and site.id is not None:
        session.execute(delete(EvidenceRecord).where(EvidenceRecord.website_id == site.id))
    for item in items:
        if not isinstance(item, dict) or not item.get("quote"):
            continue
        page = str(item.get("page") or "")
        source_url, method = sources.get(page.strip().casefold(), ("", ""))
        quality = "explicit" if item.get("explicit", True) else "inferred"
        stored = dict(item)
        stored["source_url"] = source_url
        stored["method"] = method
        stored["quality"] = quality
        enriched.append(stored)
        if session is not None and site.id is not None:
            session.add(
                EvidenceRecord(
                    website_id=site.id,
                    field=str(item.get("field") or "")[:64],
                    quote=str(item.get("quote")),
                    page=page[:80],
                    source_url=source_url[:2048],
                    method=method[:32],
                    quality=quality,
                    confidence=confidence,
                    retrieved_at=when,
                )
            )
    return enriched
