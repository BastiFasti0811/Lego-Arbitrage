"""Das Korpus gegen die beiden Seams.

Der Fall-Titel im Testlauf ist der Anzeigentext selbst — ein Fehlschlag ist
damit ohne Nachschlagen lesbar.
"""

import pytest

from app.domain.condition import classify_listing_condition
from app.domain.identity import is_set_offer
from tests.fixtures.listing_corpus import (
    ACCESSORIES,
    BOX_DAMAGE,
    BUNDLES,
    KNOWN_LIMITS,
    PLAUSIBLE_PRICE,
    REFERENCE_PRICE,
    SET,
    SINGLE_SETS,
)


@pytest.mark.parametrize(
    ("title", "location", "why"),
    ACCESSORIES,
    ids=[case[0] for case in ACCESSORIES],
)
def test_accessories_are_not_the_set(title, location, why):
    assert (
        is_set_offer(
            title,
            SET,
            price_eur=PLAUSIBLE_PRICE,
            reference_price=REFERENCE_PRICE,
            seller_location=location,
        )
        is False
    ), why


@pytest.mark.parametrize(
    ("title", "location", "why"),
    SINGLE_SETS,
    ids=[case[0] for case in SINGLE_SETS],
)
def test_single_set_listings_are_kept(title, location, why):
    assert (
        is_set_offer(
            title,
            SET,
            price_eur=PLAUSIBLE_PRICE,
            reference_price=REFERENCE_PRICE,
            seller_location=location,
        )
        is True
    ), why


@pytest.mark.parametrize(
    ("title", "location", "why"),
    BUNDLES,
    ids=[case[0] for case in BUNDLES],
)
def test_bundles_are_rejected(title, location, why):
    assert (
        is_set_offer(
            title,
            SET,
            price_eur=PLAUSIBLE_PRICE,
            reference_price=REFERENCE_PRICE,
            seller_location=location,
        )
        is False
    ), why


@pytest.mark.parametrize(
    ("title", "location", "why"),
    KNOWN_LIMITS,
    ids=[case[0] for case in KNOWN_LIMITS],
)
def test_known_limits_still_drop_the_offer(title, location, why):
    # Kein Wunschverhalten, sondern der festgehaltene Stand: diese Angebote
    # fallen durch, und das ist die getroffene Entscheidung.
    assert (
        is_set_offer(
            title,
            SET,
            price_eur=PLAUSIBLE_PRICE,
            reference_price=REFERENCE_PRICE,
            seller_location=location,
        )
        is False
    ), why


@pytest.mark.parametrize(
    ("label", "description", "expected", "why"),
    BOX_DAMAGE,
    ids=[case[1] for case in BOX_DAMAGE],
)
def test_box_damage_is_read_off_the_listing(label, description, expected, why):
    _condition, damage = classify_listing_condition(label, description)
    assert damage is expected, why
