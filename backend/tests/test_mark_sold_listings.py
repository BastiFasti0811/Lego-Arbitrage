"""Verkauft auf A, aktiv auf B: die Leiche darf nicht vergessen werden
(Spec Verkauft-Flow, Grill-Entscheid Q2)."""

from types import SimpleNamespace

from app.api.routes.inventory import close_sold_listing


def _listing(platform, status):
    return SimpleNamespace(platform=platform, status=status)


def test_sold_platform_listing_is_closed_and_others_reported():
    ebay = _listing("EBAY", "ACTIVE")
    ka = _listing("KLEINANZEIGEN", "ACTIVE")
    item = SimpleNamespace(listings=[ebay, ka])

    remaining = close_sold_listing(item, "eBay")

    assert ebay.status == "SOLD"
    assert remaining == [ka]


def test_direct_sale_without_listing_changes_nothing():
    ka = _listing("KLEINANZEIGEN", "ENDED")
    item = SimpleNamespace(listings=[ka])

    remaining = close_sold_listing(item, "Flohmarkt")

    assert ka.status == "ENDED"
    assert remaining == []


def test_no_platform_given_keeps_open_listings_as_checklist():
    ka = _listing("KLEINANZEIGEN", "PAUSED")
    item = SimpleNamespace(listings=[ka])

    remaining = close_sold_listing(item, None)

    assert ka.status == "PAUSED"
    assert remaining == [ka]
