"""KI-Anbindung: Foto-Analyse und Anzeigentexte (Spec: KI-Anbindung)."""

from app.ai.claude_provider import AIProviderError, ClaudeProvider
from app.ai.photo_prep import prepare_photo
from app.ai.schemas import ItemDraft, ListingText
from app.config import settings

__all__ = ["AIProviderError", "ItemDraft", "ListingText", "get_provider", "prepare_photo"]

# Registry statt if/elif: neue Provider (z. B. ein zweiter Anbieter) kommen als
# weiterer Eintrag dazu, ohne get_provider() selbst anzufassen.
_PROVIDERS = {"claude": ClaudeProvider}


def get_provider() -> ClaudeProvider:
    """Liefert den in settings.ai_provider konfigurierten KI-Provider.

    Wirft AIProviderError, wenn kein Provider nutzbar ist — fehlender Key
    oder ein settings.ai_provider-Wert ohne Registrierung.
    """
    if not settings.anthropic_api_key:
        raise AIProviderError("ANTHROPIC_API_KEY fehlt — in backend/.env setzen")
    provider_cls = _PROVIDERS.get(settings.ai_provider)
    if provider_cls is None:
        raise AIProviderError(f"Unbekannter KI-Provider konfiguriert: {settings.ai_provider!r}")
    return provider_cls()
