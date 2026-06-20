"""URL allowlist and local-network protection for outbound fetches."""

from __future__ import annotations

import ipaddress
import socket
from urllib.parse import urlparse


class UnsafeUrlError(ValueError):
    """Raised when a URL is not allowed for outbound fetching."""


ALLOWED_HOSTS_BY_PLATFORM: dict[str, set[str]] = {
    "AMAZON": {"amazon.de", "amazon.com"},
    "BRICKECONOMY": {"brickeconomy.com"},
    "BRICKLINK": {"bricklink.com"},
    "BRICKMERGE": {"brickmerge.de"},
    "CATAWIKI": {"catawiki.com"},
    "EBAY": {"ebay.de", "ebay.com"},
    "IDEALO": {"idealo.de"},
    "KLEINANZEIGEN": {"kleinanzeigen.de"},
    "LEGO": {"lego.com"},
    "LEGO_COM": {"lego.com"},
    "WHATNOT": {"whatnot.com"},
}

SCRAPER_PLATFORM_BY_NAME = {
    "AmazonScraper": "AMAZON",
    "BrickEconomyScraper": "BRICKECONOMY",
    "BrickLinkScraper": "BRICKLINK",
    "BrickMergeScraper": "BRICKMERGE",
    "CatawikiScraper": "CATAWIKI",
    "EbaySoldScraper": "EBAY",
    "IdealoScraper": "IDEALO",
    "KleinanzeigenScraper": "KLEINANZEIGEN",
    "LegoComScraper": "LEGO",
    "WhatnotScraper": "WHATNOT",
}


def validate_marketplace_url(url: str, platform: str | None = None) -> str:
    """Validate and return a URL that is safe to fetch."""
    normalized_platform = platform.upper() if platform else None
    return _validate_url(url, normalized_platform)


def validate_url_for_scraper(url: str, scraper_name: str) -> str:
    """Validate a scraper URL against its known marketplace host."""
    platform = SCRAPER_PLATFORM_BY_NAME.get(scraper_name)
    return _validate_url(url, platform)


def _validate_url(url: str, platform: str | None) -> str:
    parsed = urlparse((url or "").strip())
    if parsed.scheme not in {"http", "https"}:
        raise UnsafeUrlError("Nur http/https URLs sind erlaubt")
    if not parsed.hostname:
        raise UnsafeUrlError("URL enthaelt keinen Host")

    host = parsed.hostname.rstrip(".").lower()
    if _is_blocked_host(host):
        raise UnsafeUrlError("Lokale oder private Hosts duerfen nicht abgerufen werden")

    allowed_hosts = ALLOWED_HOSTS_BY_PLATFORM.get(platform or "")
    if allowed_hosts and not _host_matches(host, allowed_hosts):
        raise UnsafeUrlError(f"Host {host} ist fuer {platform} nicht erlaubt")

    _ensure_resolved_ips_are_public(host)
    return url


def _host_matches(host: str, allowed_hosts: set[str]) -> bool:
    return any(host == allowed_host or host.endswith(f".{allowed_host}") for allowed_host in allowed_hosts)


def _is_blocked_host(host: str) -> bool:
    if host in {"localhost", "localhost.localdomain"} or host.endswith(".localhost"):
        return True

    try:
        return _is_blocked_ip(ipaddress.ip_address(host))
    except ValueError:
        return False


def _ensure_resolved_ips_are_public(host: str) -> None:
    try:
        addresses = socket.getaddrinfo(host, None, type=socket.SOCK_STREAM)
    except socket.gaierror:
        return

    for address in addresses:
        ip = ipaddress.ip_address(address[4][0])
        if _is_blocked_ip(ip):
            raise UnsafeUrlError("URL loest auf eine lokale oder private Adresse auf")


def _is_blocked_ip(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    return any(
        (
            ip.is_loopback,
            ip.is_private,
            ip.is_link_local,
            ip.is_multicast,
            ip.is_reserved,
            ip.is_unspecified,
        )
    )
