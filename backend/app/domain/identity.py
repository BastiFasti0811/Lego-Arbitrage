"""Decide whether a scraped offer really is the LEGO set it was found for.

Marketplace searches for a set number return the set itself plus everything
built around it: lighting kits, wall mounts, display cases, empty boxes. Those
listings carry the set number in their title, so without this check a 9,99 EUR
wall mount was valued against the 400 EUR set and reported as a 869 % ROI
"GO_STAR" deal.

The filter has to stay usable in the other direction too: a genuine bargain is
the whole point of the system, and plenty of real listings mention an add-on
("mit LED-Beleuchtung", "Aufkleber ungenutzt"). Hence two tiers of markers and
a deliberately low price floor.
"""

import re

from app.scrapers.base import MIN_PLAUSIBLE_SET_PRICE

# Phrases that never occur in a listing selling the set itself.
_HARD_ACCESSORY_PATTERNS = (
    r"kompatibel\s+mit",
    r"compatible\s+with",
    r"kein\s+lego",
    r"not\s+lego",
    r"passend\s+f[üu]r\s+lego",
    r"wandhalterung",
    r"wall[\s-]*mount",
    r"display[\s-]*case",
    r"schaukasten",
    r"staubschutz",
    r"leer(er)?[\s-]*karton",
    r"nur\s+(der\s+)?karton",
    r"nur\s+(die\s+)?anleitung",
    r"nur\s+(die\s+)?verpackung",
    r"only\s+manual",
    r"box\s+only",
    r"upgrade[\s-]*kit",
    r"motorisierung",
)

# Phrases that describe an accessory when it IS the product, but appear just as
# often as a bonus in a real set listing ("Set inkl. Lichtset"). They only
# disqualify a listing together with a second signal.
_SOFT_ACCESSORY_PATTERNS = (
    r"led[\s-]*licht",
    r"led[\s-]*light",
    r"licht[\s-]*set",
    r"light[\s-]*kit",
    r"beleuchtungs?[\s-]*(set|kit)",
    r"vitrine",
    r"acryl",
    r"plexiglas",
    r"aufkleber",
    r"sticker",
    r"ersatzteil",
    r"spare\s+part",
    r"halterung",
    r"halter\b",
    r"standfu[ßs]",
)

# Multi-set lots cannot be valued against a single set's market price.
_BUNDLE_PATTERNS = (
    r"konvolut",
    r"sammlung",
    r"\bpaket\b",
    r"\d+\s*x\s*lego",
    r"\bbundle\b",
)

_HARD_RE = re.compile("|".join(_HARD_ACCESSORY_PATTERNS), re.IGNORECASE)
_SOFT_RE = re.compile("|".join(_SOFT_ACCESSORY_PATTERNS), re.IGNORECASE)
_BUNDLE_RE = re.compile("|".join(_BUNDLE_PATTERNS), re.IGNORECASE)

# Below this share of the set's own market value a listing cannot be the set.
# Kept low on purpose: a 400 EUR set offered at 90 EUR (22 %) is exactly the
# find the pipeline exists for, while accessories sit an order of magnitude
# lower. The floor only catches accessory types we have no keyword for.
MIN_PRICE_RATIO = 0.08

# A soft marker plus a price this far below market means the add-on IS the
# product, not a bonus.
SOFT_MARKER_PRICE_RATIO = 0.35

_TOKEN_RE = re.compile(r"[a-zäöüß0-9]+", re.IGNORECASE)
_NAME_STOPWORDS = {"lego", "der", "die", "das", "und", "mit", "von", "the", "set", "de"}


def _normalize_number(text: str) -> str:
    """Digits only — matches '42 143' and '42-143' to '42143'."""
    return re.sub(r"[^0-9]", "", text)


def names_match(title: str, set_name: str | None) -> bool:
    """Whether the title carries enough of the set's name to identify it.

    Private sellers routinely omit the set number ("LEGO Technic Ferrari
    Daytona SP3 Neu OVP"), so the number alone is too strict a requirement.
    """
    if not set_name:
        return False
    tokens = {t.lower() for t in _TOKEN_RE.findall(set_name) if len(t) > 2}
    tokens -= _NAME_STOPWORDS
    if len(tokens) < 2:
        return False
    title_lower = title.lower()
    hits = sum(1 for token in tokens if token in title_lower)
    return hits >= 2


def looks_like_accessory(title: str, price_ratio: float | None = None) -> bool:
    """Whether the listing sells an add-on rather than the set."""
    if _HARD_RE.search(title or ""):
        return True
    if _SOFT_RE.search(title or "") and price_ratio is not None and price_ratio < SOFT_MARKER_PRICE_RATIO:
        return True
    return False


def looks_like_bundle(title: str) -> bool:
    """Whether the listing sells a lot of several sets."""
    return bool(_BUNDLE_RE.search(title or ""))


def is_set_offer(
    title: str,
    set_number: str,
    price_eur: float | None = None,
    reference_price: float | None = None,
    set_name: str | None = None,
) -> bool:
    """Whether this listing plausibly sells the set itself."""
    if not title:
        return False

    if set_number not in title and _normalize_number(set_number) not in _normalize_number(title):
        if not names_match(title, set_name):
            return False

    price_ratio = None
    if price_eur is not None and reference_price and reference_price > 0:
        price_ratio = price_eur / reference_price

    if looks_like_accessory(title, price_ratio):
        return False

    if looks_like_bundle(title):
        return False

    if price_ratio is not None and price_ratio < MIN_PRICE_RATIO:
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
