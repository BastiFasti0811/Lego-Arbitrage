"""eBay Sell API: Regeln, OAuth, Einstellen, Preis, Beenden, Verkaufsabgleich.

Gegen einen simulierten eBay-Server (httpx.MockTransport) und echte Zeilen in
In-Memory-SQLite. Kein Netz, keine Zugangsdaten.
"""

import json
import os
import stat
from datetime import UTC, date, datetime
from types import SimpleNamespace
from urllib.parse import parse_qs, urlparse

import httpx
import pytest
from fastapi import HTTPException
from PIL import Image
from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session

from app.api.routes import listings as listing_routes
from app.config import settings
from app.logging_setup import redact_text
from app.models import Base
from app.models.inventory import InventoryItem, InventoryItemType, InventoryStatus
from app.models.inventory_photo import InventoryPhoto
from app.models.listing import Listing, ListingStatus
from app.models.listing_posting_job import ListingPostingJob, PostingJobStatus
from app.services import ebay_api, ebay_listing
from app.services.ebay_api import EbayClient, EbayConfig, EbayError, consent_url, extract_auth_code
from app.services.ebay_listing import (
    EBAY_TITLE_MAX,
    build_inventory_item,
    description_html,
    ebay_condition,
    ebay_sku,
    ebay_title,
    format_sync_message,
)
from app.tools.ebay_setup import ensure_setup, set_env_value

NOW = datetime(2026, 11, 2, 12, 0, tzinfo=UTC)
CONFIG = EbayConfig(
    environment="sandbox",
    client_id="client-id",
    client_secret="client-secret",
    runame="Sebastian-LegoArb-SBX-abc",
    refresh_token="v^1.1#i^1#r^1#refresh-geheim",
    marketplace_id="EBAY_DE",
)


# ── simulierter eBay-Server ──────────────────────────────────────────


