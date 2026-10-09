"""Phase 3: local-model pipeline, grounded evidence, and fixed taxonomy ids."""

import json

import httpx
import pytest

from app.ai.pipeline import classify_text
from app.ai.provider import GeminiProvider, OllamaProvider, get_provider
from app.classifier import classify_site
from app.database import get_session
from app.models import Website
from app.taxonomy import INDUSTRIES

SOURCE = """
=== PAGE: homepage | https://acme.test
ACME Robotics builds warehouse picking robots for factories.
=== PAGE: about | https://acme.test/about
Founded in 1998, ACME sells its robots to manufacturing companies worldwide.
The company employs nine hundred engineers and ships robots to plants in Europe and Asia.
"""

FACTS = {
    "company_name": "ACME Robotics",
    "business_description": "Builds warehouse picking robots.",
    "business_model": ["manufacturer"],
    "industries": ["robotics"],
    "niches": ["warehouse robotics"],
    "products_services": ["picking robots"],
    "target_customers": ["factories"],
    "geographic_markets": ["Europe", "Asia"],
    "evidence": [
        {
            "field": "description",
            "quote": "ACME Robotics builds warehouse picking robots for factories.",
            "page": "homepage",
            "explicit": True,
        },
        {
            "field": "customers",
            "quote": "ACME sells its robots to manufacturing companies worldwide.",
            "page": "about",
            "explicit": True,
        },
    ],
    "insufficient_evidence": False,
}

MAPPING = {
    "business_type_id": "bt-product",
    "industry_id": "ind-manufacturing",
    "customer_type_id": "ct-b2b",
    "niche": "warehouse robotics",
    "suggested_category": None,
}


class Scripted:
    name = "fake"
    model = "fake-model"

    def __init__(self, answers):
        self.answers = list(answers)
        self.calls = 0

    def complete(self, system, user):
        self.calls += 1
        answer = self.answers.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return answer


def test_default_provider_is_local_and_gemini_is_optional(monkeypatch):
    monkeypatch.setenv("AI_PROVIDER", "ollama")
    monkeypatch.setenv("OLLAMA_MODEL", "qwen2.5:7b-instruct")
    provider = get_provider()
    assert provider.name == "ollama"
    assert provider.model == "qwen2.5:7b-instruct"

    monkeypatch.setenv("AI_PROVIDER", "gemini")
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    assert isinstance(get_provider(), GeminiProvider)
    with pytest.raises(RuntimeError, match="GEMINI_API_KEY"):
        get_provider().complete("system", "user")


def test_ollama_asks_for_json(monkeypatch):
    captured = {}

    class Response:
        def raise_for_status(self):
            return None

        def json(self):
            return {"message": {"content": "{\"ok\": true}"}}

    def fake_post(url, json, timeout):
        captured["url"] = url
        captured["json"] = json
        return Response()

    monkeypatch.setattr(httpx, "post", fake_post)
    text = OllamaProvider(model="qwen2.5:7b-instruct", base_url="http://127.0.0.1:11434").complete("sys", "user")
    assert text == "{\"ok\": true}"
    assert captured["url"].endswith("/api/chat")
    assert captured["json"]["format"] == "json"
    assert captured["json"]["model"] == "qwen2.5:7b-instruct"


def test_grounded_quotes_map_onto_existing_categories():
    provider = Scripted([json.dumps(FACTS), json.dumps(MAPPING)])
    outcome = classify_text(SOURCE, pages_crawled=4, provider=provider)
    assert outcome.status == "classified"
    assert outcome.confidence >= 80
    assert outcome.industry == "Manufacturing / Industrial"
    assert outcome.business_type == "Product"
    assert outcome.company_name == "ACME Robotics"
    assert outcome.meta["provider"] == "fake"
    assert outcome.meta["schema_version"] == "1"
    assert outcome.meta["taxonomy_version"] == "1"
    assert len(outcome.evidence) == 2


def test_unknown_category_falls_back_and_suggestion_is_not_added():
    mapping = dict(MAPPING, industry_id="ind-space-mining", suggested_category="Space mining")
    provider = Scripted([json.dumps(FACTS), json.dumps(mapping)])
    outcome = classify_text(SOURCE, pages_crawled=4, provider=provider)
    assert outcome.industry == "Other"
    assert outcome.meta["suggested_category"] == "Space mining"
    assert "Space mining" not in INDUSTRIES
    assert outcome.meta["confidence_breakdown"]["taxonomy"] == 0


def test_quote_missing_from_the_page_is_dropped_and_marked_insufficient():
    facts = json.loads(json.dumps(FACTS))
    facts["evidence"] = [
        {"field": "description", "quote": "This sentence was never on the website.", "page": "homepage", "explicit": True}
    ]
    facts["insufficient_evidence"] = False
    provider = Scripted([json.dumps(facts), json.dumps(MAPPING)])
    outcome = classify_text(SOURCE, pages_crawled=4, provider=provider)
    assert outcome.evidence == []
    assert outcome.status == "insufficient_evidence"
    assert outcome.confidence <= 45
    assert outcome.meta["insufficient_evidence"] is True


def test_malformed_json_is_retried():
    provider = Scripted(["not json", json.dumps(FACTS), json.dumps(MAPPING)])
    outcome = classify_text(SOURCE, pages_crawled=4, provider=provider)
    assert provider.calls == 3
    assert outcome.company_name == "ACME Robotics"


def test_classify_site_saves_the_profile(monkeypatch, make_website):
    provider = Scripted([json.dumps(FACTS), json.dumps(MAPPING)])
    monkeypatch.setattr("app.classifier.get_provider", lambda: provider)
    site_id = make_website(
        "https://acme.test",
        status="crawled",
        clean_text=SOURCE,
        pages_crawled=4,
    )
    with get_session() as session:
        site = session.get(Website, site_id)
        classify_site(site)
        session.commit()
        session.refresh(site)
        assert site.status == "classified"
        assert site.industry == "Manufacturing / Industrial"
        assert site.products == "picking robots"
        meta = json.loads(site.classification_meta)
        assert meta["model"] == "fake-model"
        assert meta["customer_type"] == "B2B"
