"""Marketplace URLs must reduce to a stable identity.

The sample URLs below are real search-result hrefs, not invented ones: Amazon
appends a per-request `dib` token plus a `ref=sr_1_N` position marker, eBay
appends `itmmeta` and an encrypted `itmprp`. Every scrape run therefore saw a
new URL for the same listing, which broke the `(platform, offer_url)` upsert
key and filled the live feed with the same offer over and over.
"""

from app.domain.offer_url import canonical_offer_url, offer_identity

# Two scrapes of the same Ferrari listing, minutes apart.
AMAZON_RUN_1 = (
    "https://www.amazon.de/42143-Technic-Ferrari-Daytona-Modellauto-Bausatz/dp/B09QFSCWD9/"
    "ref=sr_1_1?dib=eyJ2IjoiMSJ9.aN_-No_NTxrdBnFDQWk5qGg9xk_y3TUtH6MP7CYXjCkk&dib_tag=se&qid=1755640000&sr=8-1"
)
AMAZON_RUN_2 = (
    "https://www.amazon.de/42143-Technic-Ferrari-Daytona-Modellauto-Bausatz/dp/B09QFSCWD9/"
    "ref=sr_1_3?dib=eyJ2IjoiMSJ9.ZZZ-Xy_QQQrdBnFDQWk5qGg9xk_y3TUtH6MP7CYXjCkk&dib_tag=se&qid=1755647777&sr=8-3"
)

EBAY_RUN_1 = (
    "https://www.ebay.de/itm/405882267931?_skw=LEGO+75192&epid=26058465711"
    "&itmmeta=01M0DR1AT9AF9YGWD9VGG0VZGR&hash=item5e8077e91b%3Ag%3A6rMAAeSwbAtoLJ7e&itmprp=enc%3AAQALAAAA8GfYFPkw"
)
EBAY_RUN_2 = (
    "https://www.ebay.de/itm/405882267931?_skw=LEGO+75192&epid=26058465711"
    "&itmmeta=99XYZR1AT9AF9YGWD9VGG0VZGR&hash=item5e8077e91b%3Ag%3AQQQAAeSwbAtoLJ7e&itmprp=enc%3ABBBLAAAA8GfYFPkw"
)


class TestAmazon:
    def test_reduces_to_asin(self):
        assert canonical_offer_url(AMAZON_RUN_1) == "https://www.amazon.de/dp/B09QFSCWD9"

    def test_two_runs_of_same_listing_share_one_key(self):
        # The regression: differing `dib`/`ref`/`qid` must not create a second offer.
        assert canonical_offer_url(AMAZON_RUN_1) == canonical_offer_url(AMAZON_RUN_2)

    def test_different_listings_stay_apart(self):
        other = "https://www.amazon.de/LEGO-Supercar/dp/B09XVMSWJC/ref=sr_1_4?dib=eyJ2IjoiMSJ9.aN"
        assert canonical_offer_url(AMAZON_RUN_1) != canonical_offer_url(other)

    def test_gp_product_form(self):
        url = "https://www.amazon.de/gp/product/B09QFSCWD9?psc=1&smid=A3JWKAKR8XB7XF"
        assert canonical_offer_url(url) == "https://www.amazon.de/dp/B09QFSCWD9"

    def test_sponsored_click_wrapper_unwraps_to_asin(self):
        url = (
            "https://www.amazon.de/sspa/click?ie=UTF8&spc=MTo3NDk&sp_csd=d2lkZ2V0TmFtZQ"
            "&url=%2FLEGO-Technic%2Fdp%2FB09QFSCWD9%2Fref%3Dsr_1_1_sspa%3Fqid%3D1755640000"
        )
        assert canonical_offer_url(url) == "https://www.amazon.de/dp/B09QFSCWD9"

    def test_marketplace_host_is_preserved(self):
        url = "https://www.amazon.com/dp/B09QFSCWD9/ref=sr_1_1?dib=x"
        assert canonical_offer_url(url) == "https://www.amazon.com/dp/B09QFSCWD9"


class TestEbay:
    def test_reduces_to_item_id(self):
        assert canonical_offer_url(EBAY_RUN_1) == "https://www.ebay.de/itm/405882267931"

    def test_two_runs_of_same_listing_share_one_key(self):
        assert canonical_offer_url(EBAY_RUN_1) == canonical_offer_url(EBAY_RUN_2)

    def test_slug_form_reduces_to_item_id(self):
        url = "https://www.ebay.de/itm/LEGO-Star-Wars-75192/405882267931?hash=item5e80"
        assert canonical_offer_url(url) == "https://www.ebay.de/itm/405882267931"


class TestKleinanzeigen:
    def test_keeps_listing_path_drops_query(self):
        url = "https://www.kleinanzeigen.de/s-anzeige/lego-42143-ferrari/2891234567-23-45?utm_source=feed"
        assert canonical_offer_url(url) == "https://www.kleinanzeigen.de/s-anzeige/lego-42143-ferrari/2891234567-23-45"


class TestGenericAndEdgeCases:
    def test_unknown_host_drops_query_and_fragment(self):
        url = "https://shop.example.com/produkt/lego-42143?utm_campaign=x&sid=42#reviews"
        assert canonical_offer_url(url) == "https://shop.example.com/produkt/lego-42143"

    def test_trailing_slash_is_normalized(self):
        assert canonical_offer_url("https://shop.example.com/p/1/") == "https://shop.example.com/p/1"

    def test_host_case_and_www_do_not_split_identity(self):
        a = canonical_offer_url("https://WWW.Amazon.de/dp/B09QFSCWD9")
        b = canonical_offer_url("https://amazon.de/dp/B09QFSCWD9")
        assert a == b

    def test_is_idempotent(self):
        once = canonical_offer_url(AMAZON_RUN_1)
        assert canonical_offer_url(once) == once

    def test_empty_stays_empty(self):
        # The upsert skips URL-less offers; canonicalisation must not invent one.
        assert canonical_offer_url("") == ""
        assert canonical_offer_url(None) == ""

    def test_garbage_survives_without_raising(self):
        assert canonical_offer_url("not a url") == "not a url"


class TestOfferIdentity:
    def test_platform_is_part_of_the_key(self):
        assert offer_identity("AMAZON", AMAZON_RUN_1) != offer_identity("EBAY", AMAZON_RUN_1)

    def test_same_listing_same_platform_matches_across_runs(self):
        assert offer_identity("AMAZON", AMAZON_RUN_1) == offer_identity("AMAZON", AMAZON_RUN_2)

    def test_platform_case_is_normalized(self):
        assert offer_identity("amazon", AMAZON_RUN_1) == offer_identity("AMAZON", AMAZON_RUN_1)