class FakeEbay:
    """Merkt sich alle Aufrufe und antwortet wie die Sell API laut Spec."""

    def __init__(self):
        self.calls: list[httpx.Request] = []
        self.offers: dict[str, dict] = {}  # offerId -> body
        self.inventory: dict[str, dict] = {}
        self.policies = {
            "fulfillment": [{"fulfillmentPolicyId": "F1", "name": "Lego-Arbitrage"}],
            "payment": [{"paymentPolicyId": "P1", "name": "Lego-Arbitrage"}],
            "return": [{"returnPolicyId": "R1", "name": "Lego-Arbitrage"}],
        }
        self.locations: dict[str, dict] = {"lego-arbitrage": {}}
        self.programs = ["SELLING_POLICY_MANAGEMENT"]
        self.orders: list[dict] = []
        self.fail: dict[str, tuple[int, dict]] = {}  # Pfadteil -> Fehlerantwort
        self.withdrawn: list[str] = []
        self.token_requests = 0

    def paths(self, method: str | None = None) -> list[str]:
        return [r.url.path for r in self.calls if method is None or r.method == method]

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.calls.append(request)
        path = request.url.path
        for part, (status, body) in self.fail.items():
            if part in path:
                return httpx.Response(status, json=body)
        if path == "/identity/v1/oauth2/token":
            self.token_requests += 1
            form = parse_qs(request.content.decode())
            if form["grant_type"] == ["authorization_code"]:
                return httpx.Response(
                    200,
                    json={
                        "access_token": "v^1.1#access",
                        "expires_in": 7200,
                        "refresh_token": "v^1.1#neuer-refresh",
                        "refresh_token_expires_in": 47304000,
                    },
                )
            return httpx.Response(200, json={"access_token": "v^1.1#access", "expires_in": 7200})
        if path.endswith("/image/create_image_from_file"):
            n = len([p for p in self.paths() if p.endswith("create_image_from_file")])
            return httpx.Response(201, json={"imageUrl": f"https://i.ebayimg.com/images/g/{n}/s-l1600.jpg"})
        if path.startswith("/sell/inventory/v1/inventory_item/") and request.method == "PUT":
            self.inventory[path.rsplit("/", 1)[-1]] = json.loads(request.content)
            return httpx.Response(204)
        if path == "/sell/inventory/v1/offer" and request.method == "GET":
            sku = request.url.params["sku"]
            found = [{"offerId": k, **v} for k, v in self.offers.items() if v["sku"] == sku]
            return httpx.Response(200, json={"offers": found}) if found else httpx.Response(404, json={"errors": []})
        if path == "/sell/inventory/v1/offer" and request.method == "POST":
            offer_id = f"O{len(self.offers) + 1}"
            self.offers[offer_id] = json.loads(request.content)
            return httpx.Response(201, json={"offerId": offer_id})
        if path.startswith("/sell/inventory/v1/offer/") and path.endswith("/publish"):
            return httpx.Response(200, json={"listingId": "110551234567"})
        if path.startswith("/sell/inventory/v1/offer/") and path.endswith("/withdraw"):
            self.withdrawn.append(path.split("/")[-2])
            return httpx.Response(200, json={"listingId": "110551234567"})
        if path.startswith("/sell/inventory/v1/offer/") and request.method == "PUT":
            self.offers[path.rsplit("/", 1)[-1]] = json.loads(request.content)
            return httpx.Response(204)
        if path == "/sell/inventory/v1/bulk_update_price_quantity":
            return httpx.Response(200, json={"responses": [{"statusCode": 200}]})
        if path.startswith("/sell/inventory/v1/location/"):
            key = path.rsplit("/", 1)[-1]
            if request.method == "GET":
                return httpx.Response(200, json=self.locations[key]) if key in self.locations else httpx.Response(404)
            self.locations[key] = json.loads(request.content)
            return httpx.Response(204)
        if path == "/sell/account/v1/program/get_opted_in_programs":
            return httpx.Response(200, json={"programs": [{"programType": p} for p in self.programs]})
        if path == "/sell/account/v1/program/opt_in":
            self.programs.append("SELLING_POLICY_MANAGEMENT")
            return httpx.Response(200, json={})
        if path.startswith("/sell/account/v1/") and path.endswith("_policy"):
            kind = path.split("/")[-1].removesuffix("_policy")
            if request.method == "GET":
                return httpx.Response(200, json={f"{kind}Policies": self.policies[kind]})
            new_id = f"{kind[0].upper()}NEW"
            self.policies[kind].append({f"{kind}PolicyId": new_id, **json.loads(request.content)})
            return httpx.Response(201, json={f"{kind}PolicyId": new_id})
        if path == "/sell/fulfillment/v1/order":
            return httpx.Response(200, json={"orders": self.orders, "total": len(self.orders)})
        return httpx.Response(500, json={"errors": [{"errorId": 1, "message": f"unerwartet: {path}"}]})


@pytest.fixture(autouse=True)
def _fresh_token_cache():
    ebay_api.clear_token_cache()
    yield
    ebay_api.clear_token_cache()


@pytest.fixture
def fake():
    return FakeEbay()


@pytest.fixture
def client(fake):
    return EbayClient(CONFIG, transport=httpx.MockTransport(fake.handler))


# ── echte Zeilen ─────────────────────────────────────────────────────


class _AsyncSessionAdapter:
    def __init__(self, sync_session):
        self._session = sync_session

    async def execute(self, statement):
        return self._session.execute(statement)

    def add(self, obj):
        self._session.add(obj)

    async def commit(self):
        self._session.commit()

    async def rollback(self):
        self._session.rollback()

    async def refresh(self, obj):
        self._session.refresh(obj)


@pytest.fixture
def db():
    engine = create_engine("sqlite://")

    @event.listens_for(engine, "connect")
    def _foreign_keys_on(dbapi_connection, _record):
        dbapi_connection.execute("PRAGMA foreign_keys=ON")

    Base.metadata.create_all(engine)
    with Session(engine, expire_on_commit=False) as sync_session:
        yield _AsyncSessionAdapter(sync_session)
    engine.dispose()


@pytest.fixture
def photo_root(tmp_path):
    return tmp_path / "inventory_photos"


