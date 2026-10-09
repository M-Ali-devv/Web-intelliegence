"""Step 1: receive website URLs and store them.

Run from the project folder:

    uvicorn app.main:app --reload
"""

import json
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import urlencode

from fastapi import BackgroundTasks, FastAPI, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.responses import JSONResponse, RedirectResponse, StreamingResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import func, select

from app.classifier import classify_site
from app.crawler.runner import run as run_crawl
from app.database import get_session, init_db
from app.exporters import iter_csv, iter_json, iter_xlsx
from app.importer import excel_to_text, import_urls
from app.limits import max_import_chars, max_upload_bytes
from app.models import Job, Website
from app.observe import log_event
from app.records import evidence_of, filtered_select, meta_of
from app.recovery import recover_abandoned, utcnow
from app.security import SecurityHeadersMiddleware
from app.taxonomy import BUSINESS_TYPES, INDUSTRIES

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
    recovered = recover_abandoned()
    if recovered["websites_reset"] or recovered["jobs_failed"]:
        log_event("queue_recovered", **recovered)
    yield


app = FastAPI(title="Website Intelligence — Step 1", lifespan=lifespan)
app.add_middleware(SecurityHeadersMiddleware)


@app.get("/health")
def health():
    """Whether this process can read the local database."""
    try:
        with get_session() as session:
            total = session.scalar(select(func.count()).select_from(Website)) or 0
    except Exception as exc:
        log_event("health_failed", error=type(exc).__name__)
        return JSONResponse({"ok": False, "error": "database unavailable"}, status_code=503)
    return {"ok": True, "websites": total}


