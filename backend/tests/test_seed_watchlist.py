from app.tools.seed_watchlist import SETS


def test_seed_entries_carry_all_required_lego_set_fields():
    # lego_sets requires set_number, set_name, theme AND release_year — a seed
    # entry missing any of them dies on the NOT NULL constraint in production.
    assert len(SETS) >= 30
    numbers = [entry[0] for entry in SETS]
    assert len(numbers) == len(set(numbers))
    for set_number, name, theme, release_year in SETS:
        assert set_number.strip()
        assert name.strip()
        assert theme.strip()
        assert 2015 <= release_year <= 2026