def _draft(db, photo_root, *, photos=2, platform="EBAY", condition="NEW_SEALED", quantity=1, item_type="LEGO"):
    item = InventoryItem(
        item_type=item_type,
        set_number="75192" if item_type == "LEGO" else None,
        set_name="Millennium Falcon",
        theme="Star Wars",
        buy_price=500.0,
        buy_shipping=0.0,
        buy_date=date(2026, 1, 10),
        condition=condition,
        quantity=quantity,
        status=InventoryStatus.HOLDING.value,
    )
    db.add(item)
    db._session.flush()
    folder = photo_root / str(item.id)
    folder.mkdir(parents=True, exist_ok=True)
    for index in range(photos):
        Image.new("RGB", (40, 30), "red").save(folder / f"p{index}.jpg")
        db.add(InventoryPhoto(item_id=item.id, filename=f"p{index}.jpg", sort_order=index))
    listing = Listing(
        item_id=item.id,
        platform=platform,
        status=ListingStatus.DRAFT.value,
        price_type="FIXED",
        title="LEGO 75192 Millennium Falcon NEU OVP",
        body="LEGO Set 75192 <UCS>\nZustand: Neu & OVP\n\nVersand möglich.",
        current_price=649.9,
        check_interval_days=14,
        price_drop_percent=10.0,
    )
    db.add(listing)
    db._session.flush()
    db._session.refresh(item)
    return listing, item


# ── reine Regeln ─────────────────────────────────────────────────────


def test_sku_is_unique_per_listing_and_separates_sandbox_from_production():
    assert ebay_sku(7, 12, "sandbox") == "LA-S-7-12"
    assert ebay_sku(7, 12, "production") == "LA-P-7-12"


def test_title_fits_ebay_limit_and_cuts_at_word_boundary():
    long_title = "LEGO 75192 Star Wars Millennium Falcon Ultimate Collector Series NEU OVP versiegelt ungeöffnet"
    title = ebay_title(long_title)
    assert len(title) <= EBAY_TITLE_MAX
    assert long_title.startswith(title)
    assert long_title[len(title)] == " "


def test_condition_mapping_uses_inventory_api_enum():
    assert ebay_condition("NEW_SEALED") == ("NEW", None)
    assert ebay_condition("NEW_OPEN_BOX")[0] == "NEW_OTHER"
    assert ebay_condition("USED_COMPLETE")[0] == "USED_EXCELLENT"
    enum, text = ebay_condition("USED_INCOMPLETE")
    assert enum == "USED_EXCELLENT" and "nicht vollständig" in text
    with pytest.raises(EbayError):
        ebay_condition("UNKNOWN")


def test_description_is_escaped_html_with_paragraphs():
    assert description_html("A <b>\nB\n\nC & D") == "<p>A &lt;b&gt;<br>B</p><p>C &amp; D</p>"


def test_inventory_item_carries_brand_mpn_quantity_photos_and_condition_note():
    item = SimpleNamespace(set_number="10305", theme=None, condition="USED_INCOMPLETE", quantity=2)
    listing = SimpleNamespace(title="LEGO 10305 Burg", body="Text")
    body = build_inventory_item(item, listing, ["https://i.ebayimg.com/1.jpg"])
    assert body["product"]["brand"] == "LEGO"
    assert body["product"]["mpn"] == "10305"
    assert body["product"]["aspects"]["Marke"] == ["LEGO"]
    assert body["product"]["imageUrls"] == ["https://i.ebayimg.com/1.jpg"]
    assert body["availability"]["shipToLocationAvailability"]["quantity"] == 2
    assert body["condition"] == "USED_EXCELLENT"
    assert "conditionDescription" in body


# ── OAuth ────────────────────────────────────────────────────────────


def test_consent_url_asks_for_sell_scopes_with_runame():
    url = urlparse(consent_url(CONFIG, state="xyz"))
    query = parse_qs(url.query)
    assert url.netloc == "auth.sandbox.ebay.com"
    assert query["redirect_uri"] == ["Sebastian-LegoArb-SBX-abc"]
    assert query["response_type"] == ["code"]
    scopes = query["scope"][0].split(" ")
    assert "https://api.ebay.com/oauth/api_scope/sell.inventory" in scopes
    assert "https://api.ebay.com/oauth/api_scope/sell.fulfillment" in scopes
    assert query["state"] == ["xyz"]


