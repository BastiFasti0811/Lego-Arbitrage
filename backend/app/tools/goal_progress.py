"""Zielstand fuer das Laufprotokoll der Nachtlaeufe (Memory laufprotokoll).

Im api-Container auf dem Prod-Host:

    docker exec lego-api-prod python -m app.tools.goal_progress

Erste Zeile: das Feld "Zielstand: <inseriert>/30, <verkauft>/15, Deals diese
Woche <n>/3" zum Einfuegen in die Protokollzeile. Danach die Wochenzahlen,
mit --json alles maschinenlesbar.
"""

import argparse
import asyncio
import json

from app.api.routes.goals import to_response
from app.models.base import async_session
from app.services.goal_progress import load_goal_progress, protocol_segment


async def _main(as_json: bool) -> None:
    async with async_session() as session:
        progress = await load_goal_progress(session)
    if as_json:
        print(json.dumps(to_response(progress).model_dump(mode="json"), ensure_ascii=False, indent=2))
        return
    print(protocol_segment(progress))
    for title, numbers in (("laufende Woche", progress.current), ("Vorwoche", progress.previous)):
        deals, sales = numbers.deals, numbers.sales
        print(
            f"{title} {numbers.week.label}: Deals {deals.total} (GO* {deals.go_star}, GO {deals.go}, "
            f"Auktion {deals.auctions}; CHECK {deals.checks}), inseriert +{sales.listed}, verkauft +{sales.sold}"
        )


def main() -> None:
    parser = argparse.ArgumentParser(description="Zielstand Lego-Arbitrage")
    parser.add_argument("--json", action="store_true", help="alles als JSON")
    asyncio.run(_main(parser.parse_args().json))


if __name__ == "__main__":
    main()
