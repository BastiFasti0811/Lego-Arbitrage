"""Vorbereitung eines Eingang-Durchgangs (laeuft lokal, nicht auf dem Server).

Spec: `docs/superpowers/specs/2026-09-20-eingang-workflow-design.md`.
"""

import json
import zipfile

from PIL import Image

from app.tools import eingang_prepare


def _photo(path, size=(60, 40), color=(10, 20, 30)):
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", size, color=color).save(path, "JPEG")
    return path


def test_groups_photos_taken_within_two_minutes():
    names = [
        "PXL_20260920_101500000.jpg",
        "PXL_20260920_101530000.jpg",
        "PXL_20260920_104500000.jpg",  # 30 min spaeter -> neue Gruppe
        "PXL_20260920_104520000.jpg",
    ]

    groups = eingang_prepare.group_by_capture_time(names)

    assert [len(g) for g in groups] == [2, 2]


def test_photos_without_timestamp_form_their_own_group():
    groups = eingang_prepare.group_by_capture_time(["IMG_0001.jpg", "PXL_20260920_101500000.jpg"])

    assert [g[0] for g in groups] == ["PXL_20260920_101500000.jpg", "IMG_0001.jpg"]


def test_already_processed_photos_are_skipped(tmp_path):
    src = tmp_path / "neu"
    known = _photo(src / "alt.jpg")
    _photo(src / "neu.jpg", color=(200, 100, 50))
    index = {eingang_prepare.photo_hash(known): "2026-09-19"}

    fresh = eingang_prepare.new_photos(src, index)

    assert [p.name for p in fresh] == ["neu.jpg"]


def test_zip_archives_are_unpacked_and_kept(tmp_path):
    # Das Archiv wird nicht geloescht: Handy-Exporte enthalten auch HEIC,
    # Videos und PDFs, die hier nicht ausgepackt werden -- sie waeren sonst weg.
    src = tmp_path / "neu"
    src.mkdir(parents=True)
    inner = _photo(tmp_path / "work" / "aus_zip.jpg")
    with zipfile.ZipFile(src / "export.zip", "w") as archive:
        archive.write(inner, arcname="DCIM/aus_zip.jpg")
        archive.writestr("DCIM/IMG_4711.HEIC", b"heic-bytes")
        archive.writestr("kaufbeleg.pdf", b"pdf-bytes")

    eingang_prepare.unpack_archives(src)

    assert (src / "aus_zip.jpg").exists()
    assert not (src / "export.zip").exists()
    done = src / "export.zip.verarbeitet"
    assert done.exists()
    with zipfile.ZipFile(done) as archive:
        assert "DCIM/IMG_4711.HEIC" in archive.namelist()
        assert "kaufbeleg.pdf" in archive.namelist()


def test_unpacking_twice_does_not_duplicate_photos(tmp_path):
    src = tmp_path / "neu"
    src.mkdir(parents=True)
    inner = _photo(tmp_path / "work" / "foto.jpg")
    with zipfile.ZipFile(src / "export.zip", "w") as archive:
        archive.write(inner, arcname="foto.jpg")

    eingang_prepare.unpack_archives(src)
    eingang_prepare.unpack_archives(src)

    assert [p.name for p in sorted(src.glob("*.jpg"))] == ["foto.jpg"]


def test_photos_with_the_same_name_in_different_folders_both_survive(tmp_path):
    src = tmp_path / "neu"
    out = tmp_path / "arbeit"
    _photo(src / "a" / "IMG_1.jpg")
    _photo(src / "b" / "IMG_1.jpg", color=(200, 10, 10))

    result = eingang_prepare.prepare(src, out, index_path=tmp_path / ".verarbeitet.json")

    assert result.prepared == 2
    assert len(list(out.glob("*.jpg"))) == 2


def test_finish_records_hashes_and_moves_the_originals(tmp_path):
    src = tmp_path / "neu"
    archive = tmp_path / "verarbeitet" / "2026-09-20"
    index_path = tmp_path / ".verarbeitet.json"
    photo = _photo(src / "a.jpg")
    digest = eingang_prepare.photo_hash(photo)

    moved = eingang_prepare.finish(src, archive, index_path, stamp="2026-09-20")

    assert moved == 1
    assert not photo.exists()
    assert (archive / "a.jpg").exists()
    assert digest in eingang_prepare.load_index(index_path)


def test_finished_photos_are_skipped_on_the_next_run(tmp_path):
    src = tmp_path / "neu"
    index_path = tmp_path / ".verarbeitet.json"
    _photo(src / "a.jpg")
    eingang_prepare.finish(src, tmp_path / "verarbeitet", index_path, stamp="2026-09-20")
    _photo(src / "a.jpg")  # dieselbe Datei landet noch einmal im Eingang

    result = eingang_prepare.prepare(src, tmp_path / "arbeit", index_path=index_path)

    assert result.prepared == 0
    assert result.skipped == 1


def test_prepared_photo_is_downscaled_and_loses_metadata(tmp_path):
    # Handyfotos tragen Aufnahmeort, Geraet und Zeitpunkt mit sich; nichts davon
    # gehoert in ein Bild, das spaeter in einer Anzeige landen kann.
    src = tmp_path / "neu"
    out = tmp_path / "arbeit"
    big = src / "gross.jpg"
    big.parent.mkdir(parents=True)
    image = Image.new("RGB", (3000, 1000), color=(1, 2, 3))
    exif = Image.Exif()
    exif[274] = 3  # Orientation
    exif[271] = "Google"  # Make
    exif[306] = "2026:09:20 10:15:00"  # DateTime
    exif[34853] = {1: "N", 2: (50.0, 0.0, 0.0)}  # GPS-IFD
    image.save(big, "JPEG", exif=exif)

    prepared = eingang_prepare.prepare_photo(big, out)

    with Image.open(prepared) as result:
        assert max(result.size) == eingang_prepare.MAX_DIMENSION
        assert dict(result.getexif()) == {}
        assert "exif" not in result.info


def test_prepare_writes_group_proposal(tmp_path):
    src = tmp_path / "neu"
    out = tmp_path / "arbeit"
    _photo(src / "PXL_20260920_101500000.jpg")
    _photo(src / "PXL_20260920_101530000.jpg", color=(90, 90, 90))

    result = eingang_prepare.prepare(src, out, index_path=tmp_path / ".verarbeitet.json")

    groups = json.loads((out / "gruppen.json").read_text(encoding="utf-8"))
    assert result.prepared == 2
    assert [len(g["fotos"]) for g in groups] == [2]
    assert (out / "PXL_20260920_101500000.jpg").exists()


def test_prepare_marks_nothing_as_processed_before_the_import(tmp_path):
    # Der Index wird erst nach dem Import fortgeschrieben (mark_processed) --
    # sonst waere ein abgebrochener Durchgang nicht wiederholbar.
    src = tmp_path / "neu"
    index_path = tmp_path / ".verarbeitet.json"
    _photo(src / "a.jpg")

    eingang_prepare.prepare(src, tmp_path / "arbeit", index_path=index_path)

    assert not index_path.exists()
