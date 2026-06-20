"""LEGO set age and category helpers."""

from datetime import date

CATEGORY_FRESH = "FRESH"
CATEGORY_SWEET_SPOT = "SWEET_SPOT"
CATEGORY_ESTABLISHED = "ESTABLISHED"
CATEGORY_VINTAGE = "VINTAGE"
CATEGORY_LEGACY = "LEGACY"


def current_year() -> int:
    """Return the calendar year used for age-sensitive calculations."""
    return date.today().year


def set_age(release_year: int, as_of_year: int | None = None) -> int:
    """Calculate a LEGO set's age in years."""
    return (as_of_year or current_year()) - release_year


def categorize_release_year(release_year: int, as_of_year: int | None = None) -> str:
    """Determine the investment category for a release year."""
    age = set_age(release_year, as_of_year)
    if age <= 1:
        return CATEGORY_FRESH
    if age <= 4:
        return CATEGORY_SWEET_SPOT
    if age <= 7:
        return CATEGORY_ESTABLISHED
    if age <= 11:
        return CATEGORY_VINTAGE
    return CATEGORY_LEGACY

