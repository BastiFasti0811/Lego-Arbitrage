"""Posten teilen kopiert Foto-DATEIEN — sonst zeigt der neue Artikel ins Leere,
sobald der alte geloescht wird (Grill-Entscheid Q17)."""

from types import SimpleNamespace

from app.api.routes.inventory import copy_item_photos


def _photo(filename, sort_order=0):
    return SimpleNamespace(
        filename=filename,
        original_filename=f"orig-{filename}",
        content_type="image/jpeg",
        sort_order=sort_order,
    )


def test_files_are_copied_with_fresh_names(tmp_path):
    source = tmp_path / "1"
    target = tmp_path / "2"
    source.mkdir()
    (source / "a.jpg").write_bytes(b"foto-a")
    (source / "b.jpg").write_bytes(b"foto-b")

    result = copy_item_photos([_photo("a.jpg", 0), _photo("b.jpg", 1)], source, target)

    assert len(result) == 2
    assert result[0]["filename"] != "a.jpg"  # frischer uuid-Name, keine Kollision
    assert (target / result[0]["filename"]).read_bytes() == b"foto-a"
    assert result[0]["sort_order"] == 0
    assert result[1]["sort_order"] == 1
    assert result[0]["original_filename"] == "orig-a.jpg"


def test_missing_source_file_is_skipped(tmp_path):
    source = tmp_path / "1"
    target = tmp_path / "2"
    source.mkdir()

    result = copy_item_photos([_photo("fehlt.jpg")], source, target)

    assert result == []
