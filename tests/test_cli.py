"""CLI argument forwarding."""

import sys

import pytest

import app.crawler_cli as cli
from app.crawler.config import CONCURRENCY
from app.crawler.runner import RunSummary


def capture_run(monkeypatch):
    captured = {}

    async def fake_run(**kwargs):
        captured.update(kwargs)
        return RunSummary(total=1, counts={"crawled": 1}, seconds=0.1)

    monkeypatch.setattr(cli, "run", fake_run)
    return captured


def test_cli_forwards_all_arguments(monkeypatch):
    captured = capture_run(monkeypatch)
    monkeypatch.setattr(
        sys, "argv",
        ["crawler", "--limit", "5", "--force", "--concurrency", "3", "--id", "2", "--id", "7"],
    )

    cli.main()

    assert captured["limit"] == 5
    assert captured["force"] is True
    assert captured["concurrency"] == 3
    assert captured["site_ids"] == [2, 7]


def test_cli_defaults(monkeypatch):
    captured = capture_run(monkeypatch)
    monkeypatch.setattr(sys, "argv", ["crawler"])

    cli.main()

    assert captured["limit"] is None
    assert captured["force"] is False
    assert captured["site_ids"] is None
    assert captured["concurrency"] == CONCURRENCY


def test_cli_prints_summary(monkeypatch, capsys):
    capture_run(monkeypatch)
    monkeypatch.setattr(sys, "argv", ["crawler"])

    cli.main()

    out = capsys.readouterr().out
    assert "Crawled 1 website(s)" in out
    assert "crawled" in out


@pytest.mark.filterwarnings("ignore::RuntimeWarning")
def test_module_run_guard_executes_main(monkeypatch):
    import runpy

    monkeypatch.setattr(sys, "argv", ["crawler", "--limit", "1"])
    runpy.run_module("app.crawler_cli", run_name="__main__")  # must not raise
