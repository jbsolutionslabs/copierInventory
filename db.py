# db.py — SQLAlchemy models + session factory

import os

from sqlalchemy import (
    Boolean, Column, DateTime, Float, ForeignKey,
    Integer, String, Text, create_engine,
)
from sqlalchemy.orm import DeclarativeBase, sessionmaker

DATABASE_URL = os.environ.get("DATABASE_URL", "sqlite:///./inventory.db")

# Railway provides postgres:// URIs; SQLAlchemy 2.x needs postgresql://
if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)

engine = create_engine(
    DATABASE_URL,
    pool_pre_ping=True,
    # SQLite needs check_same_thread=False when used in threads
    connect_args={"check_same_thread": False} if DATABASE_URL.startswith("sqlite") else {},
)

SessionLocal = sessionmaker(bind=engine, autocommit=False, autoflush=False)


class Base(DeclarativeBase):
    pass


class ScrapeRun(Base):
    __tablename__ = "scrape_runs"

    id            = Column(Integer, primary_key=True)
    started_at    = Column(DateTime, default=datetime.utcnow)
    finished_at   = Column(DateTime)
    status        = Column(String(20))   # "running" | "success" | "error"
    total_records = Column(Integer, default=0)
    new_records   = Column(Integer, default=0)
    error         = Column(Text)


class InventoryRecord(Base):
    __tablename__ = "inventory"

    id           = Column(Integer, primary_key=True)
    # All 21 OUTPUT_COLUMNS
    source       = Column(String(100))
    brand        = Column(String(100))
    model        = Column(String(200))
    condition    = Column(String(50))
    state        = Column(String(10))
    inv          = Column(String(100), index=True)
    serial       = Column(String(100), index=True)
    total_meter  = Column(Float)
    color_meter  = Column(Float)
    bw_meter     = Column(Float)
    is_color     = Column(String(5))
    feeder_model = Column(String(100))
    capacity     = Column(String(200))
    finisher     = Column(String(200))
    print_speed  = Column(String(50))
    scan         = Column(String(5))
    fax          = Column(String(5))
    qty          = Column(Float)
    price        = Column(Float)
    description  = Column(Text)
    notes        = Column(Text)
    # Computed / tracking fields
    is_new        = Column(Boolean, default=True)
    config        = Column(String(500))
    first_seen_at = Column(DateTime)   # set on INSERT, never changed
    last_seen_at  = Column(DateTime)   # updated on every scrape
    scrape_run_id = Column(Integer, ForeignKey("scrape_runs.id"))


class WatchlistItem(Base):
    __tablename__ = "watchlist"

    id         = Column(String(36), primary_key=True)   # UUID
    name       = Column(String(200))
    email      = Column(String(200))
    phone      = Column(String(50))
    brand      = Column(String(100))
    model      = Column(String(200))
    max_meter  = Column(Float)
    max_price  = Column(Float)
    color      = Column(String(10))
    state      = Column(String(10))
    finisher   = Column(String(10))
    fax        = Column(String(10))
    notes      = Column(Text)
    created_at = Column(DateTime, default=datetime.utcnow)


class UploadedFile(Base):
    __tablename__ = "uploaded_files"

    id            = Column(Integer, primary_key=True)
    filename      = Column(String(500))
    original_name = Column(String(500))
    size_bytes    = Column(Integer)
    storage_path  = Column(String(1000))
    uploaded_at   = Column(DateTime, default=datetime.utcnow)


def init_db():
    """Create all tables if they don't exist."""
    Base.metadata.create_all(bind=engine)


def get_db():
    """Yield a DB session; close on exit. Use as FastAPI dependency."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
