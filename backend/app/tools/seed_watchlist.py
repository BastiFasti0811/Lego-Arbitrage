"""One-shot seeding of the arbitrage watchlist (idempotent).

Run inside the api container:

    docker compose --env-file .env.prod -f docker-compose.prod.yml \
        exec -T api python -m app.tools.seed_watchlist

Kuratierte Startliste Stand 2026-08-16: eigene Inventar-Sets plus EOL-/
Retiring-Kandidaten (Quellen: Jay's Brick Blog Juni-2026-Update, GamePro/
StoneWars EOL-Listen, brick-tracker.de, klemmbaustein.com, lendabrick.com).
EOL-Daten sind fluide — Liste bei Gelegenheit gegen frische Quellen pruefen.
"""

import asyncio

from sqlalchemy import select

from app.models import LegoSet, WatchlistItem
from app.models.base import async_session

SETS: list[tuple[str, str]] = [
    # Eigene Inventar-Sets
    ("75416", "Astromech-Droide Chopper (C1-10P)"),
    ("75322", "AT-ST auf Hoth"),
    ("75300", "Imperial TIE Fighter"),
    ("75281", "Anakins Jedi Interceptor"),
    ("75292", "The Mandalorian - Transporter des Kopfgeldjaegers"),
    # EOL 2026 / bereits ausgelaufen — Retiring-Recherche 2026-08-16
    ("75192", "UCS Millennium Falcon"),
    ("76417", "Gringotts Zaubererbank - Sammleredition"),
    ("42143", "Ferrari Daytona SP3"),
    ("10326", "Natural History Museum"),
    ("75331", "The Razor Crest"),
    ("10312", "Jazzclub"),
    ("10302", "Optimus Prime"),
    ("10327", "Dune Atreides Royal Ornithopter"),
    ("21350", "Jaws"),
    ("21333", "Vincent van Gogh - Sternennacht"),
    ("76435", "Hogwarts: Die Grosse Halle"),
    ("75337", "AT-TE Walker"),
    ("75325", "Mandalorian N-1 Starfighter"),
    ("60337", "Personen-Schnellzug"),
    ("21347", "Rote Londoner Telefonzelle"),
    ("10325", "Almhuette"),
    ("10331", "Eisvogel"),
    ("75388", "Jedi Bobs Starfighter"),
    ("75345", "Clone Troopers der 501. Legion Battle Pack"),
    ("75333", "Obi-Wan Kenobis Jedi Starfighter"),
    ("42151", "Bugatti Bolide"),
    ("76934", "Ferrari F40 Supercar"),
    ("76917", "2F2F Nissan Skyline GT-R (R34)"),
    ("76425", "Hedwig im Ligusterweg 4"),
    ("76443", "Hagrids & Harrys Motorradfahrt"),
    ("75404", "Midi Acclamator-Klasse Angriffsschiff"),
]


async def seed() -> None:
    created_sets = 0
    created_watch = 0
    async with async_session() as session:
        for set_number, name in SETS:
            result = await session.execute(select(LegoSet).where(LegoSet.set_number == set_number))
            lego_set = result.scalar_one_or_none()
            if lego_set is None:
                lego_set = LegoSet(set_number=set_number, set_name=name)
                session.add(lego_set)
                await session.flush()
                created_sets += 1

            watch = (
                await session.execute(select(WatchlistItem).where(WatchlistItem.set_id == lego_set.id))
            ).scalars().first()
            if watch is None:
                session.add(WatchlistItem(set_id=lego_set.id, is_active=True))
                created_watch += 1
            elif not watch.is_active:
                watch.is_active = True
                created_watch += 1
        await session.commit()

    print(
        f"Sets neu angelegt: {created_sets}, "
        f"Watchlist-Eintraege neu/aktiviert: {created_watch}, Liste gesamt: {len(SETS)}"
    )


if __name__ == "__main__":
    asyncio.run(seed())
