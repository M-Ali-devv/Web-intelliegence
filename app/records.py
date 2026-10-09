"""Search, review fields, and export rows for saved companies."""

from __future__ import annotations

import json
from datetime import datetime

from sqlalchemy import or_, select
from sqlalchemy.sql import Select

from app.models import Website

REVIEW_STATUSES = ("needs_review", "insufficient_evidence")
AI_STATUSES = {"classified", "needs_review", "insufficient_evidence", "classify_failed"}

EXPORT_COLUMNS = (
    "original_url",
    "normalized_domain",
    "company_name",
    "business_description",
    "business_type",
    "business_model",
    "industry",
    "niche",
    "products",
    "services",
    "target_markets",
    "geographic_markets",
    "confidence",
    "evidence",
    "crawl_status",
    "classification_status",
    "last_crawled_at",
    "error",
    "secondary_industry",
    "sub_niche",
)


def meta_of(site: Website) -> dict:
    if not site.classification_meta:
        return {}
    try:
        loaded = json.loads(site.classification_meta)
    except json.JSONDecodeError:
        return {}
    return loaded if isinstance(loaded, dict) else {}


def evidence_of(site: Website) -> list[dict]:
    if not site.evidence:
        return []
    try:
        loaded = json.loads(site.evidence)
    except json.JSONDecodeError:
        return []
    if not isinstance(loaded, list):
        return []
    items = []
    for item in loaded:
        if isinstance(item, dict) and item.get("quote"):
            items.append(item)
    return items


def _split_statuses(site: Website) -> tuple[str, str]:
    crawl = site.crawl_status or ""
    classification = site.classification_status or ""
    if crawl or classification:
        return crawl, classification
    if site.status in AI_STATUSES:
        crawl = "crawled" if site.clean_text else ""
        return crawl, site.status or ""
    return site.status or "", ""


def filtered_select(
    q: str = "",
    industry: str = "",
    business_type: str = "",
    status: str = "",
    min_confidence: int | None = None,
    business_model: str = "",
    geography: str = "",
) -> Select:
    stmt = select(Website)
    text = q.strip()
    if text:
        like = f"%{text}%"
        stmt = stmt.where(
            or_(
                Website.normalized_website.ilike(like),
                Website.company_name.ilike(like),
                Website.description.ilike(like),
                Website.niche.ilike(like),
                Website.products.ilike(like),
            )
        )
    if industry:
        stmt = stmt.where(Website.industry == industry)
    if business_type:
        stmt = stmt.where(Website.business_type == business_type)
    if status == "review":
        stmt = stmt.where(Website.status.in_(REVIEW_STATUSES))
    elif status:
        stmt = stmt.where(Website.status == status)
    if min_confidence is not None:
        stmt = stmt.where(Website.confidence >= min_confidence)
    if business_model.strip():
        stmt = stmt.where(Website.business_model.ilike(f"%{business_model.strip()}%"))
    if geography.strip():
        stmt = stmt.where(Website.geographic_markets.ilike(f"%{geography.strip()}%"))
    return stmt.order_by(Website.id.asc())


def export_row(site: Website) -> dict[str, str]:
    meta = meta_of(site)
    quotes = []
    for item in evidence_of(site):
        page = item.get("page") or ""
        quote = item.get("quote") or ""
        quotes.append(f"{page}: {quote}" if page else quote)
    crawled = site.last_crawled_at
    when = crawled.isoformat(sep=" ", timespec="seconds") if isinstance(crawled, datetime) else ""
    markets = meta.get("target_customers") or []
    places = meta.get("geographic_markets") or []
    models = meta.get("business_model") or []
    model_text = site.business_model or ("; ".join(models) if isinstance(models, list) else str(models or ""))
    place_text = site.geographic_markets or ("; ".join(places) if isinstance(places, list) else str(places or ""))
    crawl_status, classification_status = _split_statuses(site)
    return {
        "original_url": site.original_url or "",
        "normalized_domain": site.normalized_website or "",
        "company_name": site.company_name or "",
        "business_description": site.description or "",
        "business_type": site.business_type or "",
        "business_model": model_text,
        "industry": site.industry or "",
        "niche": site.niche or "",
        "products": site.products or "",
        "services": site.services or "",
        "target_markets": "; ".join(markets) if isinstance(markets, list) else str(markets or ""),
        "geographic_markets": place_text,
        "confidence": "" if site.confidence is None else str(site.confidence),
        "evidence": " | ".join(quotes),
        "crawl_status": crawl_status,
        "classification_status": classification_status,
        "last_crawled_at": when,
        "error": site.crawl_error or "",
        "secondary_industry": site.secondary_industry or "",
        "sub_niche": site.sub_niche or "",
    }
