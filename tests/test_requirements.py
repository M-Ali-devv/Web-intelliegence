"""Remaining document fields: split status, hash, evidence rows, filters, cancel."""

from sqlalchemy import select

from app.ai.pipeline import ClassificationOutcome
from app.classifier import classify_site
from app.crawler import config
from app.crawler.pipeline import CrawlResult
from app.crawler.runner import _save
import app.database as database
from app.database import get_session
from app.models import DomainStat, EvidenceRecord, Job, Website
from app.profile import content_hash
from fastapi.testclient import TestClient

from app.main import app


def test_contact_url_changes_the_user_agent(monkeypatch):
    monkeypatch.setenv("CRAWLER_CONTACT_URL", "mailto:crawler@example.com")

    assert config.user_agent() == "WebsiteIntelligenceBot/1.0 (+mailto:crawler@example.com)"


def test_unchanged_crawl_keeps_the_classification(make_website):
    text = "ACME builds robots for factories."
    site_id = make_website(
        "https://acme.test",
        status="classified",
        classification_status="classified",
        crawl_status="crawled",
        clean_text=text,
        content_hash=content_hash(text),
        company_name="ACME",
    )

    _save(site_id, CrawlResult("crawled", text, None, 1, "https://acme.test", 20))

    with get_session() as session:
        site = session.get(Website, site_id)
        stat = session.scalar(select(DomainStat).where(DomainStat.domain == "acme.test"))
        assert site.status == "classified"
        assert site.company_name == "ACME"
        assert site.crawl_status == "crawled"
        assert stat.success_count == 1
        assert stat.last_latency_ms == 20
        assert stat.last_success_at is not None


def test_changed_crawl_clears_the_classification(make_website):
    site_id = make_website(
        "https://acme.test",
        status="classified",
        classification_status="classified",
        clean_text="Old page",
        content_hash=content_hash("Old page"),
        company_name="ACME",
        confidence=90,
    )

    _save(site_id, CrawlResult("crawled", "New page about robots.", None, 1))

    with get_session() as session:
        site = session.get(Website, site_id)
        assert site.status == "crawled"
        assert site.company_name is None
        assert site.classification_status is None
        assert site.confidence is None
        assert site.content_hash == content_hash("New page about robots.")


def test_classify_site_stores_evidence_and_extra_fields(make_website):
    site_id = make_website(
        "https://acme.test",
        status="crawled",
        crawl_status="crawled",
        clean_text=(
            "=== PAGE: about | https://acme.test/about\n"
            "Retrieved via http: https://acme.test/about\n"
            "We build robots."
        ),
    )
    outcome = ClassificationOutcome(
        company_name="ACME",
        description="Builds robots.",
        business_type="Product",
        industry="Manufacturing / Industrial",
        niche="factory robots",
        products="robots",
        confidence=82,
        evidence=[{"field": "industry", "quote": "We build robots.", "page": "about", "explicit": True}],
        status="classified",
        meta={"business_model": ["SaaS"]},
        secondary_industry="Payments",
        sub_niche="warehouse picking",
        business_model="SaaS",
        geographic_markets="United States",
    )

    with get_session() as session:
        site = session.get(Website, site_id)
        site_id = site.id
        from app import classifier

        original = classifier.classify_text
        classifier.classify_text = lambda *args, **kwargs: outcome
        try:
            classify_site(site, provider=object())
        finally:
            classifier.classify_text = original
        session.commit()

    with get_session() as session:
        site = session.get(Website, site_id)
        row = session.scalars(select(EvidenceRecord).where(EvidenceRecord.website_id == site_id)).one()
        assert site.secondary_industry == "Payments"
        assert site.sub_niche == "warehouse picking"
        assert site.business_model == "SaaS"
        assert site.geographic_markets == "United States"
        assert site.classification_status == "classified"
        assert site.crawl_status == "crawled"
        assert row.source_url == "https://acme.test/about"
        assert row.method == "http"
        assert row.quality == "explicit"
        assert row.quote == "We build robots."


def test_filters_match_business_model_and_geography(make_website, client=None):
    make_website(
        "https://acme.test",
        status="classified",
        company_name="ACME",
        business_model="SaaS",
        geographic_markets="United States",
    )
    make_website(
        "https://other.test",
        status="classified",
        company_name="Other",
        business_model="Marketplace",
        geographic_markets="Germany",
    )
    client = TestClient(app)
    response = client.get("/", params={"business_model": "saas", "geography": "united"})

    assert "https://acme.test" in response.text
    assert "https://other.test" not in response.text
    assert "Business model" in response.text
    assert "Geography" in response.text


def test_cancel_marks_a_running_job():
    with get_session() as session:
        job = Job(kind="crawl", status="running", added=2)
        session.add(job)
        session.commit()
        job_id = job.id

    response = TestClient(app).post(f"/jobs/{job_id}/cancel", follow_redirects=True)

    assert response.status_code == 200
    assert "Job cancelled" in response.text
    with get_session() as session:
        saved = session.get(Job, job_id)
        assert saved.status == "cancelled"
        assert saved.finished_at is not None


def test_backfill_splits_a_combined_status(make_website):
    site_id = make_website(
        "https://acme.test",
        status="needs_review",
        clean_text="Some saved text.",
    )

    with database.engine.begin() as connection:
        database._backfill_statuses(connection)

    with get_session() as session:
        site = session.get(Website, site_id)
        assert site.classification_status == "needs_review"
        assert site.crawl_status == "crawled"
        assert site.content_hash == content_hash("Some saved text.")
