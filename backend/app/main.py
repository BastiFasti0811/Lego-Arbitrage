"""FastAPI application entry point."""

import asyncio
import contextlib
from contextlib import asynccontextmanager

import structlog
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse

from app.api.routes import (
    analysis,
    auctions,
    auth,
    feedback,
    goals,
    inventory,
    listings,
    remote_scan,
    scout,
    sets,
    system,
    watchlist,
)
from app.api.routes import settings as settings_routes
from app.api.routes.auth import COOKIE_NAME, verify_cookie
from app.config import settings
from app.health import CHECK_OK, run_health_checks
from app.logging_setup import quiet_http_client_loggers
from app.models import Base, engine
from app.notifications.telegram_callbacks import poll_callbacks
from app.security.origin_guard import is_allowed_write

# Beim Start des API-Prozesses: httpx loggt sonst jede URL auf INFO, darunter
# api.telegram.org/bot<TOKEN>/... (siehe app/logging_setup.py).
quiet_http_client_loggers()

logger = structlog.get_logger()


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup and shutdown events."""
    logger.info("app.starting", version=settings.build_version, app_version=settings.app_version)
    if settings.auto_create_tables_on_startup:
        logger.warning("db.auto_create_tables_enabled")
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
    stop = asyncio.Event()
    poller = None
    if settings.telegram_callback_polling:
        # Klicks auf "Gekauft"/"Abgelehnt" im Deal-Alert (notifications/deal_actions).
        poller = asyncio.create_task(poll_callbacks(stop), name="telegram-callbacks")
    yield
    stop.set()
    if poller is not None:
        poller.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await poller
    logger.info("app.shutdown")


app = FastAPI(
    title=settings.app_name,
    version=settings.app_version,
    description="Autonomous LEGO Investment & Arbitrage System — German Market",
    lifespan=lifespan,
)

# CORS nur fuer ausdruecklich eingetragene fremde Origins (CORS_ALLOWED_ORIGINS,
# Default leer). Das Dashboard selbst laeuft same-origin und braucht kein CORS.
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── Auth Middleware ────────────────────────────────────────
# Logout ist offen: Es kann nur die Session beenden, deren ID der Aufrufer
# ohnehin schon hat, und muss das Browser-Cookie auch dann loeschen, wenn
# Redis gerade nicht antwortet (die Middleware wuerde dann 401 liefern).
PUBLIC_PATHS = {"/api/auth/login", "/api/auth/logout", "/health", "/"}
DOCS_PATHS = {"/docs", "/openapi.json"}
RUNNER_PATH_PREFIX = "/api/remote-scan/runner/"


@app.middleware("http")
async def auth_middleware(request: Request, call_next):
    path = request.url.path
    # CSRF: schreibende API-Aufrufe nur aus dem eigenen Origin, vor dem
    # Cookie-Check und auch fuer Login/Logout. Der Heimrechner hat kein Cookie
    # und schickt keine Browser-Header; seine Pfade prueft das Token.
    if (
        path.startswith("/api/")
        and not path.startswith(RUNNER_PATH_PREFIX)
        and not is_allowed_write(request.method, request.headers, settings.cors_origin_list)
    ):
        logger.warning(
            "auth.cross_origin_write_rejected",
            method=request.method,
            path=path,
            origin=(request.headers.get("origin") or "")[:200],
            sec_fetch_site=request.headers.get("sec-fetch-site"),
        )
        return JSONResponse(status_code=403, content={"detail": "Cross-origin write rejected"})

    if path in PUBLIC_PATHS:
        return await call_next(request)

    cookie = request.cookies.get(COOKIE_NAME)
    if path in DOCS_PATHS:
        if settings.debug or await verify_cookie(cookie):
            return await call_next(request)
        return JSONResponse(status_code=401, content={"detail": "Not authenticated"})

    if not path.startswith("/api/"):
        return await call_next(request)

    if path.startswith(RUNNER_PATH_PREFIX):
        # Der Heimrechner hat kein Cookie; die Route prueft sein Token selbst.
        return await call_next(request)

    # Check cookie on all other /api/* routes. verify_cookie fragt Redis und
    # liefert bei einem Ausfall False: dann 401, nie ein ungepruefter Durchlass.
    if not await verify_cookie(cookie):
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
app.include_router(goals.router, prefix="/api/goals", tags=["Goals"])
app.include_router(listings.router, prefix="/api/inventory", tags=["Listings"])
app.include_router(listings.overview_router, prefix="/api/listings", tags=["Listings"])
app.include_router(settings_routes.router, prefix="/api/settings", tags=["Settings"])
app.include_router(system.router, prefix="/api/system", tags=["System"])
app.include_router(remote_scan.router, prefix="/api/remote-scan", tags=["Remote Scan"])


@app.get("/health")
async def health_check():
    """Health check: 200 nur, wenn PostgreSQL und Redis antworten, sonst 503.

    `version` ist der Git-SHA des laufenden Images (siehe Settings.git_sha);
    der Deploy prueft daran, dass der neue Stand laeuft.
    """
    checks = await run_health_checks()
    healthy = all(result == CHECK_OK for result in checks.values())
    return JSONResponse(
        status_code=200 if healthy else 503,
        content={
            "status": "healthy" if healthy else "unhealthy",
            "version": settings.build_version,
            "service": "lego-arbitrage-api",
            "checks": checks,
        },
        headers={"Cache-Control": "no-store"},
    )


@app.get("/")
async def root():
    return {
        "name": settings.app_name,
        "version": settings.build_version,
        "docs": "/docs",
        "health": "/health",
    }
