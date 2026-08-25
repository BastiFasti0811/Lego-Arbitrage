"""One listing must stay one row and one feed card, across scrape runs.

Amazon and eBay hand out a fresh tracking token on every search request, so the
raw href changed each run. The upsert key `(platform, offer_url)` never matched,
every run inserted the same offer again, and the live feed showed one Ferrari
listing four times — each copy carrying the ROI and score of the run that
created it.
"""

from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from app.api.routes import scout
from app.scrapers.base import ScrapedOffer
from app.tasks import scrape_daily

SET_NUMBER = "42143"
TITLE = "LEGO Technic 42143 Ferrari Daytona SP3 Modellauto"

# The same Amazon listing as returned by two consecutive scrape runs.
RUN_1 = "https://www.amazon.de/42143-Technic-Ferrari/dp/B09QFSCWD9/ref=sr_1_1?dib=eyJ2IjoiMSJ9.AAA&qid=1755640000"
RUN_2 = "https://www.amazon.de/42143-Technic-Ferrari/dp/B09QFSCWD9/ref=sr_1_5?dib=eyJ2IjoiMSJ9.ZZZ&qid=1755647777"
CANONICAL = "https://www.amazon.de/dp/B09QFSCWD9"


class _FakeScalars:
    def __init__(self, items):
        self._items = items

    def all(self):
        return self._items


class _RowsResult:
    def __init__(self, items):
        self._items = items

    def scalars(self):
        return _FakeScalars(self._items)

    def all(self):
        return self._items


class _RecordingSession:
    """Returns whatever rows the test seeded, records what gets inserted."""

    def __init__(self, rows=()):
        self.rows = list(rows)
        self.added = []

    async def execute(self, _query):
        return _RowsResult(self.rows)

    def add(self, obj):
        self.added.append(obj)


def _lego_set():
    return SimpleNamespace(
        id=1,
        set_number=SET_NUMBER,
        set_name="Ferrari Daytona SP3",
        theme="Technic",
        current_market_price=376.99,
        uvp_eur=399.99,
    )


def _scraped(url):
    return SimpleNamespace(
        platform="AMAZON",
        offer_url=url,
        offer_title=TITLE,
        price_eur=289.0,
        shipping_eur=0.0,
        condition="NEW_SEALED",
        box_damage=False,
        sealed=True,
        seller_name="Amazon.de",
        seller_rating=95.0,
        seller_location=None,
        is_auction=False,
        auction_end=None,
    )


class TestScrapedOfferNormalizesAtTheSource:
    def test_tracking_parameters_are_stripped_on_construction(self):
        offer = ScrapedOffer(platform="AMAZON", offer_url=RUN_1, offer_title=TITLE, price_eur=289.0)
        assert offer.offer_url == CANONICAL

    def test_two_runs_produce_the_same_url(self):
        first = ScrapedOffer(platform="AMAZON", offer_url=RUN_1, offer_title=TITLE, price_eur=289.0)
        second = ScrapedOffer(platform="AMAZON", offer_url=RUN_2, offer_title=TITLE, price_eur=289.0)
        assert first.offer_url == second.offer_url

    def test_missing_url_stays_empty(self):
        offer = ScrapedOffer(platform="AMAZON", offer_url="", offer_title=TITLE, price_eur=289.0)
        assert offer.offer_url == ""


class TestUpsertKey:
    @pytest.mark.asyncio
    async def test_second_run_updates_instead_of_inserting(self):
        # The regression: run 2 carries a different tracking token for a listing
        # already stored under its canonical URL.
        existing = SimpleNamespace(platform="AMAZON", offer_url=CANONICAL, price_eur=310.0, last_seen_at=None)
        session = _RecordingSession([existing])
        now = datetime.now(UTC)

        count = await scrape_daily._upsert_offers(session, _lego_set(), [_scraped(RUN_2)], now)

        assert count == 1
        assert session.added == [], "a known listing must not be inserted a second time"
        assert existing.price_eur == 289.0
        assert existing.last_seen_at == now

    @pytest.mark.asyncio
    async def test_new_listing_is_stored_under_its_canonical_url(self):
        session = _RecordingSession([])

        count = await scrape_daily._upsert_offers(session, _lego_set(), [_scraped(RUN_1)], datetime.now(UTC))

        assert count == 1
        assert len(session.added) == 1
        assert session.added[0].offer_url == CANONICAL


