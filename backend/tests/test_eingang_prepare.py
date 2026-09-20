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


def test_zip_archives_are_unpacked(tmp_path):
    src = tmp_path / "neu"
    src.mkdir(parents=True)
    inner = _photo(tmp_path / "work" / "aus_zip.jpg")
    with zipfile.ZipFile(src / "export.zip", "w") as archive:
        archive.write(inner, arcname="Photos/aus_zip.jpg")

    eingang_prepare.unpack_archives(src)

    assert (src / "aus_zip.jpg").exists()
    assert not (src / "export.zip").exists()


def test_prepared_photo_is_downscaled_and_loses_metadata(tmp_path):
    src = tmp_path / "neu"
    out = tmp_path / "arbeit"
    big = src / "gross.jpg"
    big.parent.mkdir(parents=True)
    image = Image.new("RGB", (3000, 1000), color=(1, 2, 3))
    exif = Image.Exif()
    exif[274] = 3  # Orientation: um 180 Grad gedreht
    image.save(big, "JPEG", exif=exif)

    prepared = eingang_prepare.prepare_photo(big, out)

    with Image.open(prepared) as result:
        assert max(result.size) == eingang_prepare.MAX_DIMENSION
        assert result.getexif().get(274) is None


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
