from app.notifications.telegram_bot import _sanitize_markdown


def test_sanitize_strips_markdown_control_characters():
    # Review-Finding F4: rohe detail-Strings ("{'total_sets': 31}") mit
    # ungerader Underscore-Zahl ließen Telegram mit 400 ablehnen — und weil
    # der Throttle nur nach Erfolg brennt, dauerhaft.
    dirty = "{'total_sets': 31, 'a_b': [1], '*x*': `y`}"
    clean = _sanitize_markdown(dirty)
    for ch in ("_", "*", "[", "]", "`"):
        assert ch not in clean
    assert "total_sets" not in clean  # Underscore ersetzt, Inhalt bleibt lesbar
    assert "total" in clean and "31" in clean
