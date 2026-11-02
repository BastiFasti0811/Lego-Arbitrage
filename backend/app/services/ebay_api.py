"""Duenner Client fuer die eBay Sell API (Nachtrag ADR 0002 vom 02.11.2026).

Nur die Aufrufe, die der Verkaufsweg braucht:

- OAuth Authorization-Code-Flow fuer Sebastians privates Konto: Consent-URL,
  Code gegen Refresh-Token tauschen, Access-Token aus dem Refresh-Token holen;
- Media API: Foto hochladen (unsere Fotos liegen nicht oeffentlich, eBay
  braucht EPS-Adressen);
- Inventory API: Artikel, Angebot, Veroeffentlichen, Beenden, Preis aendern,
  Lagerort;
- Account API: Geschaeftsrichtlinien lesen und einmalig anlegen;
- Fulfillment API: Bestellungen lesen (Verkaufsabgleich).

Kein Kaufen, kein Bieten: dafuer gibt es hier bewusst keinen Aufruf.

Zugangsdaten kommen nur aus der Umgebung (`backend/.env`, siehe docs/ebay.md).
Tokens werden nie geloggt; `app.logging_setup` schwaerzt sie zusaetzlich.
Endpunkte und Felder nach den offiziellen OpenAPI-Specs (Stand 02.11.2026).
"""

from __future__ import annotations

import base64
import time
from dataclasses import dataclass
from urllib.parse import parse_qs, quote, unquote, urlencode, urlparse

import httpx
import structlog

from app.config import settings

logger = structlog.get_logger()

SCOPES = (
    "https://api.ebay.com/oauth/api_scope",
    "https://api.ebay.com/oauth/api_scope/sell.inventory",
    "https://api.ebay.com/oauth/api_scope/sell.account",
    "https://api.ebay.com/oauth/api_scope/sell.fulfillment",
)

CONTENT_LANGUAGE = "de-DE"
TIMEOUT = httpx.Timeout(30.0, connect=10.0)
# Access-Token gilt 7200 s; eine Minute Puffer, damit er nicht mitten im Ablauf verfaellt.
TOKEN_SAFETY_SECONDS = 60


@dataclass(frozen=True)
class EbayEndpoints:
    api: str
    media: str
    auth: str
    item_url: str  # oeffentliche Artikelseite, {id} = listingId


ENDPOINTS = {
    "production": EbayEndpoints(
        api="https://api.ebay.com",
        media="https://apim.ebay.com",
        auth="https://auth.ebay.com",
        item_url="https://www.ebay.de/itm/{id}",
    ),
    "sandbox": EbayEndpoints(
        api="https://api.sandbox.ebay.com",
        media="https://apim.sandbox.ebay.com",
        auth="https://auth.sandbox.ebay.com",
        item_url="https://www.sandbox.ebay.com/itm/{id}",
    ),
}


class EbayError(Exception):
    """Fachlicher oder technischer Fehler der eBay-Anbindung, als Text fuer die App."""

    def __init__(self, detail: str, *, status: int | None = None, error_ids: tuple[int, ...] = ()):
        super().__init__(detail)
        self.detail = detail
        self.status = status
        self.error_ids = error_ids


class EbayNotConfiguredError(EbayError):
    pass


class EbayInputError(EbayError):
    """Der Entwurf taugt noch nicht fuer eBay (kein Foto, kein Preis, ...). Nichts ging raus."""


@dataclass(frozen=True)
class EbayConfig:
    environment: str
    client_id: str
    client_secret: str
    runame: str | None
    refresh_token: str | None
    marketplace_id: str

    @property
    def endpoints(self) -> EbayEndpoints:
        return ENDPOINTS[self.environment]

    @classmethod
    def from_settings(cls, *, require_token: bool = True) -> EbayConfig:
        missing = [
            name
            for name, value in (
                ("EBAY_CLIENT_ID", settings.ebay_client_id),
                ("EBAY_CLIENT_SECRET", settings.ebay_client_secret),
                ("EBAY_REFRESH_TOKEN", settings.ebay_refresh_token if require_token else "x"),
            )
            if not (value or "").strip()
        ]
        if missing:
            names = ", ".join(missing)
            raise EbayNotConfiguredError(f"eBay-Anbindung nicht eingerichtet: {names} fehlt (docs/ebay.md)")
        return cls(
            environment=settings.ebay_environment,
            client_id=settings.ebay_client_id.strip(),
            client_secret=settings.ebay_client_secret.strip(),
            runame=(settings.ebay_runame or "").strip() or None,
            refresh_token=(settings.ebay_refresh_token or "").strip() or None,
            marketplace_id=settings.ebay_marketplace_id,
        )


