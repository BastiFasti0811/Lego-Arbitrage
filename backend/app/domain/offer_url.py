"""Reduce a marketplace URL to the listing it actually points at.

Search-result links are not stable identifiers. Amazon appends a per-request
`dib` token, a `ref=sr_1_N` position marker and a `qid` timestamp; eBay appends
`itmmeta` and an encrypted `itmprp`. Fetch the same result page twice and every
href differs, even though the listing behind it is unchanged.

Offers are upserted under `(platform, offer_url)`, so those volatile parameters
meant the key never matched an existing row: each scrape run inserted the set
again, each copy carrying the ROI and score of its own run. The live feed then
showed one Amazon offer four times at four different scores.

What stays constant is the listing id — the ASIN for Amazon, the item id for
eBay, the ad path for Kleinanzeigen. That is what this module extracts.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass
from urllib.parse import parse_qs, unquote, urlsplit

# Hosts that answer under both apex and www; pinning one form keeps a listing
# from splitting in two just because a scraper followed a different link.
_CANONICAL_HOSTS = {
    "amazon.de": "www.amazon.de",
    "amazon.com": "www.amazon.com",
    "ebay.de": "www.ebay.de",
    "ebay.com": "www.ebay.com",
    "kleinanzeigen.de": "www.kleinanzeigen.de",
    "ebay-kleinanzeigen.de": "www.kleinanzeigen.de",
}

_AMAZON_HOSTS = ("amazon.de", "amazon.com")
_EBAY_HOSTS = ("ebay.de", "ebay.com")

# /dp/B09QFSCWD9, /gp/product/B09QFSCWD9, /gp/aw/d/B09QFSCWD9
_ASIN_RE = re.compile(r"/(?:dp|gp/product|gp/aw/d|gp/offer-listing)/([A-Za-z0-9]{10})(?:[/?#]|$)")
# /itm/405882267931 and /itm/LEGO-Star-Wars-75192/405882267931
_EBAY_ITEM_RE = re.compile(r"/itm/(?:[^/]+/)?(\d{6,})(?:[/?#]|$)")


def _canonical_host(netloc: str) -> str:
    host = netloc.lower()
    bare = host[4:] if host.startswith("www.") else host
    return _CANONICAL_HOSTS.get(bare, bare)


def _amazon_path(path: str, query: str) -> str | None:
    """ASIN from the path, or from the target wrapped in a /sspa/click link."""
    match = _ASIN_RE.search(path)
    if match:
        return f"/dp/{match.group(1).upper()}"

    # Sponsored results hide the real target in a url= parameter.
    if "/sspa/" in path:
        wrapped = parse_qs(query).get("url", [""])[0]
        if wrapped:
            match = _ASIN_RE.search(unquote(wrapped))
            if match:
                return f"/dp/{match.group(1).upper()}"
    return None


def canonical_offer_url(url: str | None) -> str:
    """Return the listing's stable URL, or the input unchanged if unparseable.

    Never invents a URL: empty input stays empty, because the offer upsert uses
    an empty URL as its signal to skip a row rather than key it on nothing.
    """
    if not url:
        return ""

    raw = url.strip()
    try:
        parts = urlsplit(raw)
    except ValueError:
        return raw

    # Relative links and free text have no authority to build a key from.
    if not parts.scheme or not parts.netloc:
        return raw

    host = _canonical_host(parts.netloc)
    path = parts.path

    if host.endswith(_AMAZON_HOSTS):
        path = _amazon_path(path, parts.query) or path.rstrip("/")
    elif host.endswith(_EBAY_HOSTS):
        match = _EBAY_ITEM_RE.search(path)
        path = f"/itm/{match.group(1)}" if match else path.rstrip("/")
    else:
        # Unknown shops: the path identifies the article, the query is tracking.
        path = path.rstrip("/")

    return f"{parts.scheme.lower()}://{host}{path}"


def offer_identity(platform: str | None, url: str | None) -> str:
    """Dedupe key for an offer — same listing, same platform, one entry."""
    return f"{(platform or '').upper()}:{canonical_offer_url(url)}"


@dataclass(frozen=True)
class CleanupGroup:
    """One real listing and the duplicate rows that accumulated for it."""

    keep_id: int
    canonical_url: str
    drop_ids: tuple[int, ...]


def plan_duplicate_cleanup(rows: Iterable[tuple]) -> list[CleanupGroup]:
    """Decide which stored offer row survives per listing.

    Takes `(id, set_id, platform, offer_url, last_seen_at)` tuples and groups
    them by the listing they point at. The freshest row wins, because it holds
    the most recent analysis; ties go to the highest id, the one written last.

    Rows without a URL have no identity to group on and are left untouched —
    a cleanup must not delete what it cannot identify.
    """
    groups: dict[tuple[int, str, str], list[tuple]] = {}
    for row in rows:
        offer_id, set_id, platform, offer_url, last_seen_at = row
        canonical = canonical_offer_url(offer_url)
        if not canonical:
            continue
        groups.setdefault((set_id, (platform or "").upper(), canonical), []).append(
            (offer_id, last_seen_at)
        )

    plan: list[CleanupGroup] = []
    for (_set_id, _platform, canonical), members in groups.items():
        # datetime and None do not compare, so absent timestamps sort oldest.
        members.sort(key=lambda item: (item[1] is not None, item[1], item[0]), reverse=True)
        keep_id = members[0][0]
        plan.append(
            CleanupGroup(
                keep_id=keep_id,
                canonical_url=canonical,
                drop_ids=tuple(member[0] for member in members[1:]),
            )
        )
    return plan
