"""Claude-Anbindung: Foto-Analyse und Anzeigentexte (Spec: KI-Anbindung).

Vertrag mit den Endpoints: wirft ausschliesslich AIProviderError mit
deutscher, UI-tauglicher .detail — nie rohe SDK-Exceptions.
"""

import base64

import anthropic
import structlog
from pydantic import ValidationError

from app.ai.schemas import ItemDraft, ListingText
from app.config import settings

logger = structlog.get_logger()

_ANALYZE_SYSTEM = (
    "Du katalogisierst gebrauchte Gegenstaende fuer den Privatverkauf auf deutschen "
    "Plattformen (Kleinanzeigen, eBay). Du bekommst Fotos EINES Artikels und lieferst "
    "einen nuechternen, ehrlichen Entwurf. Keine Uebertreibungen, keine erfundenen "
    "Markennamen oder Modellnummern — wenn unsicher, schreibe es generisch und setze "
    "confidence auf 'low'. Preise in Euro fuer den deutschen Gebrauchtmarkt."
)

_LISTING_SYSTEM = (
    "Du schreibst deutsche Kleinanzeigen-/eBay-Texte fuer Privatverkaeufe: ehrlich, "
    "konkret, ohne Marketing-Floskeln. Titel maximal 65 Zeichen (eBay 80), "
    "Beschreibung 3-6 kurze Saetze, am Ende 'Versand moeglich.' wenn plausibel. "
    "Keine Emojis."
)


class AIProviderError(Exception):
    def __init__(self, detail: str):
        super().__init__(detail)
        self.detail = detail


class ClaudeProvider:
    def __init__(self, client: anthropic.AsyncAnthropic | None = None):
        self._client = client or anthropic.AsyncAnthropic(
            api_key=settings.anthropic_api_key, timeout=90.0, max_retries=1
        )
        self._model = settings.ai_model

    async def analyze_photos(
        self, photos: list[tuple[bytes, str]], hints: str | None, product_groups: list[str]
    ) -> ItemDraft:
        blocks = [
            {
                "type": "image",
                "source": {
                    "type": "base64",
                    "media_type": media_type,
                    "data": base64.standard_b64encode(data).decode("utf-8"),
                },
            }
            for data, media_type in photos[:4]
        ]
        prompt = (
            "Analysiere den Artikel auf den Fotos.\n"
            f"Waehle product_group aus genau dieser Liste: {', '.join(product_groups)}.\n"
            "condition: NEW_SEALED, NEW_OPEN_BOX, USED_COMPLETE oder USED_INCOMPLETE.\n"
            "search_query: der eBay-Suchbegriff, mit dem man verkaufte Exemplare "
            "dieses Artikels findet (Marke + Modell, ohne Zustand).\n"
            "platform_category: passende Kleinanzeigen-Kategorie als Pfad, "
            "z. B. 'Elektronik > Audio & Hifi'.\n"
            "price_min/price_max: realistischer Verkaufsrahmen in Euro."
        )
        if hints:
            prompt += f"\nHinweise des Verkaeufers: {hints}"
        blocks.append({"type": "text", "text": prompt})
        try:
            response = await self._client.messages.parse(
                model=self._model,
                max_tokens=2048,
                system=_ANALYZE_SYSTEM,
                messages=[{"role": "user", "content": blocks}],
                output_format=ItemDraft,
            )
        except anthropic.APIStatusError as exc:
            logger.error("ai.analyze_failed", status=exc.status_code)
            raise AIProviderError(f"KI-Analyse fehlgeschlagen (HTTP {exc.status_code})") from exc
        except anthropic.APIConnectionError as exc:
            raise AIProviderError("KI-Dienst nicht erreichbar") from exc
        except anthropic.AnthropicError as exc:
            logger.error("ai.analyze_failed", error_type=type(exc).__name__)
            raise AIProviderError("KI-Aufruf fehlgeschlagen") from exc
        except ValidationError as exc:
            # anthropic 1.x validiert schon in parse(): abgeschnittenes JSON
            # (max_tokens) oder fehlende Felder kommen hier an, nicht als
            # parsed_output=None.
            logger.error("ai.analyze_invalid_output", errors=exc.error_count())
            raise AIProviderError("Die KI-Antwort war unvollstaendig — bitte erneut versuchen") from exc
        if response.stop_reason == "refusal":
            raise AIProviderError("Die KI hat die Analyse dieser Fotos abgelehnt")
        if response.parsed_output is None:
            raise AIProviderError("Die KI-Antwort war unvollstaendig — bitte erneut versuchen")
        return response.parsed_output

    async def write_listing(
        self,
        *,
        name: str,
        condition: str,
        notes: str | None,
        platform: str,
        price: float,
        price_type: str,
        quantity: int = 1,
    ) -> ListingText:
        price_suffix = " VB" if price_type == "VB" else ""
        prompt = (
            f"Schreibe den Anzeigentext fuer {platform.title()}.\n"
            f"Artikel: {name}\nZustand: {condition}\n"
            f"Preis: {price:.0f} Euro{price_suffix} (in den Text uebernehmen).\n"
        )
        if quantity > 1:
            prompt += f"Menge: {quantity} Stueck vorhanden, der Preis gilt pro Stueck.\n"
        if notes:
            prompt += f"Bekannte Details: {notes}\n"
        try:
            response = await self._client.messages.parse(
                model=self._model,
                max_tokens=1024,
                system=_LISTING_SYSTEM,
                messages=[{"role": "user", "content": prompt}],
                output_format=ListingText,
            )
        except anthropic.APIStatusError as exc:
            logger.error("ai.write_listing_failed", status=exc.status_code)
            raise AIProviderError(f"Textgenerierung fehlgeschlagen (HTTP {exc.status_code})") from exc
        except anthropic.APIConnectionError as exc:
            raise AIProviderError("KI-Dienst nicht erreichbar") from exc
        except anthropic.AnthropicError as exc:
            logger.error("ai.write_listing_failed", error_type=type(exc).__name__)
            raise AIProviderError("KI-Aufruf fehlgeschlagen") from exc
        except ValidationError as exc:
            logger.error("ai.write_listing_invalid_output", errors=exc.error_count())
            raise AIProviderError("Die KI-Antwort war unvollstaendig — bitte erneut versuchen") from exc
        if response.stop_reason == "refusal":
            raise AIProviderError("Die KI hat die Texterstellung abgelehnt")
        if response.parsed_output is None:
            raise AIProviderError("Die KI-Antwort war unvollstaendig — bitte erneut versuchen")
        return response.parsed_output
