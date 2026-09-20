"""Fotos aus dem Eingang fuer die Sichtung vorbereiten (laeuft lokal).

Packt ZIP-Exporte aus, ueberspringt Fotos, die ein frueherer Durchgang schon
importiert hat, dreht nach EXIF, verkleinert und wirft die Metadaten weg (in
Handyfotos steckt der Aufnahmeort). Dazu ein Gruppenvorschlag nach Aufnahmezeit
-- die endgueltige Zuordnung macht Claude beim Ansehen der Bilder.

    python -m app.tools.eingang_prepare prepare Eingang/neu Eingang/arbeit
    python -m app.tools.eingang_prepare finish       # nach dem Import

Spec: `docs/superpowers/specs/2026-09-20-eingang-workflow-design.md`.
"""

import argparse
import hashlib
import json
import re
import zipfile
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path

from PIL import Image, ImageOps

MAX_DIMENSION = 2000
JPEG_QUALITY = 85
GROUP_GAP = timedelta(minutes=2)
PHOTO_SUFFIXES = {".jpg", ".jpeg", ".png", ".webp", ".gif"}
ORIGINS_FILE = "herkunft.json"
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


def finish(
    source: Path,
    archive_dir: Path,
    index_path: Path,
    stamp: str,
    prepared_names: list[str] | None = None,
    work_dir: Path | None = None,
) -> int:
    """Durchgang abschliessen: Hashes merken, Originale wegraeumen. Erst hier --
    bricht der Import ab, soll der naechste Lauf dieselben Fotos wieder sehen.

    `prepared_names` sind die Fotos aus dem Manifest (die Namen aus
    `Eingang/arbeit`). Ohne die Liste wird alles abgeraeumt, was im Eingang
    liegt; mit ihr bleiben zurueckgestellte Artikel dort liegen, wo sie beim
    naechsten Durchgang wieder auftauchen."""
    photos = [p for p in sorted(source.rglob("*")) if p.suffix.lower() in PHOTO_SUFFIXES]
    if prepared_names is not None:
        origins = load_origins(work_dir or source)
        wanted = {origins[name] for name in prepared_names if name in origins}
        photos = [p for p in photos if str(p) in wanted]
    if not photos:
        return 0
    mark_processed(index_path, photos, stamp)
    archive_dir.mkdir(parents=True, exist_ok=True)
    for photo in photos:
        photo.rename(_free_path(archive_dir / photo.name))
    return len(photos)


def load_origins(work_dir: Path) -> dict[str, str]:
    """Zuordnung vorbereitetes Foto -> Originaldatei, von `prepare` geschrieben."""
    path = work_dir / ORIGINS_FILE
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def unpack_archives(source: Path) -> list[Path]:
    """Holt die Fotos aus ZIP-Exporten. Das Archiv bleibt liegen (nur umbenannt):
    Handy-Exporte enthalten auch HEIC, Videos und Belege, die hier nicht
    ausgepackt werden -- geloescht waeren sie unwiederbringlich weg."""
    unpacked = []
    for archive_path in sorted(source.glob("*.zip")):
        with zipfile.ZipFile(archive_path) as archive:
            for info in archive.infolist():
                if info.is_dir() or Path(info.filename).suffix.lower() not in PHOTO_SUFFIXES:
                    continue
                # Nur der Dateiname, nie der Pfad aus dem Archiv: sonst schreibt
                # ein praepariertes ZIP ausserhalb von source.
                target = _free_path(source / Path(info.filename).name)
                with archive.open(info) as src, open(target, "wb") as dst:
                    dst.write(src.read())
                unpacked.append(target)
        archive_path.rename(archive_path.with_suffix(".zip.verarbeitet"))
    return unpacked


def _free_path(target: Path) -> Path:
    """Naechster freier Name: gleichnamige Fotos aus verschiedenen Ordnern
    duerfen sich nicht gegenseitig ueberschreiben."""
    if not target.exists():
        return target
    for n in range(2, 1000):
        candidate = target.with_name(f"{target.stem}_{n}{target.suffix}")
        if not candidate.exists():
            return candidate
    raise RuntimeError(f"zu viele gleichnamige Dateien: {target}")


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
    target = _free_path(out_dir / f"{path.stem}.jpg")
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

    # Reste eines frueheren Durchgangs raus: sonst sichtet der naechste Lauf
    # Fotos mit, die laengst im Inventar stehen.
    out_dir.mkdir(parents=True, exist_ok=True)
    for stale in list(out_dir.glob("*.jpg")) + [out_dir / "gruppen.json", out_dir / ORIGINS_FILE]:
        stale.unlink(missing_ok=True)

    origins = {}
    prepared_names = []
    for photo in photos:
        target = prepare_photo(photo, out_dir)
        origins[target.name] = str(photo)
        prepared_names.append(target.name)

    groups = group_by_capture_time(prepared_names)
    payload = [{"gruppe": number, "fotos": group} for number, group in enumerate(groups, start=1)]
    (out_dir / "gruppen.json").write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
    (out_dir / ORIGINS_FILE).write_text(json.dumps(origins, ensure_ascii=False, indent=1), encoding="utf-8")
    return PrepareResult(prepared=len(photos), groups=len(groups), skipped=skipped)


def main() -> None:
    parser = argparse.ArgumentParser(description="Eingang-Fotos vorbereiten und nach dem Import wegraeumen")
    sub = parser.add_subparsers(dest="command", required=True)

    prepare_cmd = sub.add_parser("prepare", help="ZIPs auspacken, verkleinern, Gruppen vorschlagen")
    prepare_cmd.add_argument("source", type=Path, nargs="?", default=Path("Eingang/neu"))
    prepare_cmd.add_argument("out", type=Path, nargs="?", default=Path("Eingang/arbeit"))
    prepare_cmd.add_argument("--index", type=Path, default=Path("Eingang/.verarbeitet.json"))

    finish_cmd = sub.add_parser("finish", help="nach dem Import: Hashes merken, Originale wegraeumen")
    finish_cmd.add_argument("source", type=Path, nargs="?", default=Path("Eingang/neu"))
    finish_cmd.add_argument("archive", type=Path, nargs="?", default=None)
    finish_cmd.add_argument("--index", type=Path, default=Path("Eingang/.verarbeitet.json"))
    finish_cmd.add_argument("--work", type=Path, default=Path("Eingang/arbeit"))
    finish_cmd.add_argument(
        "--manifest",
        type=Path,
        help="Verzeichnis mit der importierten manifest.json; dann bleiben zurueckgestellte Fotos liegen",
    )

    args = parser.parse_args()

    if args.command == "prepare":
        result = prepare(args.source, args.out, args.index)
        print(f"{result.prepared} Fotos vorbereitet, {result.groups} Gruppen, {result.skipped} schon verarbeitet")
        return

    prepared_names = None
    if args.manifest:
        manifest = json.loads((args.manifest / "manifest.json").read_text(encoding="utf-8"))
        prepared_names = [photo for item in manifest.get("items", []) for photo in item.get("photos", [])]

    stamp = date.today().isoformat()
    archive = args.archive or Path("Eingang/verarbeitet") / stamp
    moved = finish(args.source, archive, args.index, stamp, prepared_names=prepared_names, work_dir=args.work)
    liegen = "" if prepared_names is None else " (zurueckgestellte Fotos bleiben im Eingang)"
    print(f"{moved} Fotos nach {archive} verschoben und als verarbeitet vermerkt{liegen}")


if __name__ == "__main__":
    main()
