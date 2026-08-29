"""Database models."""

from app.models.analysis_history import AnalysisHistoryEntry
from app.models.auction_watch import AuctionWatchItem
from app.models.base import Base, async_session, engine, get_session
from app.models.dismissal import DismissedOffer
from app.models.feedback import DealFeedback, WatchlistItem
from app.models.heartbeat import TaskHeartbeat
from app.models.inventory import InventoryItem, InventoryStatus
from app.models.inventory_photo import InventoryPhoto
from app.models.offer import Offer, OfferCondition, OfferPlatform, OfferStatus
from app.models.price import PriceRecord, PriceSource
from app.models.set import EOLStatus, LegoSet, SetCategory, ThemeTier
from app.models.settings import AppSetting
from app.models.valuation_run import (
    ValuationOutcome,
    ValuationRun,
    ValuationRunItem,
    ValuationRunStatus,
    ValuationSkipReason,
    ValuationTrigger,
)

__all__ = [
    "Base",
    "async_session",
    "engine",
    "get_session",
    "AnalysisHistoryEntry",
    "AuctionWatchItem",
    "LegoSet",
    "SetCategory",
    "EOLStatus",
    "ThemeTier",
    "PriceRecord",
    "PriceSource",
    "Offer",
    "OfferPlatform",
    "OfferCondition",
    "OfferStatus",
    "DealFeedback",
    "DismissedOffer",
    "WatchlistItem",
    "TaskHeartbeat",
    "InventoryItem",
    "InventoryStatus",
    "InventoryPhoto",
    "AppSetting",
    "ValuationRun",
    "ValuationRunItem",
    "ValuationTrigger",
    "ValuationRunStatus",
    "ValuationOutcome",
    "ValuationSkipReason",
]