def test_auth_code_is_taken_from_redirect_url_and_url_decoded():
    redirect = "https://auth2.ebay.com/oauth2/ThirdPartyAuthSucessFailure?isAuthSuccessful=true&code=v%5E1.1%23i%5E1&expires_in=299"
    assert extract_auth_code(redirect) == "v^1.1#i^1"
    assert extract_auth_code("v%5E1.1%23i%5E1") == "v^1.1#i^1"
    with pytest.raises(EbayError):
        extract_auth_code("")


async def test_access_token_is_fetched_once_with_basic_auth_and_cached(fake, client):
    async with client:
        assert await client.access_token() == "v^1.1#access"
        assert await client.access_token() == "v^1.1#access"
    assert fake.token_requests == 1
    request = fake.calls[0]
    assert request.headers["Authorization"].startswith("Basic ")
    form = parse_qs(request.content.decode())
    assert form["grant_type"] == ["refresh_token"]
    assert form["refresh_token"] == [CONFIG.refresh_token]


async def test_exchange_code_returns_refresh_token(fake, client):
    async with client:
        payload = await client.exchange_code("v^1.1#i^1")
    form = parse_qs(fake.calls[0].content.decode())
    assert form["grant_type"] == ["authorization_code"]
    assert form["redirect_uri"] == [CONFIG.runame]
    assert payload["refresh_token"] == "v^1.1#neuer-refresh"


def test_missing_credentials_disable_the_integration(monkeypatch):
    monkeypatch.setattr(settings, "ebay_client_id", None)
    assert ebay_api.is_configured() is False
    with pytest.raises(ebay_api.EbayNotConfiguredError) as exc:
        EbayConfig.from_settings()
    assert "EBAY_CLIENT_ID" in exc.value.detail


async def test_ebay_error_messages_are_passed_on(fake, client):
    fake.fail["/offer/O1/publish"] = (
        400,
        {"errors": [{"errorId": 25002, "message": "Kurz", "longMessage": "Pflichtangabe Marke fehlt"}]},
    )
    async with client:
        with pytest.raises(EbayError) as exc:
            await client.publish_offer("O1")
    assert exc.value.status == 400
    assert exc.value.error_ids == (25002,)
    assert "Pflichtangabe Marke fehlt" in exc.value.detail


# ── Einstellen ───────────────────────────────────────────────────────


async def test_publish_runs_inventory_offer_publish_and_activates_listing(db, fake, client, photo_root):
    listing, item = _draft(db, photo_root)
    async with client:
        url = await ebay_listing.publish_listing(listing, item, client, photo_root=photo_root, today=date(2026, 11, 2))

    sku = f"LA-S-{item.id}-{listing.id}"
    assert url == "https://www.sandbox.ebay.com/itm/110551234567"
    assert listing.status == ListingStatus.ACTIVE.value
    assert listing.url == url
    assert listing.ebay_sku == sku
    assert listing.ebay_offer_id == "O1"
    assert listing.ebay_listing_id == "110551234567"
    assert listing.min_price == 454.93  # 70 % Schmerzgrenze wie beim Einstellen von Hand
    assert listing.listed_at == date(2026, 11, 2)

    # Reihenfolge: Fotos, Artikel, Angebot, Veroeffentlichen
    order = [p for p in fake.paths() if "oauth2" not in p and "_policy" not in p]
    assert order[0].endswith("create_image_from_file") and order[1].endswith("create_image_from_file")
    assert order[2] == f"/sell/inventory/v1/inventory_item/{sku}"
    assert order[-1] == "/sell/inventory/v1/offer/O1/publish"

    put = next(r for r in fake.calls if r.method == "PUT")
    assert put.headers["Content-Language"] == "de-DE"
    assert fake.inventory[sku]["product"]["imageUrls"] == [
        "https://i.ebayimg.com/images/g/1/s-l1600.jpg",
        "https://i.ebayimg.com/images/g/2/s-l1600.jpg",
    ]
    offer = fake.offers["O1"]
    assert offer["format"] == "FIXED_PRICE"
    assert offer["marketplaceId"] == "EBAY_DE"
    assert offer["pricingSummary"]["price"] == {"value": "649.90", "currency": "EUR"}
    assert offer["listingPolicies"] == {"fulfillmentPolicyId": "F1", "paymentPolicyId": "P1", "returnPolicyId": "R1"}
    assert offer["merchantLocationKey"] == "lego-arbitrage"
    assert offer["categoryId"] == "19006"
    assert "&lt;UCS&gt;" in offer["listingDescription"]


