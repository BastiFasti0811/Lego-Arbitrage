"""Settings API — manage app configuration."""
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from app.models import AppSetting, get_session

router = APIRouter()


class SettingResponse(BaseModel):
    key: str
    value: str | None
    is_secret: bool
    category: str
    label: str | None
    description: str | None
    model_config = {"from_attributes": True}


class SettingUpdate(BaseModel):
    key: str
    value: str | None


# Default settings to seed
DEFAULT_SETTINGS = [
    {"key": "ebay_api_key", "category": "ebay", "label": "eBay API Key", "description": "eBay Developer App ID", "is_secret": True},
    {"key": "ebay_api_secret", "category": "ebay", "label": "eBay API Secret", "description": "eBay Developer Cert ID", "is_secret": True},
    {"key": "kleinanzeigen_email", "category": "kleinanzeigen", "label": "Kleinanzeigen E-Mail", "description": "Login E-Mail für Kleinanzeigen", "is_secret": False},
    {"key": "kleinanzeigen_password", "category": "kleinanzeigen", "label": "Kleinanzeigen Passwort", "description": "Login Passwort", "is_secret": True},
    {"key": "telegram_bot_token", "category": "telegram", "label": "Telegram Bot Token", "description": "Token von @BotFather", "is_secret": True},
    {"key": "telegram_chat_id", "category": "telegram", "label": "Telegram Chat ID", "description": "Deine Chat ID für Notifications", "is_secret": False},
    {"key": "scraper_interval_hours", "category": "system", "label": "Scraper Intervall (Stunden)", "description": "Wie oft die Scraper laufen", "is_secret": False, "value": "6"},
    {"key": "min_roi_threshold", "category": "system", "label": "Min. ROI Schwelle (%)", "description": "Mindest-ROI für GO Empfehlung", "is_secret": False, "value": "15"},
]


@router.get("/", response_model=list[SettingResponse])
async def list_settings(category: str | None = None, session: AsyncSession = Depends(get_session)):
    """List all settings. Secret values are masked."""
    # Seed defaults if empty
    result = await session.execute(select(AppSetting))
    existing = result.scalars().all()
    if not existing:
        for s in DEFAULT_SETTINGS:
            session.add(AppSetting(**s))
        await session.commit()
        result = await session.execute(select(AppSetting))
        existing = result.scalars().all()

    settings = []
    for s in existing:
        if category and s.category != category:
            continue
        resp = SettingResponse.model_validate(s)
        if s.is_secret and s.value:
            resp.value = "\u2022\u2022\u2022\u2022\u2022\u2022\u2022\u2022"  # Mask secret values
        settings.append(resp)
    return settings


@router.put("/", response_model=list[SettingResponse])
async def update_settings(updates: list[SettingUpdate], session: AsyncSession = Depends(get_session)):
    """Update one or more settings."""
    for update in updates:
        result = await session.execute(
            select(AppSetting).where(AppSetting.key == update.key)
        )
        setting = result.scalar_one_or_none()
        if setting:
            setting.value = update.value
        else:
            session.add(AppSetting(key=update.key, value=update.value))
    await session.commit()

    # Return all settings
    return await list_settings(session=session)


@router.post("/test-telegram")
async def test_telegram(session: AsyncSession = Depends(get_session)):
    """Send a test message via Telegram."""
    result = await session.execute(
        select(AppSetting).where(AppSetting.key.in_(["telegram_bot_token", "telegram_chat_id"]))
    )
    settings = {s.key: s.value for s in result.scalars().all()}

    token = settings.get("telegram_bot_token")
    chat_id = settings.get("telegram_chat_id")

    if not token or not chat_id:
        raise HTTPException(status_code=400, detail="Telegram Bot Token und Chat ID m\u00fcssen gesetzt sein")

    import httpx
    async with httpx.AsyncClient() as client:
        resp = await client.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={"chat_id": chat_id, "text": "\U0001f9f1 LEGO Arbitrage Test \u2014 Verbindung erfolgreich!"}
        )
        if resp.status_code != 200:
            raise HTTPException(status_code=500, detail=f"Telegram Fehler: {resp.text}")

    return {"success": True, "message": "Test-Nachricht gesendet!"}
