from app.domain.classification import categorize_release_year, set_age


def test_set_age_uses_supplied_year_for_stable_tests():
    assert set_age(2022, as_of_year=2026) == 4


def test_categorize_release_year_boundaries():
    assert categorize_release_year(2026, as_of_year=2026) == "FRESH"
    assert categorize_release_year(2022, as_of_year=2026) == "SWEET_SPOT"
    assert categorize_release_year(2019, as_of_year=2026) == "ESTABLISHED"
    assert categorize_release_year(2015, as_of_year=2026) == "VINTAGE"
    assert categorize_release_year(2014, as_of_year=2026) == "LEGACY"

