# AI-Powered Website Intelligence & Business Classification System

Group project: turn a raw list of 60,000+ company website URLs into structured,
searchable business intelligence (company type, products, services, industry,
target market) with Excel-ready export.

**Pipeline:** `Import URLs → Crawl & Clean → AI Classification → Review → Export`

| Stage | Module | Status |
|---|---|---|
| 1. Save the URL list | `app/importer.py`, `app/main.py` (Step 1) | ✅ done |
| 2. Crawl sites → `clean_text` | `app/crawler/` + `app/crawler_cli.py` | ✅ done — see below |
| 3. AI classification → company fields | *(next member)* | ⬜ |
| 4–6. Review, dashboard, export | *future stages* | ⬜ |

---

## Quickstart

```bash
git clone https://github.com/M-Ali-devv/Web-intelliegence.git
cd Web-intelliegence
git checkout feature/crawler      # this PR's branch (not needed after merge)

python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/uvicorn app.main:app --reload       # web UI at http://127.0.0.1:8000
```

Import URLs (paste box, CSV upload, or "Load sample list"), then crawl:

```bash
.venv/bin/python -m app.crawler_cli            # crawl all pending rows
.venv/bin/python -m app.crawler_cli --limit 20 # first 20 pending
.venv/bin/python -m app.crawler_cli --id 3 --id 7 --force   # re-crawl specific rows
```

Or click **Crawl pending websites** / per-row **Re-crawl** in the web UI.

---

## Module 2 — Crawler contract (read this before writing the AI step)

### What the crawler writes

For every imported website the crawler fetches the homepage plus up to 4
priority pages (About / Services / Products / Industries — max **5 pages**,
configurable in `app/crawler/config.py`), strips boilerplate, and stores one
consolidated profile in the `clean_text` column of the `websites` table.

**`clean_text` format** — pages separated by a machine-readable header:

```
=== PAGE: homepage | https://stripe.com
<title line>
<cleaned body text, one line per heading/paragraph/list item>

=== PAGE: about | https://stripe.com/about
...
```

Rules the AI step can rely on:

- Headers are always `=== PAGE: <label> | <absolute URL>` — use the URL as
  the `evidence` source for any classification.
- Line breaks are real `\n`; repeated boilerplate across pages is removed
  (first occurrence kept).
- Hard cap: **60,000 characters** per site; if cut, the profile ends with
  `...[truncated]`.
- `clean_text IS NULL` means "no usable text" (never crawled, empty site,
  unreachable, blocked, …) — **skip those rows**.

### Rows to process (SQL)

```sql
SELECT id, normalized_website, clean_text
FROM websites
WHERE status = 'crawled' AND clean_text IS NOT NULL;
```

After classifying, fill only the AI-owned columns (all exist already, nullable):
`company_name`, `description`, `business_type`, `industry`, `niche`,
`products`, `services`, `confidence` (0–100), `evidence`.

### Status vocabulary (`websites.status`)

| Status | Meaning |
|---|---|
| `pending` | imported, waiting to be crawled |
| `crawling` | claimed by a run (stuck rows are auto-reset by the next run) |
| `crawled` | ✅ `clean_text` filled — **the AI step processes these** |
| `empty` | fetched, but below the minimum usable text |
| `unreachable` | DNS / timeout / connection / TLS failure |
| `blocked` | HTTP 401 / 403 / 429 (bot protection) |
| `robots_blocked` | disallowed by robots.txt |
| `invalid` | rejected by the SSRF guard (private/internal address) |
| `failed` | other HTTP/redirect/content errors — reason in `crawl_error` |

Bookkeeping columns written by the crawler: `crawl_error`, `pages_crawled`,
`last_crawled_at`. It never touches the AI-owned columns.

### Reliability guarantees

- One failed site never stops the batch; every failure keeps an actionable
  reason in `crawl_error`.
- Runs are idempotent: only `pending` rows are claimed (unless `--force`);
  Ctrl-C or a crash cannot lose rows — the next run re-queues stuck ones.
- Polite & safe: robots.txt respected, 10 concurrent sites, sequential within
  a site, private/metadata addresses blocked (SSRF guard on every redirect).

---

## Tests

```bash
.venv/bin/pip install -r requirements-dev.txt
.venv/bin/pytest -q --cov=app.crawler --cov=app.crawler_cli
# → 97 passed, 100% statement coverage
```

All HTTP is mocked — tests run offline and are CI-friendly. Coverage spans
happy paths (multi-page crawl, profile building, queue flow) and error paths
(SSRF, redirect loops/attacks, timeouts, 4xx/5xx retries, robots rules,
oversize/non-HTML content, malformed & non-Latin HTML, crash recovery,
double-run lock, CLI failures).

## Known limits (Phase 2, per the requirements document)

- JavaScript-only sites are fetched as static HTML (no Playwright yet).
- No PDF extraction, no periodic re-crawl, no LLM classification (Module 3).
- Designed for one crawler process at a time (SQLite). At 60K scale the
  proposal recommends PostgreSQL + a queue (Redis/Celery).
