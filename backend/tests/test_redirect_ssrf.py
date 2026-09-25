"""Befund 6: Jeder Redirect-Hop wird VOR dem Abruf geprueft.

Vorher lief `follow_redirects=True`, und geprueft wurde nur die Start-URL und
danach `response.url`. Ein Redirect auf `http://127.0.0.1/` oder
`http://redis:6379/` war da schon abgerufen.
"""

import httpx
import pytest

from app.scrapers import base as scraper_base
from app.scrapers import brickmerge
from app.scrapers.brickeconomy import BrickEconomyScraper
from app.scrapers.brickmerge import BrickMergeScraper
from app.security import http_guard
from app.security.url_policy import UnsafeUrlError, _is_blocked_host, validate_marketplace_url
from app.services import fx


@pytest.fixture(autouse=True)
def no_dns(monkeypatch):
    # Die Tests pruefen die Hop-Logik, nicht das DNS der Testmaschine.
    monkeypatch.setattr("app.security.url_policy._ensure_resolved_ips_are_public", lambda _host: None)


class RecordingTransport(httpx.MockTransport):
    """MockTransport, der mitschreibt, welche URLs wirklich abgerufen wurden."""

    def __init__(self, routes: dict[str, httpx.Response]):
        self.seen: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            url = str(request.url)
            self.seen.append(url)
            if url not in routes:
                return httpx.Response(200, text="<!doctype html><html><body>intern</body></html>")
            return routes[url]

        super().__init__(handler)


def _redirect(location: str) -> httpx.Response:
    return httpx.Response(302, headers={"Location": location})


def _page(body: str = "<!doctype html><html><body>LEGO</body></html>") -> httpx.Response:
    return httpx.Response(200, text=body)


def _inject(monkeypatch, module, transport):
    def with_transport(validate, **kwargs):
        return http_guard.guarded_async_client(validate, transport=transport, **kwargs)

    monkeypatch.setattr(module, "guarded_async_client", with_transport)


# ── Der Guard selbst ───────────────────────────────────────────


@pytest.mark.asyncio
async def test_redirect_to_loopback_is_blocked_before_the_hop_is_fetched():
    transport = RecordingTransport({"https://www.ebay.de/start": _redirect("http://127.0.0.1/")})
    client = http_guard.guarded_async_client(
        lambda url: validate_marketplace_url(url, "EBAY"), transport=transport
    )

    async with client:
        with pytest.raises(UnsafeUrlError):
            await client.get("https://www.ebay.de/start")

    assert transport.seen == ["https://www.ebay.de/start"]


@pytest.mark.asyncio
async def test_relative_redirect_on_an_allowed_host_still_works():
    transport = RecordingTransport({
        "https://www.ebay.de/a": _redirect("/b"),
        "https://www.ebay.de/b": _page("<!doctype html><html><body>ziel</body></html>"),
    })

    async with http_guard.guarded_async_client(
        lambda url: validate_marketplace_url(url, "EBAY"), transport=transport
    ) as client:
        response = await client.get("https://www.ebay.de/a")

    assert "ziel" in response.text
    assert transport.seen == ["https://www.ebay.de/a", "https://www.ebay.de/b"]


@pytest.mark.asyncio
async def test_more_than_five_hops_are_refused():
    routes = {f"https://www.ebay.de/{i}": _redirect(f"/{i + 1}") for i in range(10)}
    transport = RecordingTransport(routes)

    async with http_guard.guarded_async_client(
        lambda url: validate_marketplace_url(url, "EBAY"), transport=transport
    ) as client:
        with pytest.raises(httpx.TooManyRedirects):
            await client.get("https://www.ebay.de/0")

    assert len(transport.seen) == http_guard.MAX_REDIRECTS + 1


@pytest.mark.asyncio
async def test_existing_request_hooks_survive_and_run_after_validation():
    calls = []

    async def extra_hook(request):
        calls.append(str(request.url))

    transport = RecordingTransport({"https://www.ebay.de/x": _page()})
    async with http_guard.guarded_async_client(
        lambda url: validate_marketplace_url(url, "EBAY"),
        transport=transport,
        event_hooks={"request": [extra_hook]},
    ) as client:
        await client.get("https://www.ebay.de/x")

    assert calls == ["https://www.ebay.de/x"]


# ── Die drei Aufrufer aus dem Befund ───────────────────────────


