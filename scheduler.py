# scheduler.py — APScheduler hourly scrape

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


def start_scheduler():
    global _scheduler
    _scheduler = BackgroundScheduler(timezone="UTC")
    _scheduler.add_job(_hourly_scrape, "interval", hours=1, id="hourly_scrape")
    _scheduler.start()
    print("[scheduler] Hourly scrape job scheduled.")


def stop_scheduler():
    global _scheduler
    if _scheduler and _scheduler.running:
        _scheduler.shutdown(wait=False)
        print("[scheduler] Scheduler stopped.")