def is_configured() -> bool:
    try:
        EbayConfig.from_settings()
    except EbayNotConfiguredError:
        return False
    return True


# ── OAuth ───────────────────────────────────────────────────────────


def consent_url(config: EbayConfig, state: str) -> str:
    """Adresse, die Sebastian einmal im Browser oeffnet und bestaetigt."""
    if not config.runame:
        raise EbayNotConfiguredError("EBAY_RUNAME fehlt (docs/ebay.md, Schritt RuName)")
    query = urlencode(
        {
            "client_id": config.client_id,
            "redirect_uri": config.runame,
            "response_type": "code",
            "scope": " ".join(SCOPES),
            "state": state,
            "prompt": "login",
        },
        quote_via=quote,
    )
    return f"{config.endpoints.auth}/oauth2/authorize?{query}"


def extract_auth_code(value: str) -> str:
    """Code aus der Weiterleitungsadresse nach dem Consent ziehen.

    Nimmt die ganze Adresse (…?code=…&expires_in=299) oder den Code allein.
    eBay liefert den Code URL-kodiert; zum Tausch wird er dekodiert.
    """
    value = (value or "").strip()
    if not value:
        raise EbayError("Kein Code angegeben")
    if "code=" in value:
        query = urlparse(value).query or value.split("?", 1)[-1]
        codes = parse_qs(query).get("code")
        if not codes:
            raise EbayError("In der Adresse steht kein code=")
        return codes[0]
    return unquote(value)


def _basic_auth(config: EbayConfig) -> str:
    raw = f"{config.client_id}:{config.client_secret}".encode()
    return "Basic " + base64.b64encode(raw).decode()


@dataclass
class _CachedToken:
    value: str
    expires_at: float


_token_cache: dict[tuple[str, str], _CachedToken] = {}


def clear_token_cache() -> None:
    _token_cache.clear()


# ── Client ──────────────────────────────────────────────────────────


def _error_detail(response: httpx.Response) -> tuple[str, tuple[int, ...]]:
    try:
        payload = response.json()
    except ValueError:
        return f"eBay antwortet {response.status_code}", ()
    errors = payload.get("errors") or []
    if not errors and "error_description" in payload:  # OAuth-Endpunkt
        return f"eBay-Anmeldung: {payload.get('error_description')}", ()
    messages = []
    ids = []
    for err in errors:
        ids.append(int(err.get("errorId", 0) or 0))
        text = err.get("longMessage") or err.get("message") or "unbekannter Fehler"
        messages.append(f"{text} ({err.get('errorId')})" if err.get("errorId") else text)
    return ("eBay: " + "; ".join(messages)) if messages else f"eBay antwortet {response.status_code}", tuple(ids)


