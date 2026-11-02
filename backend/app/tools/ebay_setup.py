"""Einrichtung und Handbetrieb der eBay-Anbindung (docs/ebay.md).

    python -m app.tools.ebay_setup consent-url          # Adresse fuer den einmaligen Consent
    python -m app.tools.ebay_setup exchange --env-file backend/.env
                                                        # Weiterleitungsadresse einfuegen (stdin),
                                                        # Refresh-Token landet in der .env
    python -m app.tools.ebay_setup status               # Zugang, Richtlinien, Lagerort pruefen
    python -m app.tools.ebay_setup setup                # Richtlinien + Lagerort einmalig anlegen
    python -m app.tools.ebay_setup publish <listing_id> # eBay-Entwurf einstellen
    python -m app.tools.ebay_setup end <listing_id>     # Angebot beenden
    python -m app.tools.ebay_setup sync                 # Verkaufsabgleich sofort

Gibt nie ein Token oder Secret aus. Der Refresh-Token geht direkt in die
angegebene .env-Datei (Rechte 0600), nicht auf den Bildschirm.
"""

from __future__ import annotations

import argparse
import asyncio
import getpass
import os
import secrets
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

from sqlalchemy import select

from app.config import settings
from app.services import ebay_listing
from app.services.ebay_api import EbayClient, EbayConfig, EbayError, consent_url, extract_auth_code

CATEGORY_TYPES = [{"name": "ALL_EXCLUDING_MOTORS_VEHICLES"}]


def set_env_value(path: Path, key: str, value: str) -> None:
    """KEY=value in einer .env setzen oder anhaengen, Rest unveraendert.

    Schreibt in place (kein Umbenennen): die Datei kann ein einzeln
    eingebundenes Docker-Volume sein, dort scheitert rename()."""
    if "\n" in value or "\r" in value:
        raise ValueError("Wert mit Zeilenumbruch")
    lines = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
    replaced = False
    for index, line in enumerate(lines):
        bare = line.strip().removeprefix("#").strip().removeprefix("export ").strip()
        if bare.startswith(f"{key}="):
            if not replaced:
                lines[index] = f"{key}={value}"
                replaced = True
    if not replaced:
        lines.append(f"{key}={value}")
    data = ("\n".join(lines) + "\n").encode("utf-8")
    # Erst Rechte, dann Inhalt: eine neue Datei ist nie lesbarer als 0600, und
    # fsync, damit ein Abbruch keine halb geschriebene .env hinterlaesst.
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        os.fchmod(fd, 0o600)
        os.write(fd, data)
        os.fsync(fd)
    finally:
        os.close(fd)


def fulfillment_policy_body() -> dict:
    return {
        "name": settings.ebay_policy_name,
        "marketplaceId": settings.ebay_marketplace_id,
        "categoryTypes": CATEGORY_TYPES,
        "handlingTime": {"value": 3, "unit": "DAY"},
        "shippingOptions": [
            {
                "optionType": "DOMESTIC",
                "costType": "FLAT_RATE",
                "shippingServices": [
                    {
                        "sortOrder": 1,
                        "shippingCarrierCode": "DHL",
                        "shippingServiceCode": "DE_DHLPaket",
                        "shippingCost": {"value": f"{settings.ebay_shipping_cost:.2f}", "currency": "EUR"},
                    }
                ],
            }
        ],
    }


def payment_policy_body() -> dict:
    return {
        "name": settings.ebay_policy_name,
        "marketplaceId": settings.ebay_marketplace_id,
        "categoryTypes": CATEGORY_TYPES,
        "immediatePay": True,
    }


def return_policy_body() -> dict:
    # Privatverkauf: Ruecknahme ausgeschlossen. Wer Ruecknahme anbieten will,
    # aendert die Richtlinie in eBay; die App nimmt sie per Name.
    return {
        "name": settings.ebay_policy_name,
        "marketplaceId": settings.ebay_marketplace_id,
        "categoryTypes": CATEGORY_TYPES,
        "returnsAccepted": False,
    }


def location_body() -> dict:
    if not settings.ebay_location_postal_code or not settings.ebay_location_city:
        raise EbayError("EBAY_LOCATION_POSTAL_CODE und EBAY_LOCATION_CITY in der .env setzen (nur PLZ und Ort)")
    return {
        "name": "Lego-Arbitrage",
        "merchantLocationStatus": "ENABLED",
        "locationTypes": ["WAREHOUSE"],
        "location": {
            "address": {
                "postalCode": settings.ebay_location_postal_code,
                "city": settings.ebay_location_city,
                "country": "DE",
            }
        },
    }


POLICY_BODIES = {"fulfillment": fulfillment_policy_body, "payment": payment_policy_body, "return": return_policy_body}


