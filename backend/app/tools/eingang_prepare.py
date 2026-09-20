"""Fotos aus dem Eingang fuer die Sichtung vorbereiten (laeuft lokal).

Packt ZIP-Exporte aus, ueberspringt Fotos, die ein frueherer Durchgang schon
importiert hat, dreht nach EXIF, verkleinert und wirft die Metadaten weg (in
Handyfotos steckt der Aufnahmeort). Dazu ein Gruppenvorschlag nach Aufnahmezeit
-- die endgueltige Zuordnung macht Claude beim Ansehen der Bilder.

    python -m app.tools.eingang_prepare Eingang/neu Eingang/arbeit

Spec: `docs/superpowers/specs/2026-09-20-eingang-workflow-design.md`.
"""

import argparse
import hashlib
import json
import re
import zipfile
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

from PIL import Image, ImageOps

MAX_DIMENSION = 2000
JPEG_QUALITY = 85
GROUP_GAP = timedelta(minutes=2)
PHOTO_SUFFIXES = {".jpg", ".jpeg", ".png", ".webp"}
_TIMESTAMP = re.compile(r"(\d{8})_(\d{6})")


@dataclass
class PrepareResult:
    prepared: int
    groups: int
    skipped: int


def photo_hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_index(index_path: Path) -> dict[str, str]:
    if not index_path.exists():
        return {}
    return json.loads(index_path.read_text(encoding="utf-8"))


def mark_processed(index_path: Path, paths: list[Path], stamp: str) -> None:
    """Nach dem Import aufrufen: erst dann gilt ein Foto als erledigt."""
    index = load_index(index_path)
    index.update({photo_hash(path): stamp for path in paths})
    index_path.write_text(json.dumps(index, indent=1), encoding="utf-8")


def unpack_archives(source: Path) -> list[Path]:
    unpacked = []
    for archive_path in sorted(source.glob("*.zip")):
        with zipfile.ZipFile(archive_path) as archive:
            for info in archive.infolist():
                if info.is_dir() or Path(info.filename).suffix.lower() not in PHOTO_SUFFIXES:
                    continue
                target = source / Path(info.filename).name
                if target.exists():
                    target = source / f"{target.stem}_{info.CRC:08x}{target.suffix}"
                with archive.open(info) as src, open(target, "wb") as dst:
                    dst.write(src.read())
                unpacked.append(target)
        archive_path.unlink()
    return unpacked


def new_photos(source: Path, index: dict[str, str]) -> list[Path]:
    photos = [p for p in sorted(source.rglob("*")) if p.suffix.lower() in PHOTO_SUFFIXES]
    return [p for p in photos if photo_hash(p) not in index]


def _captured_at(name: str) -> datetime | None:
    match = _TIMESTAMP.search(name)
    if not match:
        return None
    try:
        return datetime.strptime(match.group(1) + match.group(2), "%Y%m%d%H%M%S")
    except ValueError:
        return None


def group_by_capture_time(names: list[str], gap: timedelta = GROUP_GAP) -> list[list[str]]:
    """Serien kurz hintereinander zeigen meist denselben Artikel. Fotos ohne
    Zeitstempel im Namen bilden eine eigene Gruppe am Ende."""
    stamped = sorted(((_captured_at(n), n) for n in names if _captured_at(n)), key=lambda x: (x[0], x[1]))
    undated = sorted(n for n in names if not _captured_at(n))

    groups: list[list[str]] = []
    previous: datetime | None = None
    for captured, name in stamped:
        if previous is None or captured - previous > gap:
            groups.append([])
        groups[-1].append(name)
        previous = captured
    if undated:
        groups.append(undated)
    return groups


def prepare_photo(path: Path, out_dir: Path) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    target = out_dir / f"{path.stem}.jpg"
    with Image.open(path) as image:
        rotated = ImageOps.exif_transpose(image)
        rotated.thumbnail((MAX_DIMENSION, MAX_DIMENSION))
        rotated.convert("RGB").save(target, "JPEG", quality=JPEG_QUALITY)
    return target


def prepare(source: Path, out_dir: Path, index_path: Path) -> PrepareResult:
    unpack_archives(source)
    index = load_index(index_path)
    photos = new_photos(source, index)
    skipped = len([p for p in source.rglob("*") if p.suffix.lower() in PHOTO_SUFFIXES]) - len(photos)

    for photo in photos:
        prepare_photo(photo, out_dir)

    groups = group_by_capture_time([p.name for p in photos])
    payload = [
        {"gruppe": number, "fotos": [Path(name).with_suffix(".jpg").name for name in group]}
        for number, group in enumerate(groups, start=1)
    ]
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "gruppen.json").write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
    return PrepareResult(prepared=len(photos), groups=len(groups), skipped=skipped)


def main() -> None:
    parser = argparse.ArgumentParser(description="Eingang-Fotos fuer die Sichtung vorbereiten")
    parser.add_argument("source", type=Path, nargs="?", default=Path("Eingang/neu"))
    parser.add_argument("out", type=Path, nargs="?", default=Path("Eingang/arbeit"))
    parser.add_argument("--index", type=Path, default=Path("Eingang/.verarbeitet.json"))
    args = parser.parse_args()

    result = prepare(args.source, args.out, args.index)
    print(f"{result.prepared} Fotos vorbereitet, {result.groups} Gruppen, {result.skipped} schon verarbeitet")


if __name__ == "__main__":
    main()