def _confidence_floor(value: str | None) -> int | None:
    """Blank form fields arrive as \"\". Treat that as no minimum."""
    if value is None or not str(value).strip():
        return None
    try:
        number = int(value)
    except ValueError:
        raise HTTPException(status_code=422, detail="min_confidence must be a whole number from 0 to 100")
    if number < 0 or number > 100:
        raise HTTPException(status_code=422, detail="min_confidence must be a whole number from 0 to 100")
    return number


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
    review: int = 0,
    notice: str = "",
    q: str = "",
    industry: str = "",
    business_type: str = "",
    status: str = "",
    business_model: str = "",
    geography: str = "",
    min_confidence: str | None = Query(None),
    page: int = Query(1, ge=1),
    per_page: int = Query(DEFAULT_PAGE_SIZE, ge=1, le=MAX_PAGE_SIZE),
):
    confidence_floor = _confidence_floor(min_confidence)
    listing = filtered_select(
        q, industry, business_type, status, confidence_floor, business_model, geography
    )
    with get_session() as session:
        saved_total = session.scalar(select(func.count()).select_from(Website)) or 0
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
        total = session.scalar(select(func.count()).select_from(listing.subquery())) or 0
        total_pages = max(1, (total + per_page - 1) // per_page) if total else 1
        page = min(page, total_pages)
        offset = (page - 1) * per_page
        websites = session.scalars(listing.offset(offset).limit(per_page)).all()
        for site in websites:
            site.evidence_items = evidence_of(site)
            site.review_decision = meta_of(site).get("review_decision") or ""
        session.expunge_all()

    if notice == "cancelled":
        message = "The running job was cancelled. Rows still in progress return to the queue."
    elif notice == "upload":
        message = (
            f"That file is larger than the upload limit ({max_upload_bytes():,} bytes), "
            "so it was not imported."
        )
    elif imported:
        message = _message(added, duplicates, invalid)
    elif crawl:
        message = "Crawl started. Updating results shortly…"
    elif review:
        message = "Review saved."
    else:
        message = None

    filters = {
        "q": q.strip(),
        "industry": industry,
        "business_type": business_type,
        "status": status,
        "business_model": business_model.strip(),
        "geography": geography.strip(),
        "min_confidence": "" if confidence_floor is None else str(confidence_floor),
    }
    filter_query = urlencode({key: value for key, value in filters.items() if value})
    start = offset + 1 if total else 0
    end = min(offset + len(websites), total)
    return templates.TemplateResponse(
        request,
        "index.html",
        {
            "websites": websites,
            "message": message,
            "crawl": bool(crawl),
            "stopped": notice in {"upload", "cancelled"},
            "stopped_title": "Job cancelled" if notice == "cancelled" else "Import stopped",
            "page": page,
            "per_page": per_page,
            "total": total,
            "saved_total": saved_total,
            "total_pages": total_pages,
            "showing_start": start,
            "showing_end": end,
            "filters": filters,
            "filter_query": filter_query,
            "industries": INDUSTRIES,
            "business_types": BUSINESS_TYPES,
            "stats": {
                "total": saved_total,
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
        if len(payload) > max_upload_bytes():
            log_event("upload_rejected", bytes=len(payload))
            return RedirectResponse("/?notice=upload", status_code=303)
        if file.filename.lower().endswith(".xlsx"):
            text += "\n" + excel_to_text(payload)
        else:
            text += "\n" + payload.decode("utf-8", errors="replace")
    if len(text) > max_import_chars():
        log_event("upload_rejected", chars=len(text))
        return RedirectResponse("/?notice=upload", status_code=303)

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


def _classify_one(site_id: int) -> None:
    with get_session() as session:
        site = session.get(Website, site_id)
        if site is None or not site.clean_text:
            return
        try:
            classify_site(site)
        except RuntimeError:
            site.status = "classify_failed"
        session.commit()


@app.post("/classify/{site_id}")
async def classify_one_site(background: BackgroundTasks, site_id: int):
    """Run classification again for one crawled website."""
    background.add_task(_classify_one, site_id)
    return RedirectResponse(f"/?crawl=1&site={site_id}", status_code=303)


@app.post("/review/{site_id}")
def review_site(site_id: int, decision: str = Form(...)):
    """Accept a low-confidence profile, or mark it as not enough evidence."""
    with get_session() as session:
        site = session.get(Website, site_id)
        if site is None:
            return RedirectResponse("/", status_code=303)
        try:
            meta = json.loads(site.classification_meta or "{}")
        except json.JSONDecodeError:
            meta = {}
        if not isinstance(meta, dict):
            meta = {}
        if decision == "accept":
            site.status = "classified"
            site.classification_status = "classified"
            meta["review_decision"] = "accepted"
        elif decision == "reject":
            site.status = "insufficient_evidence"
            site.classification_status = "insufficient_evidence"
            meta["review_decision"] = "rejected"
        site.classification_meta = json.dumps(meta, ensure_ascii=False)
        session.commit()
    return RedirectResponse("/?review=1&status=review", status_code=303)


def _export_stmt(
    q: str,
    industry: str,
    business_type: str,
    status: str,
    min_confidence: int | None,
    business_model: str = "",
    geography: str = "",
):
    return filtered_select(
        q, industry, business_type, status, min_confidence, business_model, geography
    )


@app.post("/jobs/{job_id}/cancel")
def cancel_job(job_id: int):
    """Mark a running crawl or classify job cancelled. The worker stops at the next site."""
    with get_session() as session:
        job = session.get(Job, job_id)
        if job is None or job.status != "running":
            return RedirectResponse("/", status_code=303)
        job.status = "cancelled"
        job.finished_at = utcnow()
        job.note = "Cancelled by an operator."
        session.commit()
    log_event("job_cancelled", job_id=job_id)
    return RedirectResponse("/?notice=cancelled", status_code=303)


@app.get("/export.csv")
def export_csv(
    q: str = "",
    industry: str = "",
    business_type: str = "",
    status: str = "",
    business_model: str = "",
    geography: str = "",
    min_confidence: str | None = Query(None),
):
    stmt = _export_stmt(
        q, industry, business_type, status, _confidence_floor(min_confidence), business_model, geography
    )
    return StreamingResponse(
        iter_csv(stmt),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=companies.csv"},
    )


@app.get("/export.json")
def export_json(
    q: str = "",
    industry: str = "",
    business_type: str = "",
    status: str = "",
    business_model: str = "",
    geography: str = "",
    min_confidence: str | None = Query(None),
):
    stmt = _export_stmt(
        q, industry, business_type, status, _confidence_floor(min_confidence), business_model, geography
    )
    return StreamingResponse(
        iter_json(stmt),
        media_type="application/json",
        headers={"Content-Disposition": "attachment; filename=companies.json"},
    )


@app.get("/export.xlsx")
def export_xlsx(
    q: str = "",
    industry: str = "",
    business_type: str = "",
    status: str = "",
    business_model: str = "",
    geography: str = "",
    min_confidence: str | None = Query(None),
):
    stmt = _export_stmt(
        q, industry, business_type, status, _confidence_floor(min_confidence), business_model, geography
    )
    return StreamingResponse(
        iter_xlsx(stmt),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": "attachment; filename=companies.xlsx"},
    )