@pytest.mark.asyncio
async def test_base_scraper_fetch_blocks_redirect_hop_without_fetching_it(monkeypatch):
    transport = RecordingTransport({
        "https://www.brickeconomy.com/search?query=75192": _redirect("http://127.0.0.1/admin"),
    })
    _inject(monkeypatch, scraper_base, transport)

    async def no_delay(self):
        return None

    monkeypatch.setattr(scraper_base.BaseScraper, "_delay", no_delay)

    async with BrickEconomyScraper() as scraper:
        with pytest.raises(UnsafeUrlError):
            await scraper._fetch("https://www.brickeconomy.com/search?query=75192")

    # Genau ein Abruf: kein interner Hop und keine Wiederholung durch tenacity.
    assert transport.seen == ["https://www.brickeconomy.com/search?query=75192"]


@pytest.mark.asyncio
async def test_base_scraper_blocks_redirect_to_another_host(monkeypatch):
    transport = RecordingTransport({
        "https://www.brickeconomy.com/set/1": _redirect("https://evil.example/steal"),
    })
    _inject(monkeypatch, scraper_base, transport)

    async def no_delay(self):
        return None

    monkeypatch.setattr(scraper_base.BaseScraper, "_delay", no_delay)

    async with BrickEconomyScraper() as scraper:
        with pytest.raises(UnsafeUrlError):
            await scraper._fetch("https://www.brickeconomy.com/set/1")

    assert transport.seen == ["https://www.brickeconomy.com/set/1"]


@pytest.mark.asyncio
async def test_brickmerge_find_redirect_to_metadata_ip_is_not_fetched(monkeypatch):
    transport = RecordingTransport({
        "https://www.brickmerge.de/?find=75192": _redirect("http://169.254.169.254/latest/meta-data/"),
    })
    _inject(monkeypatch, brickmerge, transport)

    async def no_delay(self):
        return None

    monkeypatch.setattr(BrickMergeScraper, "_delay", no_delay)

    with pytest.raises(UnsafeUrlError):
        await BrickMergeScraper()._fetch_detail_page("75192")

    assert transport.seen == ["https://www.brickmerge.de/?find=75192"]


@pytest.mark.asyncio
async def test_brickmerge_blocked_redirect_is_a_fetch_error_not_no_offers(monkeypatch):
    transport = RecordingTransport({
        "https://www.brickmerge.de/?find=75192": _redirect("http://redis:6379/"),
    })
    _inject(monkeypatch, brickmerge, transport)

    async def no_delay(self):
        return None

    monkeypatch.setattr(BrickMergeScraper, "_delay", no_delay)

    with pytest.raises(UnsafeUrlError):
        await BrickMergeScraper().get_price("75192")


@pytest.mark.asyncio
async def test_brickmerge_internal_redirect_still_reaches_the_detail_page(monkeypatch):
    transport = RecordingTransport({
        "https://www.brickmerge.de/?find=75192": _redirect("/75192-1_lego-star-wars"),
        "https://www.brickmerge.de/75192-1_lego-star-wars": _page(),
    })
    _inject(monkeypatch, brickmerge, transport)

    async def no_delay(self):
        return None

    monkeypatch.setattr(BrickMergeScraper, "_delay", no_delay)

    html = await BrickMergeScraper()._fetch_detail_page("75192")

    assert "LEGO" in html
    assert transport.seen[-1] == "https://www.brickmerge.de/75192-1_lego-star-wars"


@pytest.mark.asyncio
async def test_ecb_fetch_does_not_follow_a_redirect_into_the_docker_network(monkeypatch):
    transport = RecordingTransport({fx.ECB_DAILY_URL: _redirect("http://postgres:5432/")})
    _inject(monkeypatch, fx, transport)

    with pytest.raises(UnsafeUrlError):
        await fx._fetch_ecb()

    assert transport.seen == [fx.ECB_DAILY_URL]


# ── Sperrliste ─────────────────────────────────────────────────


@pytest.mark.parametrize(
    "host",
    [
        "100.64.1.1",  # CGNAT, von ip.is_private nicht erfasst
        "100.127.255.254",
        "::ffff:127.0.0.1",  # IPv4-mapped Loopback
        "::ffff:10.0.0.5",
        "::ffff:100.64.1.1",  # IPv4-mapped CGNAT
        "::ffff:169.254.169.254",
    ],
)
def test_newly_blocked_addresses(host):
    assert _is_blocked_host(host) is True


@pytest.mark.parametrize("host", ["100.63.255.255", "100.128.0.1", "8.8.8.8", "::ffff:8.8.8.8"])
def test_neighbouring_public_addresses_stay_allowed(host):
    assert _is_blocked_host(host) is False


@pytest.mark.parametrize("url", ["http://100.64.1.1/", "http://[::ffff:127.0.0.1]/"])
def test_blocked_addresses_are_refused_as_urls(url):
    with pytest.raises(UnsafeUrlError):
        validate_marketplace_url(url, None)