async def test_second_attempt_after_failed_publish_reuses_the_offer(db, fake, client, photo_root):
    listing, item = _draft(db, photo_root)
    fake.fail["/publish"] = (500, {"errors": [{"errorId": 25001, "message": "Systemfehler"}]})
    async with client:
        with pytest.raises(EbayError):
            await ebay_listing.publish_listing(listing, item, client, photo_root=photo_root)
    assert listing.status == ListingStatus.DRAFT.value
    assert listing.ebay_offer_id == "O1"

    del fake.fail["/publish"]
    async with EbayClient(CONFIG, transport=httpx.MockTransport(fake.handler)) as retry:
        await ebay_listing.publish_listing(listing, item, retry, photo_root=photo_root)
    assert listing.status == ListingStatus.ACTIVE.value
    assert len(fake.offers) == 1  # kein zweites Angebot
    assert "/sell/inventory/v1/offer/O1" in fake.paths("PUT")


async def test_publish_finds_an_existing_offer_by_sku(db, fake, client, photo_root):
    listing, item = _draft(db, photo_root)
    sku = f"LA-S-{item.id}-{listing.id}"
    fake.offers["O9"] = {"sku": sku}
    async with client:
        await ebay_listing.publish_listing(listing, item, client, photo_root=photo_root)
    assert listing.ebay_offer_id == "O9"
    assert "/sell/inventory/v1/offer" not in fake.paths("POST")


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"platform": "KLEINANZEIGEN"}, "Nur eBay-Entwürfe"),
        ({"photos": 0}, "mindestens ein Foto"),
        ({"item_type": InventoryItemType.GENERIC.value}, "nur Lego-Sets"),
        ({"condition": "UNKNOWN"}, "Zustand unbekannt"),
    ],
)
async def test_publish_refuses_what_ebay_would_reject(db, fake, client, photo_root, kwargs, message):
    listing, item = _draft(db, photo_root, **kwargs)
    async with client:
        with pytest.raises(EbayError) as exc:
            await ebay_listing.publish_listing(listing, item, client, photo_root=photo_root)
    assert message in exc.value.detail
    assert fake.calls == []  # nichts zu eBay geschickt


async def test_policies_come_from_env_ids_first(monkeypatch, fake, client):
    monkeypatch.setattr(settings, "ebay_fulfillment_policy_id", "ENV-F")
    async with client:
        policies = await ebay_listing.resolve_policies(client)
    assert policies.fulfillment == "ENV-F"
    assert policies.payment == "P1"
    assert "/sell/account/v1/fulfillment_policy" not in fake.paths()


async def test_ambiguous_policies_stop_with_a_hint(fake, client):
    fake.policies["payment"] = [
        {"paymentPolicyId": "A", "name": "PayPal"},
        {"paymentPolicyId": "B", "name": "Sofort"},
    ]
    async with client:
        with pytest.raises(EbayError) as exc:
            await ebay_listing.resolve_policies(client)
    assert "ebay_setup policies" in exc.value.detail


async def test_single_policy_with_other_name_is_taken(fake, client):
    fake.policies["return"] = [{"returnPolicyId": "R7", "name": "Keine Rücknahme"}]
    async with client:
        policies = await ebay_listing.resolve_policies(client)
    assert policies.returns == "R7"


# ── Preis und Beenden ────────────────────────────────────────────────


async def _published(db, fake, photo_root, **kwargs):
    listing, item = _draft(db, photo_root, **kwargs)
    async with EbayClient(CONFIG, transport=httpx.MockTransport(fake.handler)) as setup_client:
        await ebay_listing.publish_listing(listing, item, setup_client, photo_root=photo_root)
    fake.calls.clear()
    return listing, item


