from app.services.auction_watch import build_auction_fee_profile, detect_source_platform


def test_detect_source_platform_supports_catawiki_and_whatnot():
    assert detect_source_platform("https://www.catawiki.com/de/l/123", None) == "CATAWIKI"
    assert detect_source_platform("https://www.whatnot.com/de-DE/listing/123", None) == "WHATNOT"


def test_build_auction_fee_profile_defaults_to_catawiki_fee_model():
    profile = build_auction_fee_profile(source_platform="CATAWIKI")

    assert profile.platform == "CATAWIKI"
    assert profile.buyer_fee_rate == 0.09
    assert profile.buyer_fee_fixed == 3.0
    assert profile.fee_applies_to_shipping is False
