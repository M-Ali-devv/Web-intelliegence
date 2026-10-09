"""Command line entry point for the crawler.

Run from the project folder (with the virtualenv active):

    python -m app.crawler_cli                 # crawl pending rows
    python -m app.crawler_cli --limit 20      # first 20 pending rows
    python -m app.crawler_cli --id 3 --id 7   # specific rows
    python -m app.crawler_cli --force         # re-crawl regardless of status
"""

from __future__ import annotations

import argparse
import asyncio

from app.crawler import config
from app.crawler.runner import run
from app.database import init_db
from app.limits import crawl_concurrency
from app.recovery import recover_abandoned


def main() -> None:
    parser = argparse.ArgumentParser(description="Crawl pending websites into clean_text.")
    parser.add_argument("--limit", type=int, default=None, help="max number of rows to crawl")
    parser.add_argument("--id", dest="ids", action="append", type=int, default=None,
                        help="crawl a specific row (repeatable)")
    parser.add_argument("--force", action="store_true",
                        help="crawl rows regardless of their current status")
    parser.add_argument("--concurrency", type=int, default=None,
                        help=f"parallel websites (default {config.CONCURRENCY})")
    args = parser.parse_args()

    init_db()
    recovered = recover_abandoned()
    if recovered["websites_reset"] or recovered["jobs_failed"]:
        print(
            f"Recovered {recovered['websites_reset']} crawling website(s) "
            f"and {recovered['jobs_failed']} interrupted job(s)."
        )

    summary = asyncio.run(
        run(limit=args.limit, site_ids=args.ids, force=args.force,
            concurrency=args.concurrency or crawl_concurrency())
    )

    print(f"\nCrawled {summary.total} website(s) in {summary.seconds}s")
    for status, count in sorted(summary.counts.items()):
        print(f"  {status:<16} {count}")


if __name__ == "__main__":
    main()
