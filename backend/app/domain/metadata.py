"""Helpers for merging scraped LEGO set metadata."""


def merge_set_info(
    *,
    info,
    set_number: str,
    set_name: str,
    theme: str,
    release_year: int,
    uvp: float | None,
    eol_status: str,
) -> tuple[str, str, int, float | None, str]:
    """Merge a scraper's partial set metadata into the current best values."""
    if info.set_name and (not set_name or set_name in {set_number, f"LEGO {set_number}"}):
        set_name = info.set_name
    if info.theme and (theme == "Unknown" or not theme):
        theme = info.theme
    if info.release_year and (release_year == 2020 or not release_year):
        release_year = info.release_year
    if info.uvp_eur and not uvp:
        uvp = info.uvp_eur
    if info.eol_status and eol_status == "UNKNOWN":
        eol_status = info.eol_status
    return set_name, theme, release_year, uvp, eol_status


def needs_metadata_retry(theme: str, release_year: int, uvp: float | None, eol_status: str) -> bool:
    """Return True when the current metadata still looks like fallback data."""
    return theme == "Unknown" or release_year == 2020 or not uvp or eol_status == "UNKNOWN"
