"""Marketplace platform detection helpers."""


def detect_source_platform(source_url: str | None, source_platform: str | None = None) -> str | None:
    """Infer the marketplace platform from an explicit value or URL."""
    if source_platform:
        return source_platform.upper()
    if not source_url:
        return None

    lowered = source_url.lower()
    if "catawiki" in lowered:
        return "CATAWIKI"
    if "kleinanzeigen" in lowered:
        return "KLEINANZEIGEN"
    if "ebay" in lowered:
        return "EBAY"
    if "amazon" in lowered:
        return "AMAZON"
    if "whatnot" in lowered:
        return "WHATNOT"
    if "bricklink" in lowered:
        return "BRICKLINK"
    if "lego.com" in lowered:
        return "LEGO"
    return "UNKNOWN"