async def test_price_from_the_staircase_is_pushed_to_ebay(db, fake, client, photo_root):
    listing, item = await _published(db, fake, photo_root)
    async with client:
        await ebay_listing.push_price(listing, item, 585.0, client)
    request = next(r for r in fake.calls if r.url.path.endswith("bulk_update_price_quantity"))
    body = json.loads(request.content)
    offer = body["requests"][0]["offers"][0]
    assert body["requests"][0]["sku"] == listing.ebay_sku
    assert offer == {"offerId": "O1", "availableQuantity": 1, "price": {"value": "585.00", "currency": "EUR"}}


async def test_price_push_ignores_listings_posted_by_hand(db, fake, client, photo_root):
    listing, item = _draft(db, photo_root)
    listing.status = ListingStatus.ACTIVE.value
    async with client:
        await ebay_listing.push_price(listing, item, 500.0, client)
    assert fake.calls == []


async def test_end_withdraws_the_offer_and_keeps_history(db, fake, client, photo_root):
    listing, _item = await _published(db, fake, photo_root)
    async with client:
        await ebay_listing.end_listing(listing, client)
    assert fake.withdrawn == ["O1"]
    assert listing.status == ListingStatus.ENDED.value


# ── Verkaufsabgleich ─────────────────────────────────────────────────


def _order(sku, *, qty=1, price="620.00", cancelled=False):
    return {
        "orderId": "12-34567-89012",
        "creationDate": "2026-11-01T18:30:00.000Z",
        "cancelStatus": {"cancelState": "CANCELED" if cancelled else "NONE_REQUESTED"},
        "lineItems": [{"sku": sku, "quantity": qty, "lineItemCost": {"value": price, "currency": "EUR"}}],
    }


async def test_ebay_sale_marks_item_sold_and_reports_open_kleinanzeigen_ad(db, fake, client, photo_root):
    listing, item = await _published(db, fake, photo_root)
    ka = Listing(
        item_id=item.id,
        platform="KLEINANZEIGEN",
        status=ListingStatus.ACTIVE.value,
        price_type="VB",
        current_price=650.0,
        url="https://www.kleinanzeigen.de/s-anzeige/x/1",
    )
    db.add(ka)
    db._session.flush()
    db._session.refresh(item)
    fake.orders = [_order(listing.ebay_sku)]

    async with client:
        result = await ebay_listing.sync_sales(db, client, NOW)

    assert item.status == InventoryStatus.SOLD.value
    assert item.sell_platform == "eBay"
    assert item.sell_price == 620.0
    assert item.sell_date == date(2026, 11, 1)
    assert item.realized_profit is not None
    assert listing.status == ListingStatus.SOLD.value
    assert ka.status == ListingStatus.ACTIVE.value  # Kleinanzeigen wird nie automatisch geloescht
    assert result.sold[0].open_elsewhere == [("KLEINANZEIGEN", "https://www.kleinanzeigen.de/s-anzeige/x/1")]
    message = format_sync_message(result)
    assert "75192 Millennium Falcon für 620,00 €" in message
    assert "Kleinanzeigen-Anzeige von Hand löschen" in message

    # zweiter Lauf: nichts mehr zu tun
    async with EbayClient(CONFIG, transport=httpx.MockTransport(fake.handler)) as again:
        assert (await ebay_listing.sync_sales(db, again, NOW)).sold == []


async def test_ebay_sale_cancels_waiting_kleinanzeigen_posting_job(db, fake, client, photo_root):
    listing, item = await _published(db, fake, photo_root)
    ka = Listing(item_id=item.id, platform="KLEINANZEIGEN", status=ListingStatus.DRAFT.value, price_type="VB")
    db.add(ka)
    db._session.flush()
    job = ListingPostingJob(
        listing_id=ka.id, action="CREATE", status=PostingJobStatus.APPROVED.value, requested_at=NOW
    )
    db.add(job)
    db._session.flush()
    db._session.refresh(item)
    fake.orders = [_order(listing.ebay_sku)]

    async with client:
        await ebay_listing.sync_sales(db, client, NOW)

    assert job.status == PostingJobStatus.CANCELLED.value
    assert job.error == "Auf eBay verkauft"


