"""Step 1: receive website URLs and store them.

Run from the project folder:

    uvicorn app.main:app --reload
"""

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, File, Form, Request, UploadFile
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select

from app.database import get_session, init_db
from app.importer import import_urls
from app.models import Website

ROOT = Path(__file__).resolve().parent.parent
templates = Jinja2Templates(directory=str(ROOT / "templates"))


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
def home(request: Request, added: int = 0, duplicates: int = 0, invalid: int = 0, imported: int = 0):
    with get_session() as session:
        websites = session.scalars(select(Website).order_by(Website.id.desc())).all()
        session.expunge_all()

    message = _message(added, duplicates, invalid) if imported else None
    return templates.TemplateResponse(
        request,
        "index.html",
        {"websites": websites, "message": message},
    )


@app.post("/import")
async def import_list(
    urls: str = Form(""),
    source_batch: str = Form(""),
    file: UploadFile | None = File(None),
):
    text = urls
    if file is not None and file.filename:
        text += "\n" + (await file.read()).decode("utf-8", errors="replace")

    with get_session() as session:
        result = import_urls(session, text, source_batch or None)

    return RedirectResponse(
        (
            "/?imported=1"
            f"&added={result.added}"
            f"&duplicates={len(result.duplicates)}"
            f"&invalid={len(result.invalid)}"
        ),
        status_code=303,
    )


@app.post("/import-sample")
def import_sample():
    sample = (ROOT / "sample_urls.csv").read_text(encoding="utf-8")
    with get_session() as session:
        result = import_urls(session, sample, "sample")

    return RedirectResponse(
        (
            "/?imported=1"
            f"&added={result.added}"
            f"&duplicates={len(result.duplicates)}"
            f"&invalid={len(result.invalid)}"
        ),
        status_code=303,
    )
