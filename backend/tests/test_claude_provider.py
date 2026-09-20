"""Tests fuer app.ai.claude_provider und app.ai.get_provider (PR 2, Task 5).

Das SDK ist vollstaendig gefaked (_FakeMessages.parse) - kein Netzzugriff.
"""

import base64

import pytest

from app.ai import get_provider
from app.ai.claude_provider import AIProviderError, ClaudeProvider
from app.ai.schemas import ItemDraft, ListingText
from app.config import settings


class _FakeParseResponse:
    def __init__(self, parsed, stop_reason="end_turn"):
        self.parsed_output = parsed
        self.stop_reason = stop_reason


class _FakeMessages:
    def __init__(self, response):
        self._response = response
        self.calls = []

    async def parse(self, **kwargs):
        self.calls.append(kwargs)
        return self._response


class _FakeClient:
    def __init__(self, response):
        self.messages = _FakeMessages(response)


_ITEM_DRAFT = ItemDraft(
    name="LEGO Star Wars Millennium Falcon",
    product_group="LEGO",
    condition="USED_COMPLETE",
    description="Gebrauchtes Set, augenscheinlich vollstaendig.",
    search_query="LEGO 75192 Millennium Falcon",
    platform_category="Spielzeug > Bausteine",
    price_min=450.0,
    price_max=600.0,
    confidence="medium",
)

_LISTING_TEXT = ListingText(
    title="LEGO Millennium Falcon - gebraucht, komplett",
    body="Verkaufe mein gebrauchtes Set. Alle Teile vorhanden. Versand moeglich.",
    platform_category="Spielzeug > Bausteine",
)


@pytest.mark.asyncio
async def test_analyze_photos_returns_parsed_item_draft():
    fake_client = _FakeClient(_FakeParseResponse(_ITEM_DRAFT))
    provider = ClaudeProvider(client=fake_client)
    photos = [(b"\xff\xd8\xff\xe0fake-jpeg-bytes", "image/jpeg")]

    draft = await provider.analyze_photos(photos, hints=None, product_groups=["LEGO", "Sonstiges Spielzeug"])

    assert draft is _ITEM_DRAFT
    assert len(fake_client.messages.calls) == 1
    call = fake_client.messages.calls[0]
    assert call["model"] == settings.ai_model
    assert call["output_format"] is ItemDraft

    content = call["messages"][0]["content"]
    assert content[0]["type"] == "image"
    assert content[0]["source"]["media_type"] == "image/jpeg"
    assert content[0]["source"]["data"] == base64.standard_b64encode(photos[0][0]).decode("utf-8")

    prompt_text = content[-1]["text"]
    assert content[-1]["type"] == "text"
    assert "LEGO, Sonstiges Spielzeug" in prompt_text
    # Kanonischer Zustandswert aus app/domain/condition.py, nicht "NEW_OPEN" -
    # der generische PATCH-Pfad normalisiert das Feld nicht nach.
    assert "NEW_OPEN_BOX" in prompt_text


@pytest.mark.asyncio
async def test_analyze_photos_includes_hints_when_given():
    fake_client = _FakeClient(_FakeParseResponse(_ITEM_DRAFT))
    provider = ClaudeProvider(client=fake_client)

    await provider.analyze_photos(
        [(b"data", "image/jpeg")], hints="Deckel leicht verkratzt", product_groups=["LEGO"]
    )

    prompt_text = fake_client.messages.calls[0]["messages"][0]["content"][-1]["text"]
    assert "Deckel leicht verkratzt" in prompt_text


@pytest.mark.asyncio
async def test_analyze_photos_refusal_raises_german_error():
    fake_client = _FakeClient(_FakeParseResponse(None, stop_reason="refusal"))
    provider = ClaudeProvider(client=fake_client)

    with pytest.raises(AIProviderError) as exc_info:
        await provider.analyze_photos([(b"data", "image/jpeg")], hints=None, product_groups=["LEGO"])

    assert "abgelehnt" in exc_info.value.detail


@pytest.mark.asyncio
async def test_analyze_photos_incomplete_response_raises_german_error():
    # stop_reason != "refusal", aber parsed_output ist trotzdem None - z. B.
    # Abbruch mitten im JSON durch max_tokens. Ohne eigenen Guard wuerde das
    # als AttributeError statt als sauberer AIProviderError durchschlagen.
    fake_client = _FakeClient(_FakeParseResponse(None, stop_reason="max_tokens"))
    provider = ClaudeProvider(client=fake_client)

    with pytest.raises(AIProviderError) as exc_info:
        await provider.analyze_photos([(b"data", "image/jpeg")], hints=None, product_groups=["LEGO"])

    assert "unvollstaendig" in exc_info.value.detail


@pytest.mark.asyncio
async def test_write_listing_passes_platform_price_and_returns_listing_text():
    fake_client = _FakeClient(_FakeParseResponse(_LISTING_TEXT))
    provider = ClaudeProvider(client=fake_client)

    listing = await provider.write_listing(
        name="LEGO 75192 Millennium Falcon",
        condition="USED_COMPLETE",
        notes="Karton leicht bestossen",
        platform="kleinanzeigen",
        price=480.0,
        price_type="VB",
    )

    assert listing is _LISTING_TEXT
    call = fake_client.messages.calls[0]
    assert call["output_format"] is ListingText
    assert call["model"] == settings.ai_model

    prompt_text = call["messages"][0]["content"]
    assert "kleinanzeigen" in prompt_text.lower()
    assert "480" in prompt_text
    assert "VB" in prompt_text
    assert "Karton leicht bestossen" in prompt_text