async def ensure_setup(client: EbayClient, out=print) -> None:
    """Idempotent: Geschaeftsrichtlinien einschalten, Richtlinien und Lagerort
    anlegen, wenn es sie (unter dem Namen) noch nicht gibt."""
    programs = await client.opted_in_programs()
    if "SELLING_POLICY_MANAGEMENT" not in programs:
        await client.opt_in_business_policies()
        out("Geschäftsrichtlinien eingeschaltet (eBay braucht bis zu 24 h, bis sie greifen).")
    for kind, body in POLICY_BODIES.items():
        existing = [p for p in await client.list_policies(kind) if p.get("name") == settings.ebay_policy_name]
        if existing:
            out(f"{kind}-Richtlinie vorhanden: {existing[0].get(f'{kind}PolicyId')}")
            continue
        policy_id = await client.create_policy(kind, body())
        out(f"{kind}-Richtlinie angelegt: {policy_id}")
    if await client.get_location(settings.ebay_location_key) is None:
        await client.create_location(settings.ebay_location_key, location_body())
        out(f"Lagerort {settings.ebay_location_key} angelegt.")
    else:
        out(f"Lagerort {settings.ebay_location_key} vorhanden.")


async def status(client: EbayClient, out=print) -> bool:
    out(f"Umgebung: {client.config.environment}, Marktplatz: {client.config.marketplace_id}")
    await client.access_token()
    out("Zugang: ok (Access-Token aus Refresh-Token geholt)")
    ok = True
    try:
        policies = await ebay_listing.resolve_policies(client)
        out(f"Richtlinien: Versand {policies.fulfillment}, Zahlung {policies.payment}, Rücknahme {policies.returns}")
    except EbayError as exc:
        ok = False
        out(f"Richtlinien: {exc.detail}")
    if await client.get_location(settings.ebay_location_key) is None:
        ok = False
        out(f"Lagerort {settings.ebay_location_key}: fehlt — `setup` ausführen")
    else:
        out(f"Lagerort {settings.ebay_location_key}: ok")
    return ok


async def _with_listing(listing_id: int, action: str) -> str:
    from app.api.routes.listings import PHOTO_STORAGE_ROOT
    from app.models.base import async_session
    from app.models.inventory import InventoryItem
    from app.models.listing import Listing

    async with async_session() as session:
        listing = (await session.execute(select(Listing).where(Listing.id == listing_id))).scalar_one_or_none()
        if listing is None:
            raise EbayError(f"Listing {listing_id} nicht gefunden")
        item = (await session.execute(select(InventoryItem).where(InventoryItem.id == listing.item_id))).scalar_one()
        async with EbayClient(EbayConfig.from_settings()) as client:
            try:
                if action == "publish":
                    result = await ebay_listing.publish_listing(listing, item, client, photo_root=PHOTO_STORAGE_ROOT)
                else:
                    await ebay_listing.end_listing(listing, client)
                    result = f"Listing {listing_id} beendet"
            finally:
                # SKU/Angebots-ID auch nach einem Fehler sichern (siehe publish_listing).
                await session.commit()
        return result


async def _sync() -> str:
    from app.tasks.ebay_sync import _sync_async

    return str(await _sync_async())


async def _run(args) -> int:
    if args.command == "consent-url":
        config = EbayConfig.from_settings(require_token=False)
        print(consent_url(config, state=secrets.token_urlsafe(16)))
        return 0
    if args.command == "exchange":
        config = EbayConfig.from_settings(require_token=False)
        raw = getpass.getpass("Weiterleitungsadresse nach dem Consent (wird nicht angezeigt): ")
        async with EbayClient(config) as client:
            payload = await client.exchange_code(extract_auth_code(raw))
        token = payload.get("refresh_token")
        if not token:
            raise EbayError("eBay hat keinen Refresh-Token geliefert")
        set_env_value(Path(args.env_file), "EBAY_REFRESH_TOKEN", token)
        valid_until = datetime.now(UTC) + timedelta(seconds=int(payload.get("refresh_token_expires_in", 0)))
        print(f"Refresh-Token in {args.env_file} gespeichert (0600), gültig bis {valid_until:%d.%m.%Y}.")
        print("Danach die Container neu starten, damit sie ihn lesen.")
        return 0
    if args.command in ("status", "setup"):
        async with EbayClient(EbayConfig.from_settings()) as client:
            if args.command == "setup":
                await ensure_setup(client)
                return 0
            return 0 if await status(client) else 1
    if args.command in ("publish", "end"):
        print(await _with_listing(args.listing_id, args.command))
        return 0
    if args.command == "sync":
        print(await _sync())
        return 0
    return 2


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m app.tools.ebay_setup")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("consent-url")
    exchange = sub.add_parser("exchange")
    exchange.add_argument("--env-file", required=True)
    sub.add_parser("status")
    sub.add_parser("setup")
    for name in ("publish", "end"):
        sub.add_parser(name).add_argument("listing_id", type=int)
    sub.add_parser("sync")
    args = parser.parse_args(argv)
    try:
        return asyncio.run(_run(args))
    except EbayError as exc:
        print(f"Fehler: {exc.detail}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
