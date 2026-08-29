"""Der Dollarkurs ist ein Messwert, keine Konstante.

BrickEconomy quotiert in USD. Ein fest verdrahteter Kurs verzerrt jeden
Konsenspreis, und weil er kein Datum traegt, faellt die Drift niemandem auf.
Wenn kein Kurs zu holen ist, wird das vermerkt statt verschwiegen.
"""

from datetime import UTC, datetime, timedelta

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
