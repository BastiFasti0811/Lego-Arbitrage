"""Der Dollarkurs ist ein Messwert, keine Konstante.

BrickEconomy quotiert in USD. Ein fest verdrahteter Kurs verzerrt jeden
Konsenspreis, und weil er kein Datum traegt, faellt die Drift niemandem auf.
Wenn kein Kurs zu holen ist, wird das vermerkt statt verschwiegen.
"""

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import httpx
import pytest

from app.services import fx

ECB_XML = """<?xml version="1.0" encoding="UTF-8"?>
<gesmes:Envelope xmlns:gesmes="http://www.gesmes.org/xml/2002-08-01"
                 xmlns="http://www.ecb.int/vocabulary/2002-08-01/eurofxref">
  <Cube>
    <Cube time="2026-08-25">
      <Cube currency="USD" rate="1.0812"/>
      <Cube currency="JPY" rate="163.21"/>
    </Cube>
  </Cube>
</gesmes:Envelope>"""


def test_parses_usd_rate_as_reciprocal():
    # Der Feed quotiert EUR->USD (Dollar je Euro). Gebraucht wird die Gegenrichtung.
    rate = fx.parse_ecb_rate(ECB_XML)
    assert rate == pytest.approx(1 / 1.0812, rel=1e-6)


def test_returns_none_when_usd_is_absent():
    without_usd = ECB_XML.replace('<Cube currency="USD" rate="1.0812"/>', "")
    assert fx.parse_ecb_rate(without_usd) is None


def test_returns_none_on_malformed_xml():
    assert fx.parse_ecb_rate("<not-xml") is None


def test_returns_none_on_implausible_rate():
    # Ein Kurs von 0 oder ein negativer Wert ist ein Parserfehler, kein Kurs.
    assert fx.parse_ecb_rate(ECB_XML.replace('rate="1.0812"', 'rate="0"')) is None


def test_cached_rate_is_fresh_within_a_day():
    now = datetime(2026, 8, 25, 12, 0, tzinfo=UTC)
    assert fx.is_fresh(now - timedelta(hours=23), now) is True
    assert fx.is_fresh(now - timedelta(hours=25), now) is False
    assert fx.is_fresh(None, now) is False


def test_fallback_rate_is_marked_as_such():
    rate = fx.FxRate(usd_to_eur=fx.FALLBACK_USD_TO_EUR, as_of=None, is_fallback=True)
    assert rate.is_fallback is True
    assert rate.note == "Ersatzkurs — kein EZB-Kurs verfuegbar"


def test_measured_rate_carries_its_date_and_no_note():
    as_of = datetime(2026, 8, 25, tzinfo=UTC)
    rate = fx.FxRate(usd_to_eur=0.925, as_of=as_of, is_fallback=False)
    assert rate.note is None


def test_stale_cached_rate_is_marked_with_its_date():
    # Review-Finding (Critical): ein Kurs, dessen Live-Abruf fehlschlaegt, fiel
    # bisher auf den zwischengelagerten Wert zurueck und kam mit
    # is_fallback=False zurueck — strukturell nicht von einem Kurs zu
    # unterscheiden, der gerade erst gemessen wurde, obwohl er Wochen oder
    # Monate alt sein kann. is_stale ist der dritte, bisher fehlende Zustand.
    as_of = datetime(2026, 7, 1, tzinfo=UTC)
    rate = fx.FxRate(usd_to_eur=0.9, as_of=as_of, is_fallback=False, is_stale=True)
    assert rate.is_fallback is False
    assert rate.note == "Kurs veraltet — zuletzt am 2026-07-01 von der EZB bestaetigt"


def test_stale_note_falls_back_to_unbekannt_without_a_date():
    # Randfall: ein zwischengelagerter Kurswert ohne lesbares Datum (z.B. ein
    # korrupter UPDATED_KEY-Eintrag) darf note nicht zum Absturz bringen.
    rate = fx.FxRate(usd_to_eur=0.9, as_of=None, is_fallback=False, is_stale=True)
    assert rate.note == "Kurs veraltet — zuletzt am unbekannt von der EZB bestaetigt"


