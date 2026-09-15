# scheduler.py — APScheduler hourly scrape + daily retention cleanup

from apscheduler.schedulers.background import BackgroundScheduler

_scheduler: BackgroundScheduler | None = None


def _hourly_scrape():
    from db import SessionLocal
    from scraper import run_scrape
    db = SessionLocal()
    try:
        run_scrape(db)
    finally:
        db.close()


def _daily_cleanup():
    from db import SessionLocal
    from cleanup import run_retention_cleanup
    db = SessionLocal()
    try:
        run_retention_cleanup(db)
    finally:
        db.close()


def start_scheduler():
    global _scheduler
    _scheduler = BackgroundScheduler(timezone="UTC")
    _scheduler.add_job(_hourly_scrape, "interval", hours=1, id="hourly_scrape")
    # Run retention cleanup once per day at 03:00 UTC
    _scheduler.add_job(_daily_cleanup, "cron", hour=3, minute=0, id="daily_cleanup")
    _scheduler.start()
    print("[scheduler] Hourly scrape job scheduled.")
    print("[scheduler] Daily retention cleanup scheduled (03:00 UTC).")


def stop_scheduler():
    global _scheduler
    if _scheduler and _scheduler.running:
        _scheduler.shutdown(wait=False)
        print("[scheduler] Scheduler stopped.")
