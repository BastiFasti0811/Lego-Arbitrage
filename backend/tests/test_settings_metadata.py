from app.api.routes.settings import describe_stored_value


def test_metadata_describes_a_stored_secret_without_revealing_it():
    # Review-Finding H1: GET maskiert Secrets mit acht Punkten. Das Auge zeigte
    # deshalb die Maske, und der Zeichenzaehler meldete fuer jedes Secret "8".
    # Genau der Produktionsfall (Token mit 46 Zeichen, Chat-ID mit Bot-Namen)
    # blieb unsichtbar. Laenge und Whitespace kommen jetzt vom Server.
    length, has_ws, tail = describe_stored_value("123456789:AAHxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx")
    assert length == 47
    assert has_ws is False
    assert tail == "xxxx"


def test_metadata_flags_trailing_whitespace():
    length, has_ws, _ = describe_stored_value("123456789:AAHtoken ")
    assert has_ws is True
    assert length == 19


def test_metadata_flags_invisible_paste_artifacts():
    # Zero-Width-Space beim Kopieren aus einer Webseite — sieht aus wie sauber.
    _, has_ws, _ = describe_stored_value("123456789:AAHtoken\u200b")
    assert has_ws is True


def test_metadata_for_empty_value():
    assert describe_stored_value(None) == (0, False, "")
    assert describe_stored_value("") == (0, False, "")