# ---------------------------------------------------------------------------
# M2: get_usd_to_eur() selbst hatte keinen Test — nur seine Bausteine
# (parse_ecb_rate, is_fresh, FxRate.note) waren abgedeckt. Genau in dieser
# Orchestrierung fand eine frühere Review den fehlenden dritten Zustand
# (is_stale). Fake-Session-Idiom wie im Rest der Suite (siehe
# test_inventory_valuation_run.py, test_scrape_daily.py): ein Objekt mit
# execute()/scalars(), keine Datenbank.
# ---------------------------------------------------------------------------


class _FakeScalars:
    def __init__(self, rows):
        self._rows = rows

    def __iter__(self):
        return iter(self._rows)


class _FakeResult:
    def __init__(self, rows):
        self._rows = rows

    def scalars(self):
        return _FakeScalars(self._rows)


class _FakeFxSession:
    """Bedient nur _load_cached()'s eine SELECT — keiner der drei Tests
    unten erreicht _store() (siehe jeweilige Vorbedingung)."""

    def __init__(self, rows):
        self._rows = rows

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc_info):
        return False

    async def execute(self, _statement):
        return _FakeResult(self._rows)


def _setting_rows(*, rate: str | None, updated_at: str | None) -> list:
    rows = []
    if rate is not None:
        rows.append(SimpleNamespace(key=fx.RATE_KEY, value=rate))
    if updated_at is not None:
        rows.append(SimpleNamespace(key=fx.UPDATED_KEY, value=updated_at))
    return rows


@pytest.mark.asyncio
async def test_a_fresh_cached_rate_is_returned_without_a_live_fetch(monkeypatch):
    now = datetime.now(UTC)
    rows = _setting_rows(rate="0.93", updated_at=(now - timedelta(hours=2)).isoformat())
    monkeypatch.setattr(fx, "async_session", lambda: _FakeFxSession(rows))

    async def _must_not_be_called():
        raise RuntimeError("ein frischer Cache-Kurs darf keinen Live-Abruf ausloesen")

    monkeypatch.setattr(fx, "_fetch_ecb", _must_not_be_called)

    rate = await fx.get_usd_to_eur()

    assert rate.usd_to_eur == pytest.approx(0.93)
    assert rate.is_fallback is False
    assert rate.is_stale is False
    assert rate.note is None


@pytest.mark.asyncio
async def test_a_failed_fetch_falls_back_to_a_stale_cached_rate(monkeypatch):
    # Review-Finding (Critical), hier auf Ebene von get_usd_to_eur() selbst
    # nachvollzogen statt nur gegen FxRate direkt (siehe
    # test_stale_cached_rate_is_marked_with_its_date oben): ein Kurs, dessen
    # Live-Abruf fehlschlaegt, faellt auf den zwischengelagerten Wert zurueck
    # - und der ist hier bereits laenger als MAX_AGE alt.
    stale_as_of = datetime.now(UTC) - timedelta(days=10)
    rows = _setting_rows(rate="0.87", updated_at=stale_as_of.isoformat())
    monkeypatch.setattr(fx, "async_session", lambda: _FakeFxSession(rows))

    async def _fail():
        raise httpx.ConnectTimeout("EZB nicht erreichbar")

    monkeypatch.setattr(fx, "_fetch_ecb", _fail)

    rate = await fx.get_usd_to_eur()

    assert rate.usd_to_eur == pytest.approx(0.87)
    assert rate.as_of == stale_as_of
    assert rate.is_fallback is False
    assert rate.is_stale is True
    assert rate.note == f"Kurs veraltet — zuletzt am {stale_as_of.date().isoformat()} von der EZB bestaetigt"


@pytest.mark.asyncio
async def test_no_cache_and_a_failed_fetch_falls_back_to_the_constant(monkeypatch):
    monkeypatch.setattr(fx, "async_session", lambda: _FakeFxSession([]))

    async def _fail():
        raise httpx.ConnectTimeout("EZB nicht erreichbar")

    monkeypatch.setattr(fx, "_fetch_ecb", _fail)

    rate = await fx.get_usd_to_eur()

    assert rate.usd_to_eur == fx.FALLBACK_USD_TO_EUR
    assert rate.as_of is None
    assert rate.is_fallback is True
    assert rate.is_stale is False
    assert rate.note == "Ersatzkurs — kein EZB-Kurs verfuegbar"
