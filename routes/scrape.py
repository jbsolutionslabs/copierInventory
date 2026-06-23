# routes/scrape.py — POST /api/scrape/trigger, GET /api/scrape/status

import threading

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from db import ScrapeRun, SessionLocal, get_db

router = APIRouter()

_scrape_lock = threading.Lock()


def _run_in_background(sources=None, imports_only=False):
    """Run scrape in a background thread with its own DB session."""
    if not _scrape_lock.acquire(blocking=False):
        print("[scrape] Already running — skipped duplicate trigger.")
        return
    try:
        from scraper import run_scrape
        db = SessionLocal()
        try:
            run_scrape(db, sources=sources, imports_only=imports_only)
        finally:
            db.close()
    finally:
        _scrape_lock.release()


@router.post("/api/scrape/trigger")
def trigger_scrape(
    sources: list[str] | None = None,
    imports_only: bool = False,
):
    """Kick off a background scrape (non-blocking)."""
    t = threading.Thread(
        target=_run_in_background,
        kwargs={"sources": sources, "imports_only": imports_only},
        daemon=True,
    )
    t.start()
    return {"ok": True, "message": "Scrape started in background."}


@router.get("/api/scrape/status")
def scrape_status(db: Session = Depends(get_db)):
    """Return the most recent ScrapeRun record."""
    run: ScrapeRun | None = (
        db.query(ScrapeRun).order_by(ScrapeRun.id.desc()).first()
    )
    if not run:
        return {"status": "never_run"}
    return {
        "id":           run.id,
        "status":       run.status,
        "startedAt":    run.started_at.strftime("%Y-%m-%dT%H:%M:%SZ") if run.started_at else None,
        "finishedAt":   run.finished_at.strftime("%Y-%m-%dT%H:%M:%SZ") if run.finished_at else None,
        "totalRecords": run.total_records,
        "newRecords":   run.new_records,
        "error":        run.error,
    }
