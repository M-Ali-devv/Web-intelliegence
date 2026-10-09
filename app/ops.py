"""Operator commands: report, backup, recover, and a local benchmark.

    python -m app.ops report
    python -m app.ops backup
    python -m app.ops recover
    python -m app.ops benchmark
    python -m app.ops cancel --job 4
"""

from __future__ import annotations

import argparse
import json

from app.backup import backup_database
from app.benchmark import measure
from app.database import get_session, init_db
from app.models import Job
from app.recovery import recover_abandoned, utcnow
from app.report import build_report


def _print_report(report: dict) -> None:
    print(f"Websites saved: {report['websites']}")
    print(f"Crawl finished: {report['crawl_finished']}")
    print(f"With useful text: {report['useful_text']}")
    print(f"Crawl success rate: {report['crawl_success_rate']}")
    print(f"Browser fallback sites: {report['browser_fallback_sites']}")
    print(f"Classification attempts: {report['classification_attempted']}")
    print(f"Insufficient-evidence rate: {report['insufficient_evidence_rate']}")
    print(f"Needs-review rate: {report['needs_review_rate']}")
    print(f"Average confidence: {report['average_confidence']}")
    print("Status counts:")
    for status, count in sorted(report["by_status"].items()):
        print(f"  {status:<24} {count}")
    scale = report["scale"]
    print(f"Scale stage: {scale['stage']}")
    print(f"Production ready: {scale['production_ready']}")
    print(scale["next_step"])


def main() -> None:
    parser = argparse.ArgumentParser(description="Backup, recover, and report on the local database.")
    parser.add_argument("command", choices=("report", "backup", "recover", "benchmark", "cancel"))
    parser.add_argument("--job", type=int, default=None, help="job id for cancel")
    parser.add_argument("--rows", type=int, default=200, help="rows for the local benchmark")
    parser.add_argument("--json", action="store_true", help="print the report as JSON")
    args = parser.parse_args()

    init_db()
    if args.command == "report":
        with get_session() as session:
            report = build_report(session)
        if args.json:
            print(json.dumps(report, indent=2))
        else:
            _print_report(report)
        return
    if args.command == "backup":
        path = backup_database()
        print(f"Backup written to {path}")
        return
    if args.command == "recover":
        result = recover_abandoned()
        print(
            f"Reset {result['websites_reset']} crawling website(s). "
            f"Marked {result['jobs_failed']} interrupted job(s) failed."
        )
        return
    if args.command == "cancel":
        if args.job is None:
            raise SystemExit("Pass --job with the id of a running job.")
        with get_session() as session:
            job = session.get(Job, args.job)
            if job is None or job.status != "running":
                raise SystemExit("That job is not running.")
            job.status = "cancelled"
            job.finished_at = utcnow()
            job.note = "Cancelled by an operator."
            session.commit()
        print(f"Job {args.job} cancelled.")
        return
    result = measure(args.rows)
    print(
        f"Measured {result.rows} local rows in {result.filter_ms} ms (filter) "
        f"and {result.export_ms} ms (export shaping). Matched {result.matched}."
    )
    print(result.note)


if __name__ == "__main__":
    main()
