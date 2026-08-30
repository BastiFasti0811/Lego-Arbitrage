from datetime import UTC, date, datetime
from types import SimpleNamespace

from app.services.listing_rules import (
    apply_price_change,
    compute_next_check,
    default_min_price,
    default_price_type,
    validate_activation,
)


def test_default_min_price_is_70_percent():
    assert default_min_price(50.0) == 35.0
    assert default_min_price(39.99) == 27.99


def test_default_price_type_by_platform():
    assert default_price_type("KLEINANZEIGEN") == "VB"
    assert default_price_type("EBAY") == "FIXED"


def test_compute_next_check_counts_from_listed_at():
    result = compute_next_check(date(2026, 8, 1), 14)
    assert result == datetime(2026, 8, 15, tzinfo=UTC)


def test_activation_requires_positive_price_and_min_price():
    assert validate_activation("KLEINANZEIGEN", None, 10.0) is not None
    assert validate_activation("KLEINANZEIGEN", 0, 10.0) is not None
    assert validate_activation("KLEINANZEIGEN", 50.0, None) is not None
    assert validate_activation("KLEINANZEIGEN", 50.0, 60.0) is not None  # Schmerzgrenze ueber Startpreis
    assert validate_activation("UNBEKANNT", 50.0, 35.0) is not None
    assert validate_activation("KLEINANZEIGEN", 50.0, 35.0) is None


def _listing(price=50.0, interval=14):
    return SimpleNamespace(
        id=1, current_price=price, check_interval_days=interval, next_check_at=None
    )


def test_apply_price_change_records_old_and_new_and_reschedules():
    listing = _listing()
    now = datetime(2026, 8, 30, 8, 0, tzinfo=UTC)

    change = apply_price_change(listing, 45.0, now)

    assert change.old_price == 50.0
    assert change.new_price == 45.0
    assert change.changed_at == now
    assert listing.current_price == 45.0
    assert listing.next_check_at == datetime(2026, 9, 13, 8, 0, tzinfo=UTC)


def test_apply_price_change_ignores_unchanged_price():
    listing = _listing()
    assert apply_price_change(listing, 50.0, datetime(2026, 8, 30, tzinfo=UTC)) is None
    assert listing.next_check_at is None
