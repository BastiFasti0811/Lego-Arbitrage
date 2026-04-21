"""Business logic engine."""

from app.engine.auction_calculator import (
    AuctionBidResult,
    AuctionFeeProfile,
    calculate_auction_purchase_total,
    calculate_max_auction_bid,
)
from app.engine.decision_engine import AnalysisResult, Recommendation, analyze_deal
from app.engine.market_consensus import MarketConsensus, calculate_consensus
from app.engine.risk_scorer import RiskBreakdown, calculate_risk_score
from app.engine.roi_calculator import ROIResult, calculate_roi

__all__ = [
    "calculate_max_auction_bid",
    "calculate_auction_purchase_total",
    "AuctionBidResult",
    "AuctionFeeProfile",
    "analyze_deal",
    "AnalysisResult",
    "Recommendation",
    "calculate_consensus",
    "MarketConsensus",
    "calculate_risk_score",
    "RiskBreakdown",
    "calculate_roi",
    "ROIResult",
]
