"""Application configuration using Pydantic Settings."""

from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    """Central configuration — all values from .env or environment variables."""

    # ── App ──────────────────────────────────────────────
    app_name: str = "LEGO Arbitrage System"
    app_version: str = "0.2.0"
    debug: bool = False
    log_level: str = "INFO"

    # ── Database ─────────────────────────────────────────
    database_url: str = Field(
        default="postgresql+asyncpg://lego:lego_secret@localhost:5432/lego_arbitrage",
        description="Async PostgreSQL connection string",
    )
    database_url_sync: str = Field(
        default="postgresql://lego:lego_secret@localhost:5432/lego_arbitrage",
        description="Sync PostgreSQL connection string (for Alembic)",
    )

    # ── Redis ────────────────────────────────────────────
    redis_url: str = "redis://localhost:6379/0"
    cache_ttl_seconds: int = 14400  # 4 hours

    # ── Scraper Settings ─────────────────────────────────
    scraper_delay_min: float = 2.0  # Min seconds between requests
    scraper_delay_max: float = 5.0  # Max seconds between requests
    scraper_max_retries: int = 3
    scraper_timeout: int = 30  # seconds
    proxy_url: str | None = None  # e.g. http://user:pass@proxy:port
    use_stealth_mode: bool = True

    # ── eBay Settings ────────────────────────────────────
    ebay_sold_lookback_days: int = 60
    ebay_outlier_threshold: float = 0.30  # ±30% from median
    ebay_min_sold_items: int = 5  # Minimum sold items for reliable data

    # ── Market Analysis ──────────────────────────────────
    # Source weights for market consensus price
    weight_ebay_sold: float = 0.40
    weight_brickeconomy: float = 0.30
    weight_idealo: float = 0.20
    weight_brickmerge: float = 0.10
    # Price divergence warning threshold
    price_divergence_warning: float = 0.20  # 20%

    # ── eBay Fee Structure (Germany, 2026) ───────────────
    ebay_provision_rate: float = 0.129  # 12.9%
    ebay_provision_fixed: float = 0.35  # €
    ebay_payment_rate: float = 0.019  # 1.9%
    ebay_payment_fixed: float = 0.35  # €

    # ── Shipping Cost Matrix (€) ─────────────────────────
    shipping_small: float = 6.50  # <30cm
    shipping_medium: float = 12.50  # 30-50cm
    shipping_large: float = 21.50  # 50-70cm
    shipping_xlarge: float = 32.50  # >70cm

    # ── ROI Thresholds by Category ───────────────────────
    min_roi_fresh: float = 30.0  # 0-1 years
    min_roi_sweet_spot: float = 15.0  # 2-4 years
    min_roi_established: float = 12.0  # 5-7 years
    min_roi_vintage: float = 20.0  # 8-11 years
    min_roi_legacy: float = 25.0  # 12+ years

    # ── Risk Score Limits ────────────────────────────────
    max_risk_score_go: int = 6  # Max risk for GO recommendation
    max_risk_score_go_star: int = 5  # Max risk for GO ⭐

    # ── AI Agent (Phase 3) ───────────────────────────────
    anthropic_api_key: str | None = None
    anthropic_model_analysis: str = "claude-sonnet-4-20250514"
    anthropic_model_simple: str = "claude-haiku-4-5-20251001"
    ai_monthly_budget_eur: float = 100.0

    # ── Telegram Bot ─────────────────────────────────────
    telegram_bot_token: str | None = None
    telegram_chat_id: str | None = None
    telegram_alert_on_go_only: bool = True

    # ── Email Notifications ──────────────────────────────
    smtp_host: str | None = None
    smtp_port: int = 587
    smtp_user: str | None = None
    smtp_password: str | None = None
    notification_email: str | None = None

    # ── Dashboard Auth ─────────────────────────────────────
    dashboard_password: str | None = None
    session_secret: str | None = None

    # ── Media / Uploads ─────────────────────────────────
    media_root: Path = Path("data")
    inventory_photo_max_count: int = 8
    inventory_photo_max_bytes: int = 8 * 1024 * 1024
    inventory_photo_max_dimension: int = 1600
    inventory_photo_jpeg_quality: int = 82

    # ── Scheduling ───────────────────────────────────────
    scrape_interval_hours: int = 6  # How often to run full scrape
    analysis_interval_minutes: int = 30  # How often to analyze new offers

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8", "case_sensitive": False}


# Singleton
settings = Settings()
