"""Pydantic-Modelle fuer die KI-Anbindung (Spec: KI-Anbindung, Foto-Analyse + Anzeigentexte)."""

from pydantic import BaseModel


class ItemDraft(BaseModel):
    """Ergebnis der Foto-Analyse: Entwurf fuer eine neue Inventar-Position."""

    name: str
    product_group: str
    condition: str
    description: str
    search_query: str
    platform_category: str
    price_min: float
    price_max: float
    confidence: str


class ListingText(BaseModel):
    """Von der KI geschriebener Anzeigentext fuer eine Zielplattform."""

    title: str
    body: str
    platform_category: str
