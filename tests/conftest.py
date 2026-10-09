"""Shared fixtures: isolated temp database and a Website factory."""

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import app.database as database
from app.models import Base, Website


@pytest.fixture(autouse=True)
def db(tmp_path, monkeypatch):
    """Every test gets its own empty SQLite file — the real data/ DB is never touched."""
    engine = create_engine(f"sqlite:///{tmp_path / 'test.db'}")
    Base.metadata.create_all(engine)
    monkeypatch.setattr(database, "engine", engine)
    monkeypatch.setattr(database, "SessionLocal", sessionmaker(bind=engine))
    monkeypatch.setenv("OBSERVE_ECHO", "0")
    monkeypatch.setenv("OBSERVE_LOG", str(tmp_path / "events.jsonl"))
    yield engine


@pytest.fixture()
def make_website(db):
    def _make(url: str, status: str = "pending", **extra) -> int:
        with database.get_session() as session:
            site = Website(
                original_url=url,
                normalized_website=url,
                status=status,
                **extra,
            )
            session.add(site)
            session.commit()
            return site.id

    return _make


@pytest.fixture()
def allow_hosts(monkeypatch):
    """Disable DNS/SSRF checks so mocked-HTTP tests stay offline and deterministic.

    The guard itself has its own dedicated tests in test_guard.py.
    """
    monkeypatch.setattr("app.crawler.fetcher.assert_public_url", lambda url: url)
