from app.api.routes.analysis import (
    AnalysisResponse,
    _allocate_bundle_shipping,
    _with_bundle_metrics,
)


def _sample_result(**overrides) -> AnalysisResponse:
    base = {
        "history_id": 1,
        "set_number": "75292",
        "set_name": "The Mandalorian Transport",
        "release_year": 2022,
        "theme": "Star Wars",
        "set_age": 4,
        "category": "SWEET_SPOT",
        "uvp": 149.99,
        "offer_price": 300.0,
        "discount_vs_uvp": 0.0,
        "market_price": 149.16,
        "reference_price": 149.16,
        "reference_label": "MARKT_KONSENS",
        "still_in_retail": False,
        "eol_status": "RETIRED",
        "calibration_roi_delta": None,
        "calibrated_roi_percent": None,
        "num_sources": 3,
        "roi_percent": -117.3,
        "annualized_roi": -117.3,
        "net_profit": -95.60,
        "total_purchase_cost": 81.50,
        "total_selling_costs": 21.13,
        "risk_score": 6,
        "risk_rating": "MEDIUM",
        "recommendation": "NO_GO",
        "reason": "Test fixture",
        "suggestions": [],
        "opportunity_score": 10.0,
        "confidence": 0.9,
        "warnings": [],
        "source_prices": {},
        "analyzed_at": "2026-03-26T12:00:00+00:00",
        "source_url": None,
        "source_platform": None,
    }
    base.update(overrides)
    return AnalysisResponse(**base)


def test_allocate_bundle_shipping_preserves_total():
    allocation = _allocate_bundle_shipping(
        set_numbers=["a", "b", "c"],
        price_allocation={"a": 50.0, "b": 30.0, "c": 20.0},
        total_purchase_shipping=5.0,
    )

    assert round(sum(allocation.values()), 2) == 5.0
    assert allocation["a"] >= allocation["b"] >= allocation["c"]


def test_with_bundle_metrics_uses_market_minus_costs():
    result = _sample_result()

    corrected = _with_bundle_metrics(
        result,
        allocated_price=75.0,
        allocated_shipping=0.0,
    )

    assert corrected.offer_price == 75.0
    assert corrected.total_purchase_cost == 75.0
    assert corrected.net_profit == 53.03
    assert corrected.roi_percent == 70.7