async def test_cancelled_order_and_foreign_sku_change_nothing(db, fake, client, photo_root):
    listing, item = await _published(db, fake, photo_root)
    fake.orders = [_order(listing.ebay_sku, cancelled=True), _order("FREMD-1")]
    async with client:
        result = await ebay_listing.sync_sales(db, client, NOW)
    assert item.status == InventoryStatus.HOLDING.value
    assert listing.status == ListingStatus.ACTIVE.value
    assert result.orders_seen == 2
    assert format_sync_message(result) is None


async def test_partial_sale_is_reported_not_booked(db, fake, client, photo_root):
    listing, item = await _published(db, fake, photo_root, quantity=3)
    fake.orders = [_order(listing.ebay_sku, qty=1)]
    async with client:
        result = await ebay_listing.sync_sales(db, client, NOW)
    assert item.status == InventoryStatus.HOLDING.value
    assert result.partial == ["75192 Millennium Falcon: 1 von 3 verkauft"]

    # derselbe Stand beim naechsten Lauf: keine zweite Meldung
    async with EbayClient(CONFIG, transport=httpx.MockTransport(fake.handler)) as again:
        assert (await ebay_listing.sync_sales(db, again, NOW)).partial == []
    # neue Bestellung zum selben Posten: wieder gemeldet
    second = _order(listing.ebay_sku, qty=1)
    second["orderId"] = "99-00000-00001"
    fake.orders.append(second)
    async with EbayClient(CONFIG, transport=httpx.MockTransport(fake.handler)) as third:
        assert (await ebay_listing.sync_sales(db, third, NOW)).partial == ["75192 Millennium Falcon: 2 von 3 verkauft"]


async def test_item_sold_elsewhere_ends_the_running_ebay_offer(db, fake, client, photo_root):
    listing, item = await _published(db, fake, photo_root)
    item.status = InventoryStatus.SOLD.value  # auf Kleinanzeigen verkauft, in der App markiert
    async with client:
        result = await ebay_listing.sync_sales(db, client, NOW)
    assert fake.withdrawn == ["O1"]
    assert listing.status == ListingStatus.ENDED.value
    assert result.withdrawn == 1


async def test_sync_without_api_listings_does_not_call_ebay(db, fake, client):
    async with client:
        result = await ebay_listing.sync_sales(db, client, NOW)
    assert result.orders_seen == 0
    assert fake.calls == []


# ── Routen ───────────────────────────────────────────────────────────


async def test_publish_route_activates_listing(monkeypatch, db, fake, photo_root):
    listing, item = _draft(db, photo_root)
    monkeypatch.setattr(listing_routes, "PHOTO_STORAGE_ROOT", photo_root)
    monkeypatch.setattr(
        listing_routes, "ebay_client", lambda: EbayClient(CONFIG, transport=httpx.MockTransport(fake.handler))
    )
    response = await listing_routes.publish_on_ebay(item.id, listing.id, session=db)
    assert response.status == ListingStatus.ACTIVE.value
    assert response.ebay_listing_id == "110551234567"


async def test_publish_route_keeps_offer_id_when_ebay_fails(monkeypatch, db, fake, photo_root):
    listing, item = _draft(db, photo_root)
    fake.fail["/publish"] = (400, {"errors": [{"errorId": 25002, "message": "Marke fehlt"}]})
    monkeypatch.setattr(listing_routes, "PHOTO_STORAGE_ROOT", photo_root)
    monkeypatch.setattr(
        listing_routes, "ebay_client", lambda: EbayClient(CONFIG, transport=httpx.MockTransport(fake.handler))
    )
    with pytest.raises(HTTPException) as exc:
        await listing_routes.publish_on_ebay(item.id, listing.id, session=db)
    assert exc.value.status_code == 502
    assert "Marke fehlt" in exc.value.detail
    db._session.expire_all()
    assert db._session.get(Listing, listing.id).ebay_offer_id == "O1"


