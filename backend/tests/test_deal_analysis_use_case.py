import pytest

from app.scrapers.base import ScrapedPrice
from app.services.deal_analysis import DealAnalysisCommand, DealAnalysisUseCase, MarketContext


class _FakeProvider:
    async def load(self, command):
        return MarketContext(
            prices=[
                ScrapedPrice(source="EBAY_SOLD", price_eur=150.0, sold_count=12),
                ScrapedPrice(source="BRICKMERGE", price_eur=148.0),
            ],
            set_name=f"LEGO {command.set_number}",
            theme="Star Wars",
            release_year=2022,
            uvp=120.0,
            eol_status="RETIRED",
            monthly_sales=6,
            still_in_retail=False,
            detected_platform="EBAY",
        )


class _FakeRepository:
    def __init__(self):
        self.upserted = False

    async def feedback_calibration(self):
        return None, 0

    async def upsert_set_from_analysis(self, _analysis, _eol_status):
        self.upserted = True


@pytest.mark.asyncio
async def test_deal_analysis_use_case_runs_analysis_and_persists_snapshot():
    repository = _FakeRepository()
    use_case = DealAnalysisUseCase(_FakeProvider(), repository)

    outcome = await use_case.execute(DealAnalysisCommand(set_number="75313", offer_price=90.0))

    assert outcome.analysis.set_number == "75313"
    assert outcome.analysis.market_consensus.num_sources == 2
    assert repository.upserted is True