class TestFeedDedupe:
    def test_legacy_rows_of_one_listing_collapse_to_one_card(self):
        # Rows written before canonicalisation still carry raw URLs. The feed has
        # to fold them together, otherwise the fix only helps for future scrapes.
        lego_set = _lego_set()
        rows = [
            (
                SimpleNamespace(
                    platform="AMAZON",
                    offer_url=url,
                    offer_title=TITLE,
                    price_eur=289.0,
                    shipping_eur=0.0,
                    estimated_roi=roi,
                    risk_score=4,
                    recommendation="GO",
                    analysis_notes="ok",
                    condition="NEW_SEALED",
                    box_damage=False,
                    last_seen_at=datetime.now(UTC),
                ),
                lego_set,
            )
            for url, roi in ((RUN_1, 45.0), (RUN_2, 22.0), (CANONICAL, 30.0))
        ]
        request = scout.ScoutRequest(set_numbers=[SET_NUMBER], min_roi=0, cached_only=True)

        response = scout.build_feed(rows, request)

        assert len(response.deals) == 1, "same listing, three stored rows, one card"
        assert response.deals[0].estimated_roi == 45.0, "the freshest row wins"

    def test_distinct_listings_are_kept(self):
        lego_set = _lego_set()
        rows = [
            (
                SimpleNamespace(
                    platform="AMAZON",
                    offer_url=url,
                    offer_title=TITLE,
                    price_eur=289.0,
                    shipping_eur=0.0,
                    estimated_roi=30.0,
                    risk_score=4,
                    recommendation="GO",
                    analysis_notes="ok",
                    condition="NEW_SEALED",
                    box_damage=False,
                    last_seen_at=datetime.now(UTC),
                ),
                lego_set,
            )
            for url in ("https://www.amazon.de/dp/B09QFSCWD9", "https://www.amazon.de/dp/B09XVMSWJC")
        ]
        request = scout.ScoutRequest(set_numbers=[SET_NUMBER], min_roi=0, cached_only=True)

        response = scout.build_feed(rows, request)

        assert len(response.deals) == 2


class TestFeedRejectsImplausibleLegacyRows:
    """Rows stored before the accessory filter existed still sit in the DB.

    A 9,99 EUR wall mount valued against a 377 EUR set produced the +2973 %
    cards that dominated the feed. The scrape path stopped writing them, but
    the read path never re-checked what was already stored.
    """

    def _row(self, title, price, lego_set):
        return (
            SimpleNamespace(
                platform="AMAZON",
                offer_url=f"https://www.amazon.de/dp/{abs(hash(title)) % 10**10:010d}",
                offer_title=title,
                price_eur=price,
                shipping_eur=0.0,
                estimated_roi=2973.1,
                risk_score=5,
                recommendation="CHECK",
                analysis_notes="",
                condition="NEW_SEALED",
                box_damage=False,
                last_seen_at=datetime.now(UTC),
            ),
            lego_set,
        )

    def test_accessory_priced_row_is_not_shown(self):
        lego_set = _lego_set()
        rows = [self._row("LEGO 42143 Wandhalterung, kompatibel mit Ferrari Daytona", 9.99, lego_set)]
        request = scout.ScoutRequest(set_numbers=[SET_NUMBER], min_roi=0, cached_only=True)

        response = scout.build_feed(rows, request)

        assert response.deals == []

    def test_genuine_listing_still_passes(self):
        lego_set = _lego_set()
        rows = [self._row(TITLE, 289.0, lego_set)]
        request = scout.ScoutRequest(set_numbers=[SET_NUMBER], min_roi=0, cached_only=True)

        response = scout.build_feed(rows, request)

        assert len(response.deals) == 1


def test_both_pipelines_canonicalise_urls():
    # Mirrors test_offer_identity's guard: a new scrape path must not silently
    # skip the dedupe key and start writing duplicates again.
    import inspect

    for module in (scrape_daily, scout):
        source = inspect.getsource(module)
        assert "canonical_offer_url" in source or "offer_identity" in source, module.__name__