class EbayClient:
    """Ein Client je Vorgang; `async with EbayClient(...) as ebay:`.

    `transport` ist fuer Tests (httpx.MockTransport)."""

    def __init__(self, config: EbayConfig, *, transport: httpx.AsyncBaseTransport | None = None):
        self.config = config
        self._http = httpx.AsyncClient(timeout=TIMEOUT, transport=transport)

    async def __aenter__(self) -> EbayClient:
        return self

    async def __aexit__(self, *_exc) -> None:
        await self._http.aclose()

    # OAuth ---------------------------------------------------------

    async def _token_request(self, form: dict) -> dict:
        try:
            response = await self._http.post(
                f"{self.config.endpoints.api}/identity/v1/oauth2/token",
                data=form,
                headers={
                    "Authorization": _basic_auth(self.config),
                    "Content-Type": "application/x-www-form-urlencoded",
                },
            )
        except httpx.HTTPError as exc:
            raise EbayError(f"eBay-Anmeldung nicht erreichbar ({type(exc).__name__})") from exc
        if response.status_code != 200:
            detail, ids = _error_detail(response)
            raise EbayError(detail, status=response.status_code, error_ids=ids)
        return response.json()

    async def exchange_code(self, code: str) -> dict:
        """Einmaliger Tausch nach dem Consent. Liefert refresh_token und Laufzeit."""
        if not self.config.runame:
            raise EbayNotConfiguredError("EBAY_RUNAME fehlt")
        return await self._token_request(
            {"grant_type": "authorization_code", "code": code, "redirect_uri": self.config.runame}
        )

    async def access_token(self) -> str:
        if not self.config.refresh_token:
            raise EbayNotConfiguredError("EBAY_REFRESH_TOKEN fehlt (docs/ebay.md, Schritt Consent)")
        key = (self.config.environment, self.config.client_id)
        cached = _token_cache.get(key)
        if cached and cached.expires_at > time.monotonic():
            return cached.value
        payload = await self._token_request(
            {"grant_type": "refresh_token", "refresh_token": self.config.refresh_token, "scope": " ".join(SCOPES)}
        )
        token = payload["access_token"]
        lifetime = int(payload.get("expires_in", 7200))
        _token_cache[key] = _CachedToken(token, time.monotonic() + max(0, lifetime - TOKEN_SAFETY_SECONDS))
        return token

    # Allgemein -----------------------------------------------------

    async def _request(
        self,
        method: str,
        path: str,
        *,
        base: str | None = None,
        json: dict | None = None,
        params: dict | None = None,
        files: dict | None = None,
        content_language: bool = False,
        allow_404: bool = False,
    ) -> httpx.Response | None:
        headers = {"Authorization": f"Bearer {await self.access_token()}", "Accept": "application/json"}
        if content_language:
            headers["Content-Language"] = CONTENT_LANGUAGE
        try:
            url = f"{base or self.config.endpoints.api}{path}"
            response = await self._http.request(method, url, json=json, params=params, files=files, headers=headers)
        except httpx.HTTPError as exc:
            # Timeout/Verbindungsabbruch: ob eBay den Aufruf ausgefuehrt hat, ist
            # offen. Als EbayError, damit die Route den Zwischenstand speichert.
            logger.warning("ebay.network_error", method=method, path=path, error=type(exc).__name__)
            raise EbayError(
                f"eBay nicht erreichbar ({type(exc).__name__}) — Ergebnis unklar, bitte erneut versuchen"
            ) from exc
        if allow_404 and response.status_code == 404:
            return None
        if response.status_code >= 400:
            detail, ids = _error_detail(response)
            logger.warning("ebay.api_error", method=method, path=path, status=response.status_code, error_ids=ids)
            raise EbayError(detail, status=response.status_code, error_ids=ids)
        return response

    # Media API -----------------------------------------------------

    async def upload_image(self, data: bytes, filename: str) -> str:
        """Foto nach eBay Picture Services; liefert die EPS-Adresse fuer imageUrls."""
        response = await self._request(
            "POST",
            "/commerce/media/v1_beta/image/create_image_from_file",
            base=self.config.endpoints.media,
            files={"image": (filename, data, "image/jpeg")},
        )
        try:
            url = response.json().get("imageUrl")
        except ValueError:
            url = None
        if url:
            return url
        # Laut Spec steht die getImage-Adresse im Location-Header.
        location = response.headers.get("Location")
        if not location:
            raise EbayError("eBay hat das Foto angenommen, aber keine Adresse geliefert")
        image_id = location.rstrip("/").rsplit("/", 1)[-1]
        detail = await self._request(
            "GET", f"/commerce/media/v1_beta/image/{image_id}", base=self.config.endpoints.media
        )
        url = detail.json().get("imageUrl")
        if not url:
            raise EbayError("eBay liefert zum Foto keine imageUrl")
        return url

    # Inventory API -------------------------------------------------

    async def put_inventory_item(self, sku: str, body: dict) -> None:
        await self._request(
            "PUT", f"/sell/inventory/v1/inventory_item/{quote(sku, safe='')}", json=body, content_language=True
        )

    async def find_offer_id(self, sku: str) -> str | None:
        response = await self._request(
            "GET",
            "/sell/inventory/v1/offer",
            params={"sku": sku, "marketplace_id": self.config.marketplace_id},
            allow_404=True,
        )
        if response is None:
            return None
        offers = response.json().get("offers") or []
        return offers[0].get("offerId") if offers else None

    async def create_offer(self, body: dict) -> str:
        response = await self._request("POST", "/sell/inventory/v1/offer", json=body, content_language=True)
        return response.json()["offerId"]

    async def update_offer(self, offer_id: str, body: dict) -> None:
        await self._request("PUT", f"/sell/inventory/v1/offer/{offer_id}", json=body, content_language=True)

    async def get_offer(self, offer_id: str) -> dict | None:
        response = await self._request("GET", f"/sell/inventory/v1/offer/{offer_id}", allow_404=True)
        return None if response is None else response.json()

    async def publish_offer(self, offer_id: str) -> str:
        response = await self._request("POST", f"/sell/inventory/v1/offer/{offer_id}/publish")
        return str(response.json()["listingId"])

    async def withdraw_offer(self, offer_id: str) -> None:
        await self._request("POST", f"/sell/inventory/v1/offer/{offer_id}/withdraw")

    async def update_price(self, *, sku: str, offer_id: str, price: float) -> None:
        """Nur der Preis. Die Menge fuehrt eBay selbst (Teilverkaeufe); sie
        mitzuschicken koennte schon Verkauftes wieder freigeben."""
        body = {
            "requests": [
                {
                    "sku": sku,
                    "offers": [
                        {
                            "offerId": offer_id,
                            "price": {"value": f"{price:.2f}", "currency": "EUR"},
                        }
                    ],
                }
            ]
        }
        response = await self._request("POST", "/sell/inventory/v1/bulk_update_price_quantity", json=body)
        for entry in response.json().get("responses") or []:
            if int(entry.get("statusCode", 200)) >= 400:
                errors = entry.get("errors") or []
                text = "; ".join(e.get("message", "") for e in errors) or "Preis nicht übernommen"
                raise EbayError(f"eBay: {text}")

    async def get_location(self, key: str) -> dict | None:
        response = await self._request(
            "GET", f"/sell/inventory/v1/location/{quote(key, safe='')}", allow_404=True
        )
        return None if response is None else response.json()

    async def create_location(self, key: str, body: dict) -> None:
        await self._request("POST", f"/sell/inventory/v1/location/{quote(key, safe='')}", json=body)

    # Account API ---------------------------------------------------

    async def opted_in_programs(self) -> list[str]:
        response = await self._request("GET", "/sell/account/v1/program/get_opted_in_programs")
        return [p.get("programType") for p in response.json().get("programs") or []]

    async def opt_in_business_policies(self) -> None:
        await self._request(
            "POST", "/sell/account/v1/program/opt_in", json={"programType": "SELLING_POLICY_MANAGEMENT"}
        )

    async def list_policies(self, kind: str) -> list[dict]:
        """kind: fulfillment, payment oder return."""
        response = await self._request(
            "GET", f"/sell/account/v1/{kind}_policy", params={"marketplace_id": self.config.marketplace_id}
        )
        return response.json().get(f"{kind}Policies") or []

    async def create_policy(self, kind: str, body: dict) -> str:
        response = await self._request("POST", f"/sell/account/v1/{kind}_policy", json=body)
        return str(response.json()[f"{kind}PolicyId"])

    # Fulfillment API -----------------------------------------------

    async def orders_modified_since(self, since_iso: str, *, page_size: int = 50, max_pages: int = 20) -> list[dict]:
        orders: list[dict] = []
        for page in range(max_pages):
            response = await self._request(
                "GET",
                "/sell/fulfillment/v1/order",
                params={"filter": f"lastmodifieddate:[{since_iso}..]", "limit": page_size, "offset": page * page_size},
            )
            payload = response.json()
            batch = payload.get("orders") or []
            orders.extend(batch)
            if not payload.get("next") or not batch:
                break
        return orders

    def item_url(self, listing_id: str) -> str:
        return self.config.endpoints.item_url.format(id=listing_id)
