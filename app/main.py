"""Step 1: receive website URLs and store them.

Run from the project folder:

    uvicorn app.main:app --reload
"""

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import BackgroundTasks, FastAPI, File, Form, Query, Request, UploadFile
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import func, select

from app.crawler.runner import run as run_crawl
from app.database import get_session, init_db
from app.importer import excel_to_text, import_urls
from app.models import Website

ROOT = Path(__file__).resolve().parent.parent
templates = Jinja2Templates(directory=str(ROOT / "templates"))
DEFAULT_PAGE_SIZE = 5
MAX_PAGE_SIZE = 20


def preview_clean_text(text: str | None, limit: int = 220) -> str:
    """Short readable blurb for the card. Full text stays in the expand panel."""
    if not text:
        return ""
    lines = [
        line.strip()
        for line in text.splitlines()
        if line.strip() and not line.startswith("=== PAGE:")
    ]
    joined = " ".join(lines)
    joined = " ".join(joined.split())
    if len(joined) <= limit:
        return joined
    return joined[: limit - 1].rsplit(" ", 1)[0] + "…"


templates.env.filters["preview_clean"] = preview_clean_text


@asynccontextmanager
async def lifespan(_app: FastAPI):
    init_db()
    yield


app = FastAPI(title="Website Intelligence — Step 1", lifespan=lifespan)


def _message(added: int, duplicates: int, invalid: int) -> str:
    return (
        f"Added {added}. "
        f"Skipped {duplicates} duplicates. "
        f"Rejected {invalid} that were not website addresses."
    )


@app.get("/")
def home(
    request: Request,
    added: int = 0,
    duplicates: int = 0,
    invalid: int = 0,
    imported: int = 0,
    crawl: int = 0,
    page: int = Query(1, ge=1),
    per_page: int = Query(DEFAULT_PAGE_SIZE, ge=1, le=MAX_PAGE_SIZE),
):
    with get_session() as session:
        total = session.scalar(select(func.count()).select_from(Website)) or 0
        classified = session.scalar(
            select(func.count()).select_from(Website).where(
                Website.status.in_(("classified", "needs_review"))
            )
        ) or 0
        crawled = session.scalar(
            select(func.count()).select_from(Website).where(Website.status == "crawled")
        ) or 0
        pending = session.scalar(
            select(func.count()).select_from(Website).where(
                Website.status.in_(("pending", "crawling"))
            )
        ) or 0
        issues = session.scalar(
            select(func.count()).select_from(Website).where(
                Website.status.in_(
                    ("failed", "blocked", "unreachable", "robots_blocked", "invalid", "classify_failed", "empty", "insufficient_evidence")
                )
            )
        ) or 0
        total_pages = max(1, (total + per_page - 1) // per_page) if total else 1
        page = min(page, total_pages)
        offset = (page - 1) * per_page
        websites = session.scalars(
            select(Website).order_by(Website.id.asc()).offset(offset).limit(per_page)
        ).all()
        session.expunge_all()

    if imported:
        message = _message(added, duplicates, invalid)
    elif crawl:
        message = "Crawl started. Updating results shortly…"
    else:
        message = None

    start = offset + 1 if total else 0
    end = min(offset + len(websites), total)
    return templates.TemplateResponse(
        request,
        "index.html",
        {
            "websites": websites,
            "message": message,
            "crawl": bool(crawl),
            "page": page,
            "per_page": per_page,
            "total": total,
            "total_pages": total_pages,
            "showing_start": start,
            "showing_end": end,
            "stats": {
                "total": total,
                "classified": classified,
                "crawled": crawled,
                "pending": pending,
                "issues": issues,
            },
        },
    )


@app.post("/import")
async def import_list(
    urls: str = Form(""),
    file: UploadFile | None = File(None),
):
    text = urls
    if file is not None and file.filename:
        payload = await file.read()
        if file.filename.lower().endswith(".xlsx"):
            text += "\n" + excel_to_text(payload)
        else:
            text += "\n" + payload.decode("utf-8", errors="replace")

    with get_session() as session:
        result = import_urls(session, text)

    return RedirectResponse(
        (
            "/?imported=1"
            f"&added={result.added}"
            f"&duplicates={len(result.duplicates)}"
            f"&invalid={len(result.invalid)}"
        ),
        status_code=303,
    )


async def _crawl_background(limit: int | None, site_ids: list[int] | None, force: bool):
    summary = await run_crawl(limit=limit, site_ids=site_ids, force=force)
    print(
        f"[crawl] {summary.total} site(s) in {summary.seconds}s — "
        + ", ".join(f"{k}={v}" for k, v in sorted(summary.counts.items()))
    )


@app.post("/crawl")
async def crawl_pending(background: BackgroundTasks, limit: int = Form(0)):
    """Queue a crawl run for every pending row (or the first `limit` rows)."""
    background.add_task(_crawl_background, limit or None, None, False)
    return RedirectResponse("/?crawl=1", status_code=303)


@app.post("/crawl/{site_id}")
async def crawl_one_site(background: BackgroundTasks, site_id: int):
    """Re-crawl a single row no matter what status it has."""
    background.add_task(_crawl_background, None, [site_id], True)
    return RedirectResponse(f"/?crawl=1&site={site_id}", status_code=303)
