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

# (set_number, set_name, theme, release_year) — theme und release_year sind
# NOT-NULL-Spalten. Jahre sind Startwerte; der taegliche Metadaten-Refresh
# (LEGO.com/BrickMerge) verfeinert sie.
SETS: list[tuple[str, str, str, int]] = [
    # Eigene Inventar-Sets
    ("75416", "Astromech-Droide Chopper (C1-10P)", "Star Wars", 2025),
    ("75322", "AT-ST auf Hoth", "Star Wars", 2022),
    ("75300", "Imperial TIE Fighter", "Star Wars", 2021),
    ("75281", "Anakins Jedi Interceptor", "Star Wars", 2020),
    ("75292", "The Mandalorian - Transporter des Kopfgeldjaegers", "Star Wars", 2020),
    # EOL 2026 / bereits ausgelaufen — Retiring-Recherche 2026-08-16
    ("75192", "UCS Millennium Falcon", "Star Wars", 2017),
    ("76417", "Gringotts Zaubererbank - Sammleredition", "Harry Potter", 2023),
    ("42143", "Ferrari Daytona SP3", "Technic", 2022),
    ("10326", "Natural History Museum", "Icons", 2023),
    ("75331", "The Razor Crest", "Star Wars", 2022),
    ("10312", "Jazzclub", "Icons", 2023),
    ("10302", "Optimus Prime", "Icons", 2022),
    ("10327", "Dune Atreides Royal Ornithopter", "Icons", 2024),
    ("21350", "Jaws", "Ideas", 2024),
    ("21333", "Vincent van Gogh - Sternennacht", "Ideas", 2022),
    ("76435", "Hogwarts: Die Grosse Halle", "Harry Potter", 2024),
    ("75337", "AT-TE Walker", "Star Wars", 2022),
    ("75325", "Mandalorian N-1 Starfighter", "Star Wars", 2022),
    ("60337", "Personen-Schnellzug", "City", 2022),
    ("21347", "Rote Londoner Telefonzelle", "Ideas", 2024),
    ("10325", "Almhuette", "Icons", 2023),
    ("10331", "Eisvogel", "Icons", 2024),
    ("75388", "Jedi Bobs Starfighter", "Star Wars", 2024),
    ("75345", "Clone Troopers der 501. Legion Battle Pack", "Star Wars", 2023),
    ("75333", "Obi-Wan Kenobis Jedi Starfighter", "Star Wars", 2022),
    ("42151", "Bugatti Bolide", "Technic", 2023),
    ("76934", "Ferrari F40 Supercar", "Speed Champions", 2025),
    ("76917", "2F2F Nissan Skyline GT-R (R34)", "Speed Champions", 2023),
    ("76425", "Hedwig im Ligusterweg 4", "Harry Potter", 2023),
    ("76443", "Hagrids & Harrys Motorradfahrt", "Harry Potter", 2025),
    ("75404", "Midi Acclamator-Klasse Angriffsschiff", "Star Wars", 2025),
]


async def seed() -> None:
    created_sets = 0
    created_watch = 0
    async with async_session() as session:
        for set_number, name, theme, release_year in SETS:
            result = await session.execute(select(LegoSet).where(LegoSet.set_number == set_number))
            lego_set = result.scalar_one_or_none()
            if lego_set is None:
                lego_set = LegoSet(set_number=set_number, set_name=name, theme=theme, release_year=release_year)
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
