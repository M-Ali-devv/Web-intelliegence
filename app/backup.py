"""Copy the SQLite database and keep only the newest backup files."""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from app import database
from app.database import DATA_DIR
from app.limits import backup_keep


def backup_database(directory: Path | None = None, keep: int | None = None) -> Path:
    folder = directory or (DATA_DIR / "backups")
    folder.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S-%f")
    dest = folder / f"websites-{stamp}.db"
    target = sqlite3.connect(dest)
    try:
        raw = database.engine.raw_connection()
        try:
            raw.backup(target)
        finally:
            raw.close()
    except Exception:
        target.close()
        dest.unlink(missing_ok=True)
        raise
    else:
        target.close()
    _prune(folder, backup_keep() if keep is None else keep)
    return dest


def _prune(folder: Path, keep: int) -> None:
    files = sorted(folder.glob("websites-*.db"))
    extra = files[:-keep] if keep else files
    for path in extra:
        path.unlink()
