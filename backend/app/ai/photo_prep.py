"""Foto-Vorverarbeitung fuer die KI-Analyse (Spec: KI-Anbindung).

Das Frontend verkleinert Uploads bereits vor dem Senden - das hier ist ein
Sicherheitsnetz fuer Fotos, die trotzdem zu gross ankommen (z. B. per API
direkt hochgeladen statt ueber das Frontend), nicht der Normalfall.
"""

import io
from pathlib import Path

_MAX_DIMENSION = 1600
_MAX_BYTES = int(1.5 * 1024 * 1024)
_JPEG_QUALITY = 85


def prepare_photo(path: Path, media_type: str | None) -> tuple[bytes, str]:
    """Liest ein Foto und verkleinert es bei Bedarf fuer den KI-Aufruf.

    Ueberschreitet die laengste Kante 1600 px oder die Datei 1,5 MB, wird mit
    Pillow auf maximal 1600 px verkleinert und als JPEG (q=85) neu
    komprimiert. Sonst kommen die Original-Bytes unveraendert zurueck.
    """
    from PIL import Image, ImageOps  # Import-Kosten: nur beim tatsaechlichen Aufruf laden

    file_size = path.stat().st_size
    with Image.open(path) as im:
        if max(im.size) <= _MAX_DIMENSION and file_size <= _MAX_BYTES:
            fallback_type = f"image/{(im.format or 'jpeg').lower()}"
            return path.read_bytes(), media_type or fallback_type

        # Handyfotos tragen die Drehung oft nur als EXIF-Tag, nicht in den
        # Pixeln — ohne diesen Schritt kommt ein Hochkant-Foto seitlich bei
        # der KI an, und die Analyse liest ein falsch liegendes Bild.
        im = ImageOps.exif_transpose(im) or im
        im.thumbnail((_MAX_DIMENSION, _MAX_DIMENSION))
        buffer = io.BytesIO()
        im.convert("RGB").save(buffer, "JPEG", quality=_JPEG_QUALITY)
        return buffer.getvalue(), "image/jpeg"
