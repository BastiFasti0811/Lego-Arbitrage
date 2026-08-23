"""Condition has to reach the ROI, not just the risk score.

A used kingfisher without box or instructions was valued against 37,89 EUR —
the price a complete one fetches — and reported +57,4 % ROI at 10 EUR. The
condition was known to the risk scorer (2 of 10 points) and invisible to the
return calculation.
"""

from app.domain.condition import condition_value_factor
from app.engine.decision_engine import analyze_deal
from app.scrapers.base import ScrapedPrice


class TestConditionValueFactor:
    def test_sealed_keeps_the_full_market_price(self):
        assert condition_value_factor("NEW_SEALED") == 1.0

    def test_open_box_loses_a_little(self):
        assert condition_value_factor("NEW_OPEN_BOX") == 0.9

    def test_used_but_complete_loses_more(self):
        assert condition_value_factor("USED_COMPLETE") == 0.7

    def test_incomplete_is_worth_half(self):
        assert condition_value_factor("USED_INCOMPLETE") == 0.5

    def test_unknown_is_treated_as_used(self):
        # Private listings rarely say "sealed" when they mean it; assuming new
        # is the more expensive mistake.
        assert condition_value_factor("UNKNOWN") == 0.7

    def test_unrecognised_value_falls_back_to_unknown(self):
        assert condition_value_factor("WHATEVER") == condition_value_factor("UNKNOWN")

    def test_missing_value_falls_back_to_unknown(self):
        assert condition_value_factor(None) == condition_value_factor("UNKNOWN")

    def test_case_and_spacing_do_not_matter(self):
        assert condition_value_factor("  new_sealed ") == 1.0

    def test_damaged_box_costs_extra(self):
        assert condition_value_factor("NEW_SEALED", box_damage=True) == 0.9

    def test_damage_compounds_with_condition(self):
        assert condition_value_factor("USED_COMPLETE", box_damage=True) == round(0.7 * 0.9, 4)

    def test_factor_stays_within_bounds(self):
        for condition in ("NEW_SEALED", "NEW_OPEN_BOX", "USED_COMPLETE", "USED_INCOMPLETE", "UNKNOWN"):
            for damage in (False, True):
                assert 0.0 < condition_value_factor(condition, box_damage=damage) <= 1.0


def _prices():
    return [
        ScrapedPrice(source="EBAY_SOLD", price_eur=37.89, currency="EUR", is_reliable=True),
        ScrapedPrice(source="BRICKMERGE", price_eur=37.89, currency="EUR", is_reliable=True),
    ]


class TestConditionReachesTheRoi:
    """The kingfisher case: same price, same set, different condition."""

    def _roi(self, condition):
        analysis = analyze_deal(
            set_number="10331",
            set_name="Eisvogel",
            release_year=2023,
            theme="Icons",
            offer_price=10.0,
            prices=_prices(),
            uvp=19.99,
            condition=condition,
            purchase_shipping=6.19,
        )
        return analysis.roi.roi_percent

    def test_used_incomplete_earns_less_than_sealed(self):
        assert self._roi("USED_INCOMPLETE") < self._roi("NEW_SEALED")

    def test_the_ordering_holds_across_all_conditions(self):
        sealed = self._roi("NEW_SEALED")
        open_box = self._roi("NEW_OPEN_BOX")
        used = self._roi("USED_COMPLETE")
        incomplete = self._roi("USED_INCOMPLETE")

        assert sealed > open_box > used > incomplete

    def test_sealed_result_is_unchanged_by_the_new_rule(self):
        # The factor is 1.0 for sealed, so existing valuations must not move.
        analysis = analyze_deal(
            set_number="10331",
            set_name="Eisvogel",
            release_year=2023,
            theme="Icons",
            offer_price=10.0,
            prices=_prices(),
            uvp=19.99,
            condition="NEW_SEALED",
        )
        assert analysis.expected_sale_price == analysis.market_consensus.consensus_price

    def test_expected_sale_price_is_reported(self):
        # The card shows this number; without it a reduced ROI looks arbitrary.
        analysis = analyze_deal(
            set_number="10331",
            set_name="Eisvogel",
            release_year=2023,
            theme="Icons",
            offer_price=10.0,
            prices=_prices(),
            uvp=19.99,
            condition="USED_INCOMPLETE",
        )
        assert analysis.expected_sale_price < analysis.market_consensus.consensus_price
        assert analysis.expected_sale_price > 0


class TestConditionAliases:
    """Der risk_scorer kannte diese Schreibweisen laengst, die Wertseite nicht."""

    def test_spellings_that_mean_sealed(self):
        for value in ("NEU", "MISB", "NEW", "SEALED"):
            assert condition_value_factor(value) == 1.0, value

    def test_spellings_that_mean_open_box(self):
        # "NEW_OPEN" erzeugt analysis.py aktiv — es kostete still 30 % Wert.
        for value in ("OVP", "UNGEOEFFNET", "NEW_OPEN"):
            assert condition_value_factor(value) == 0.9, value

    def test_the_risk_scorer_agrees_with_the_value_side(self):
        from app.engine.risk_scorer import calculate_risk_score

        # Gleiche Bedeutung, gleiche Behandlung — frueher liefen die Tabellen auseinander.
        def risk_for(condition):
            return calculate_risk_score(set_age=1, eol_status="ACTIVE", condition=condition).condition_risk

        for sealed_spelling in ("NEW_SEALED", "NEU", "MISB"):
            assert risk_for(sealed_spelling) == 0, sealed_spelling
        for open_spelling in ("NEW_OPEN_BOX", "OVP", "NEW_OPEN"):
            assert risk_for(open_spelling) == 1, open_spelling

    def test_an_unmapped_spelling_falls_back_to_unknown(self):
        assert condition_value_factor("VOELLIG NEUE SCHREIBWEISE") == 0.7
