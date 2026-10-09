"""Confidence version 1.

The model's own score is not the result. Points come from grounded quotes,
how many pages were read, filled fields, valid taxonomy ids, and whether
those quotes come from more than one page. Caps apply when evidence is
missing or the text is short.
"""

from __future__ import annotations

from app.ai.schema import BusinessFacts, TaxonomyMapping

CONFIDENCE_VERSION = "1"
HIGH_CONFIDENCE = 80
REVIEW_CONFIDENCE = 50
MIN_GOOD_TEXT = 300


def calculate_confidence(
    facts: BusinessFacts,
    mapping: TaxonomyMapping,
    source_text: str,
    pages_crawled: int,
    *,
    taxonomy_ok: bool,
) -> tuple[int, dict]:
    parts: dict[str, int] = {}
    grounded = [item for item in facts.evidence if item.quote.strip()]
    parts["evidence"] = min(40, 10 * len(grounded))
    parts["pages"] = min(20, 5 * max(pages_crawled, 0))
    filled = sum(
        1
        for present in (
            facts.company_name,
            facts.business_description,
            mapping.industry_id not in ("", "ind-other"),
            mapping.business_type_id not in ("", "bt-unknown"),
            facts.products_services,
        )
        if present
    )
    parts["completeness"] = filled * 6
    parts["taxonomy"] = 10 if taxonomy_ok else 0
    pages = {item.page.strip() for item in grounded if item.page.strip()}
    parts["page_agreement"] = 5 if len(pages) >= 2 else 0
    if any(not item.explicit for item in grounded):
        parts["inferred_penalty"] = -10
    score = sum(parts.values())
    if facts.insufficient_evidence or not grounded:
        score = min(score, 45)
        parts["insufficient_cap"] = 45
    if len(source_text.strip()) < MIN_GOOD_TEXT:
        score = min(score, 55)
        parts["short_text_cap"] = 55
    score = max(0, min(100, score))
    return score, parts


def status_for(score: int, insufficient: bool) -> str:
    if insufficient or score < REVIEW_CONFIDENCE:
        return "insufficient_evidence"
    if score >= HIGH_CONFIDENCE:
        return "classified"
    return "needs_review"
