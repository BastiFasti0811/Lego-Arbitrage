"""Tests fuer app.ai.schemas und app.ai.photo_prep (PR 2, Task 5)."""

import io
from pathlib import Path

from PIL import Image

from app.ai.photo_prep import prepare_photo
from app.ai.schemas import ItemDraft, ListingText


def test_item_draft_roundtrip():
    data = {
        "name": "LEGO Star Wars Millennium Falcon",
        "product_group": "LEGO",
        "condition": "USED_COMPLETE",
        "description": "Gebrauchtes Set, augenscheinlich vollstaendig, mit OVP.",
        "search_query": "LEGO 75192 Millennium Falcon",
        "platform_category": "Spielzeug > Bausteine",
        "price_min": 450.0,
        "price_max": 600.0,
        "confidence": "medium",
    }

    draft = ItemDraft(**data)

    assert draft.search_query == data["search_query"]
    assert draft.platform_category == data["platform_category"]
    assert draft.product_group == "LEGO"
    assert draft.price_min == 450.0
    assert draft.price_max == 600.0
    assert draft.confidence == "medium"


def test_listing_text_roundtrip():
    data = {
        "title": "LEGO Millennium Falcon - gebraucht, komplett",
        "body": "Verkaufe mein gebrauchtes Set. Alle Teile vorhanden.\nVersand moeglich.",
        "platform_category": "Spielzeug > Bausteine",
    }

    listing = ListingText(**data)

    assert listing.title == data["title"]
    assert listing.body == data["body"]
    assert listing.platform_category == data["platform_category"]


def test_prepare_photo_shrinks_oversized_jpeg(tmp_path: Path):
    path = tmp_path / "large.jpg"
    Image.new("RGB", (2400, 1200), color=(120, 80, 40)).save(path, "JPEG", quality=95)

    data, media_type = prepare_photo(path, "image/jpeg")

    assert media_type == "image/jpeg"
    reopened = Image.open(io.BytesIO(data))
    assert max(reopened.size) <= 1600


def test_prepare_photo_keeps_small_png_untouched(tmp_path: Path):
    path = tmp_path / "small.png"
    Image.new("RGB", (200, 200), color=(10, 20, 30)).save(path, "PNG")
    original_bytes = path.read_bytes()
    assert len(original_bytes) < 1_500_000

    data, media_type = prepare_photo(path, "image/png")

    assert data == original_bytes
    assert media_type == "image/png"