async def test_publish_route_without_credentials_is_409(monkeypatch, db, photo_root):
    listing, item = _draft(db, photo_root)
    monkeypatch.setattr(settings, "ebay_refresh_token", None)
    with pytest.raises(HTTPException) as exc:
        await listing_routes.publish_on_ebay(item.id, listing.id, session=db)
    assert exc.value.status_code == 409


async def test_end_route_withdraws_api_listing(monkeypatch, db, fake, photo_root):
    listing, item = await _published(db, fake, photo_root)
    monkeypatch.setattr(
        listing_routes, "ebay_client", lambda: EbayClient(CONFIG, transport=httpx.MockTransport(fake.handler))
    )
    response = await listing_routes.end_listing(item.id, listing.id, session=db)
    assert response.status == ListingStatus.ENDED.value
    assert fake.withdrawn == ["O1"]


async def test_accepted_suggestion_is_not_stored_when_ebay_rejects_the_price(monkeypatch, db, fake, photo_root):
    listing, item = await _published(db, fake, photo_root)
    listing.suggested_price = 585.0
    fake.fail["bulk_update_price_quantity"] = (400, {"errors": [{"errorId": 25005, "message": "Preis ungültig"}]})
    monkeypatch.setattr(
        listing_routes, "ebay_client", lambda: EbayClient(CONFIG, transport=httpx.MockTransport(fake.handler))
    )
    with pytest.raises(HTTPException) as exc:
        await listing_routes.accept_suggestion(item.id, listing.id, session=db)
    assert exc.value.status_code == 502
    assert listing.current_price == 649.9


# ── Einrichtung und Secrets ──────────────────────────────────────────


async def test_setup_creates_missing_policies_and_location_once(monkeypatch, fake, client):
    monkeypatch.setattr(settings, "ebay_location_postal_code", "10115")
    monkeypatch.setattr(settings, "ebay_location_city", "Berlin")
    fake.programs = []
    fake.policies = {"fulfillment": [], "payment": [], "return": []}
    fake.locations = {}
    async with client:
        await ensure_setup(client, out=lambda _line: None)
    assert "SELLING_POLICY_MANAGEMENT" in fake.programs
    assert [p["name"] for p in fake.policies["fulfillment"]] == ["Lego-Arbitrage"]
    shipping = fake.policies["fulfillment"][0]["shippingOptions"][0]["shippingServices"][0]
    assert shipping["shippingServiceCode"] == "DE_DHLPaket"
    assert fake.policies["return"][0]["returnsAccepted"] is False
    address = fake.locations["lego-arbitrage"]["location"]["address"]
    assert address == {"postalCode": "10115", "city": "Berlin", "country": "DE"}  # keine Strasse

    fake.calls.clear()
    async with EbayClient(CONFIG, transport=httpx.MockTransport(fake.handler)) as again:
        await ensure_setup(again, out=lambda _line: None)
    assert fake.paths("POST") == []  # idempotent


def test_refresh_token_is_written_into_env_file_with_0600(tmp_path):
    env = tmp_path / ".env"
    env.write_text("DEBUG=false\n# EBAY_REFRESH_TOKEN=\nLOG_LEVEL=INFO\n")
    set_env_value(env, "EBAY_REFRESH_TOKEN", "v^1.1#neu")
    assert env.read_text() == "DEBUG=false\nEBAY_REFRESH_TOKEN=v^1.1#neu\nLOG_LEVEL=INFO\n"
    assert stat.S_IMODE(os.stat(env).st_mode) == 0o600
    set_env_value(env, "EBAY_REFRESH_TOKEN", "v^1.1#zweiter")
    assert env.read_text().count("EBAY_REFRESH_TOKEN=") == 1
    set_env_value(env, "EBAY_ENVIRONMENT", "production")
    assert env.read_text().endswith("EBAY_ENVIRONMENT=production\n")


def test_ebay_tokens_are_redacted_in_logs():
    text = "refresh failed for v^1.1#i^1#r^1#p^3#abc123 in request"
    assert redact_text(text) == "refresh failed for <ebay-token> in request"
