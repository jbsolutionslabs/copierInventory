# main.py — FastAPI application entry point

import os
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from db import init_db
from scheduler import start_scheduler, stop_scheduler

ALLOWED_ORIGINS = os.environ.get(
    "ALLOWED_ORIGINS",
    "https://jbsolutionslabs.github.io",
).split(",")

# Also allow localhost for development
ALLOWED_ORIGINS += ["http://localhost", "http://localhost:8000", "http://127.0.0.1"]


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup
    init_db()
    start_scheduler()
    yield
    # Shutdown
    stop_scheduler()


app = FastAPI(
    title="Copier Inventory API",
    version="2.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Mount route modules
from routes.inventory import router as inventory_router
from routes.scrape    import router as scrape_router
from routes.uploads   import router as uploads_router
from routes.watchlist import router as watchlist_router

app.include_router(inventory_router)
app.include_router(uploads_router)
app.include_router(watchlist_router)
app.include_router(scrape_router)


@app.get("/")
def health():
    return {"status": "ok", "service": "Copier Inventory API"}
