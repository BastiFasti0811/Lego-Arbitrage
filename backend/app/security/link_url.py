"""Schema-Pruefung fuer Links, die das Frontend als href ausgibt.

Die Felder (Listing-URL, Foto-Link, Original-Link, eigener Link) ruft der
Server nie ab, sie landen nur als `<a href>` im Browser. React 19 blockt
`javascript:` bereits; die Pruefung hier sorgt dafuer, dass gar nichts
ausser http(s) gespeichert wird (auch kein `data:` oder `vbscript:`).

None bleibt None, nur aus Leerzeichen bestehende Werte werden zu "": die
Routen behandeln "" und None schon heute (Listing-URL leeren, Inventar-Feld
leeren).
"""

from typing import Annotated
from urllib.parse import urlsplit

from pydantic import AfterValidator, Field

MAX_LINK_LENGTH = 2000
_ALLOWED_SCHEMES = {"http", "https"}


def check_link_url(value: str | None) -> str | None:
    if value is None:
        return None
    stripped = value.strip()
    if not stripped:
        return ""
    if any(ord(ch) < 0x20 or ch == "\x7f" or ch.isspace() for ch in stripped):
        raise ValueError("Link darf keine Leer- oder Steuerzeichen enthalten")
    parts = urlsplit(stripped)
    if parts.scheme.lower() not in _ALLOWED_SCHEMES or not parts.hostname:
        raise ValueError("Link muss mit http:// oder https:// beginnen")
    return stripped


_BoundedStr = Annotated[str, Field(max_length=MAX_LINK_LENGTH)]
LinkUrl = Annotated[_BoundedStr, AfterValidator(check_link_url)]
OptionalLinkUrl = Annotated[_BoundedStr | None, AfterValidator(check_link_url)]
