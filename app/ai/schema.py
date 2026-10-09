"""Versioned shapes for fact extraction and taxonomy mapping."""

from __future__ import annotations

from pydantic import BaseModel, Field

SCHEMA_VERSION = "1"


class EvidenceItem(BaseModel):
    field: str = ""
    quote: str = ""
    page: str = ""
    explicit: bool = True


class BusinessFacts(BaseModel):
    """Stage A. Values the model claims to have read, before taxonomy mapping."""

    company_name: str | None = None
    business_description: str | None = None
    business_model: list[str] = Field(default_factory=list)
    industries: list[str] = Field(default_factory=list)
    niches: list[str] = Field(default_factory=list)
    products_services: list[str] = Field(default_factory=list)
    target_customers: list[str] = Field(default_factory=list)
    geographic_markets: list[str] = Field(default_factory=list)
    evidence: list[EvidenceItem] = Field(default_factory=list)
    insufficient_evidence: bool = True


class TaxonomyMapping(BaseModel):
    """Stage B. Ids must already exist in app.taxonomy. New labels stay suggestions."""

    business_type_id: str = "bt-unknown"
    industry_id: str = "ind-other"
    customer_type_id: str = "ct-unknown"
    niche: str = ""
    suggested_category: str | None = None
