"""Link-Felder nehmen nur http(s) an (Haertungsbuendel Security-Nachtlauf 2).

Die Werte landen als href im Frontend. Gespeichert wird nur, was mit http://
oder https:// beginnt und einen Host hat; leere Werte behalten ihre bisherige
Bedeutung (Feld leeren).
"""

from datetime import date

import pytest
from pydantic import ValidationError

from app.api.routes.inventory import InventoryAdd, InventoryUpdate
from app.api.routes.listings import ListingCreate, ListingUpdate
from app.security.link_url import MAX_LINK_LENGTH, check_link_url

GOOD = [
    "https://www.kleinanzeigen.de/s-anzeige/lego-42143/123",
    "http://192.168.50.89/lego/",
    "HTTPS://Example.com/x?y=1#z",
]
BAD = [
    "javascript:alert(1)",
    "JaVaScRiPt:alert(1)",
    "data:text/html;base64,PHNjcmlwdD4=",
    "vbscript:msgbox(1)",
    "ftp://example.com/file",
    "//example.com/pfad",
    "www.example.com",
    "https://",
    "https:///nur-pfad",
    "java\tscript:alert(1)",
    "https://exa mple.com/",
    "https://example.com/\x00",
]


@pytest.mark.parametrize("url", GOOD)
def test_http_and_https_pass(url):
    assert check_link_url(url) == url


def test_surrounding_whitespace_is_trimmed():
    assert check_link_url("  https://example.com/a  ") == "https://example.com/a"


@pytest.mark.parametrize("url", BAD)
def test_other_schemes_and_broken_links_fail(url):
    with pytest.raises(ValueError):
        check_link_url(url)


@pytest.mark.parametrize("value", [None, "", "   "])
def test_empty_values_keep_their_meaning(value):
    assert check_link_url(value) == value


def _add(**overrides) -> InventoryAdd:
    data = {"set_number": "42143", "set_name": "Ferrari", "buy_date": date(2026, 1, 1)}
    data.update(overrides)
    return InventoryAdd(**data)


SCHEMA_FIELDS = [
    (_add, "image_url"),
    (_add, "buy_url"),
    (_add, "reference_url"),
    (InventoryUpdate, "image_url"),
    (InventoryUpdate, "buy_url"),
    (InventoryUpdate, "reference_url"),
    (lambda **kw: ListingCreate(platform="EBAY", current_price=10, **kw), "url"),
    (ListingUpdate, "url"),
]


@pytest.mark.parametrize(("build", "field"), SCHEMA_FIELDS)
def test_every_link_field_rejects_javascript(build, field):
    with pytest.raises(ValidationError):
        build(**{field: "javascript:alert(document.cookie)"})


@pytest.mark.parametrize(("build", "field"), SCHEMA_FIELDS)
def test_every_link_field_keeps_a_normal_link(build, field):
    url = "https://www.bricklink.com/v2/catalog/catalogitem.page?S=42143-1"
    assert getattr(build(**{field: url}), field) == url


@pytest.mark.parametrize(("build", "field"), SCHEMA_FIELDS)
def test_every_link_field_has_a_length_limit(build, field):
    with pytest.raises(ValidationError):
        build(**{field: "https://example.com/" + "a" * MAX_LINK_LENGTH})


def test_optional_fields_still_accept_null_and_empty():
    assert InventoryUpdate(buy_url=None).buy_url is None
    assert InventoryUpdate(reference_url="").reference_url == ""
    # ListingUpdate: "" leert die URL wie bisher (Route setzt den Wert direkt).
    assert ListingUpdate(url="").url == ""