@pytest.mark.asyncio
async def test_write_listing_refusal_raises_german_error():
    fake_client = _FakeClient(_FakeParseResponse(None, stop_reason="refusal"))
    provider = ClaudeProvider(client=fake_client)

    with pytest.raises(AIProviderError) as exc_info:
        await provider.write_listing(
            name="Testartikel",
            condition="USED_COMPLETE",
            notes=None,
            platform="ebay",
            price=10.0,
            price_type="FIXED",
        )

    assert "abgelehnt" in exc_info.value.detail


@pytest.mark.asyncio
async def test_write_listing_incomplete_response_raises_german_error():
    fake_client = _FakeClient(_FakeParseResponse(None, stop_reason="max_tokens"))
    provider = ClaudeProvider(client=fake_client)

    with pytest.raises(AIProviderError) as exc_info:
        await provider.write_listing(
            name="Testartikel", condition="USED_COMPLETE", notes=None, platform="ebay", price=10.0, price_type="VB"
        )

    assert "unvollstaendig" in exc_info.value.detail


class _RaisingMessages:
    """messages.parse, das wie das echte SDK (anthropic 1.x) schon beim Parsen
    scheitert: abgeschnittenes JSON oder fehlende Pflichtfelder kommen dort als
    pydantic.ValidationError aus parse() heraus, nicht als parsed_output=None."""

    async def parse(self, **kwargs):
        ItemDraft.model_validate_json('{"name": "abgeschnit')


class _RaisingClient:
    messages = _RaisingMessages()


@pytest.mark.asyncio
async def test_analyze_photos_schema_violation_raises_german_error():
    provider = ClaudeProvider(client=_RaisingClient())

    with pytest.raises(AIProviderError) as exc_info:
        await provider.analyze_photos([(b"data", "image/jpeg")], hints=None, product_groups=["LEGO"])

    assert "unvollstaendig" in exc_info.value.detail


@pytest.mark.asyncio
async def test_write_listing_schema_violation_raises_german_error():
    provider = ClaudeProvider(client=_RaisingClient())

    with pytest.raises(AIProviderError) as exc_info:
        await provider.write_listing(
            name="Testartikel", condition="USED_COMPLETE", notes=None, platform="ebay", price=10.0, price_type="VB"
        )

    assert "unvollstaendig" in exc_info.value.detail


@pytest.mark.asyncio
async def test_write_listing_states_quantity_and_that_price_is_per_piece():
    # current_market_price ist ein Stueckpreis; ohne Mengenangabe beschreibt der
    # Text bei einem Posten von drei Stueck nur eines.
    fake_client = _FakeClient(_FakeParseResponse(_LISTING_TEXT))
    provider = ClaudeProvider(client=fake_client)

    await provider.write_listing(
        name="LEGO 75267 Mandalorianer Battle Pack", condition="NEW_SEALED", notes=None,
        platform="kleinanzeigen", price=20.0, price_type="VB", quantity=3,
    )

    prompt_text = fake_client.messages.calls[0]["messages"][0]["content"]
    assert "3 Stueck" in prompt_text
    assert "pro Stueck" in prompt_text


def test_get_provider_without_key_raises(monkeypatch):
    monkeypatch.setattr(settings, "anthropic_api_key", None)

    with pytest.raises(AIProviderError):
        get_provider()


def test_get_provider_with_unknown_provider_raises(monkeypatch):
    monkeypatch.setattr(settings, "anthropic_api_key", "sk-test-key")
    monkeypatch.setattr(settings, "ai_provider", "gpt-unknown")

    with pytest.raises(AIProviderError):
        get_provider()


def test_get_provider_returns_claude_provider(monkeypatch):
    monkeypatch.setattr(settings, "anthropic_api_key", "sk-test-key")
    monkeypatch.setattr(settings, "ai_provider", "claude")

    provider = get_provider()

    assert isinstance(provider, ClaudeProvider)


@pytest.mark.parametrize("call", ["analyze", "listing"])
@pytest.mark.asyncio
async def test_requests_leave_room_for_thinking_tokens(call):
    # claude-opus-5 denkt per Default mit, und Denk-Tokens zaehlen gegen
    # max_tokens. Mit 2048 (Analyse) bzw. 1024 (Anzeigentext) bricht die
    # Antwort mitten im JSON ab: bezahlter Aufruf, Ergebnis unbrauchbar.
    fake_client = _FakeClient(_FakeParseResponse(_ITEM_DRAFT if call == "analyze" else _LISTING_TEXT))
    provider = ClaudeProvider(client=fake_client)

    if call == "analyze":
        await provider.analyze_photos([(b"data", "image/jpeg")], hints=None, product_groups=["LEGO"])
    else:
        await provider.write_listing(
            name="Testartikel", condition="USED_COMPLETE", notes=None,
            platform="ebay", price=10.0, price_type="FIXED",
        )

    assert fake_client.messages.calls[0]["max_tokens"] >= 8000


def test_client_timeout_covers_a_long_thinking_turn():
    # Mit Denkphase dauert eine Foto-Analyse laenger als die urspruenglichen
    # 90 s; laeuft der HTTP-Timeout ab, ist der Aufruf trotzdem bezahlt.
    provider = ClaudeProvider()

    assert provider._client.timeout >= 300


def test_client_does_not_retry_a_timed_out_call():
    # Ein Timeout gilt dem SDK als wiederholbar. Bei 300 s Timeout hiesse das:
    # bis zu 10 min Wartezeit und ein zweites Mal bezahlen fuer denselben
    # Aufruf. Lieber einmal sauber scheitern.
    provider = ClaudeProvider()

    assert provider._client.max_retries == 0
