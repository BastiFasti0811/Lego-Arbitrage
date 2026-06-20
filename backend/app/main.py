"""FastAPI application entry point."""

from contextlib import asynccontextmanager

import structlog
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse

from app.api.routes import analysis, auctions, auth, feedback, inventory, scout, sets, watchlist
from app.api.routes import settings as settings_routes
from app.api.routes.auth import COOKIE_NAME, verify_cookie
from app.config import settings
from app.models import Base, engine

logger = structlog.get_logger()


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup and shutdown events."""
    logger.info("app.starting", version=settings.app_version)
    if settings.auto_create_tables_on_startup:
        logger.warning("db.auto_create_tables_enabled")
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
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

# ── Auth Middleware ────────────────────────────────────────
PUBLIC_PATHS = {"/api/auth/login", "/health", "/"}
DOCS_PATHS = {"/docs", "/openapi.json"}


@app.middleware("http")
async def auth_middleware(request: Request, call_next):
    path = request.url.path
    if path in PUBLIC_PATHS:
        return await call_next(request)

    cookie = request.cookies.get(COOKIE_NAME)
    if path in DOCS_PATHS:
        if settings.debug or verify_cookie(cookie):
            return await call_next(request)
        return JSONResponse(status_code=401, content={"detail": "Not authenticated"})

    if not path.startswith("/api/"):
        return await call_next(request)

    # Check cookie on all other /api/* routes
    if not verify_cookie(cookie):
        return JSONResponse(
            status_code=401,
            content={"detail": "Not authenticated"},
        )
    return await call_next(request)


# ── Routes ───────────────────────────────────────────────
app.include_router(auth.router, prefix="/api/auth", tags=["Auth"])
app.include_router(sets.router, prefix="/api/sets", tags=["Sets"])
app.include_router(analysis.router, prefix="/api/analysis", tags=["Analysis"])
app.include_router(auctions.router, prefix="/api/auctions", tags=["Auctions"])
app.include_router(scout.router, prefix="/api/scout", tags=["Scout"])
app.include_router(watchlist.router, prefix="/api/watchlist", tags=["Watchlist"])
app.include_router(feedback.router, prefix="/api/feedback", tags=["Feedback"])
app.include_router(inventory.router, prefix="/api/inventory", tags=["Inventory"])
app.include_router(settings_routes.router, prefix="/api/settings", tags=["Settings"])


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
