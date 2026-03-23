"""FastAPI application entry point."""

from contextlib import asynccontextmanager

import structlog
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import settings
from app.api.routes import sets, analysis, scout, watchlist, feedback, inventory

logger = structlog.get_logger()


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup and shutdown events."""
    logger.info("app.starting", version=settings.app_version)
    yield
    logger.info("app.shutdown")


app = FastAPI(
    title=settings.app_name,
    version=settings.app_version,
    description="Autonomous LEGO Investment & Arbitrage System — German Market",
    lifespan=lifespan,
)

# CORS for dashboard frontend
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000", "https://lego-arbitrage.de"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── Routes ───────────────────────────────────────────────
app.include_router(sets.router, prefix="/api/sets", tags=["Sets"])
app.include_router(analysis.router, prefix="/api/analysis", tags=["Analysis"])
app.include_router(scout.router, prefix="/api/scout", tags=["Scout"])
app.include_router(watchlist.router, prefix="/api/watchlist", tags=["Watchlist"])
app.include_router(feedback.router, prefix="/api/feedback", tags=["Feedback"])
app.include_router(inventory.router, prefix="/api/inventory", tags=["Inventory"])


@app.get("/health")
async def health_check():
    """Health check endpoint for monitoring."""
    return {
        "status": "healthy",
        "version": settings.app_version,
        "service": "lego-arbitrage-api",
    }


@app.get("/")
async def root():
    return {
        "name": settings.app_name,
        "version": settings.app_version,
        "docs": "/docs",
        "health": "/health",
    }
