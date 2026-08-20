"""Decide whether a scraped offer really is the LEGO set it was found for.

Marketplace searches for a set number return the set itself plus everything
built around it: lighting kits, wall mounts, display cases, stickers. Those
listings carry the set number in their title, so without this check a 9,99 EUR
wall mount was valued against the 400 EUR set and reported as a 869 % ROI
"GO_STAR" deal.
"""

import re

from app.scrapers.base import MIN_PLAUSIBLE_SET_PRICE

# Unambiguous accessory markers. Kept narrow on purpose: a real listing may
# well say "mit Anleitung" or "Lichtschwert", so only phrases that describe
# the product itself qualify.
_ACCESSORY_PATTERNS = (
    r"kompatibel\s+mit",
    r"compatible\s+with",
    r"kein\s+lego",
    r"not\s+lego",
    r"led[\s-]*licht",
    r"led[\s-]*light",
    r"licht[\s-]*set",
    r"light[\s-]*kit",
    r"beleuchtungs?[\s-]*(set|kit)",
    r"beleuchtung\s+f[üu]r",
    r"wandhalterung",
    r"halterung",
    r"wall[\s-]*mount",
    r"vitrine",
    r"display[\s-]*case",
    r"schaukasten",
    r"acryl",
    r"plexiglas",
    r"staubschutz",
    r"aufkleber",
    r"sticker",
    r"nur\s+(die\s+)?anleitung",
    r"only\s+manual",
    r"nur\s+(der\s+)?karton",
    r"leerer\s+karton",
    r"ersatzteil",
    r"spare\s+part",
    r"motorisierung",
    r"upgrade[\s-]*kit",
)

_ACCESSORY_RE = re.compile("|".join(_ACCESSORY_PATTERNS), re.IGNORECASE)

# An offer below this share of the set's reference price cannot be the set.
# Real bargains — the whole point of the system — sit far above it.
MIN_PRICE_RATIO = 0.25


def looks_like_accessory(title: str) -> bool:
    """True if the title describes an add-on rather than the set."""
    return bool(_ACCESSORY_RE.search(title or ""))


def is_set_offer(
    title: str,
    set_number: str,
    price_eur: float | None = None,
    reference_price: float | None = None,
) -> bool:
    """Whether this listing plausibly sells the set itself.

    Three filters: the set number must be named, the title must not describe
    an accessory, and the price must not be far below the set's own market
    value (catches accessory types we have no keyword for).
    """
    if not title or set_number not in title:
        return False

    if looks_like_accessory(title):
        return False

    if price_eur and reference_price and reference_price > 0:
        if price_eur < reference_price * MIN_PRICE_RATIO:
            return False

    return True


def is_plausible_price(price_eur: float, uvp_eur: float | None) -> bool:
    """Reject market prices that cannot belong to this set.

    Guards consensus sources (not offers): below 20 % of UVP is a parsing
    accident, not a bargain. Upward outliers are legitimate (EOL premiums),
    so only the lower bound is guarded. An implausible UVP is itself scraped
    data and is discarded as an anchor rather than rejecting a correct price.
    """
    if not uvp_eur or uvp_eur < MIN_PLAUSIBLE_SET_PRICE:
        return True
    return price_eur >= uvp_eur * 0.20
