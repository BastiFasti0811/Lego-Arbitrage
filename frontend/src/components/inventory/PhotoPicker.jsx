import { api } from "../../api/client";
import { PHOTO_ICON } from "./inventoryFormat";

export default function PhotoPicker({
  itemId,
  existingPhotos = [],
  removedPhotoIds = [],
  onToggleExistingPhoto,
  onMakePrimaryExistingPhoto,
  localPhotos = [],
  onSelectFiles,
  onRemoveLocalPhoto,
  externalImageUrl = "",
  onExternalImageUrlChange,
  isUpdatingPrimary = false,
}) {
  const visibleExistingPhotos = existingPhotos.filter((photo) => !removedPhotoIds.includes(photo.id));
  const hasAnyPhotos = visibleExistingPhotos.length > 0 || localPhotos.length > 0;

  return (
    <div className="space-y-3">
      <div className="flex items-center justify-between">
        <label className="block text-text-muted text-xs">Fotos</label>
        <label className="cursor-pointer text-xs text-lego-yellow hover:text-lego-yellow/80 transition-colors">
          + Bilder wählen
          <input
            type="file"
            accept="image/*"
            multiple
            className="hidden"
            onChange={(e) => {
              onSelectFiles(e.target.files);
              e.target.value = "";
            }}
          />
        </label>
      </div>
      {hasAnyPhotos ? (
        <div className="grid grid-cols-3 gap-2">
          {visibleExistingPhotos.map((photo, index) => (
            <div key={photo.id} className="relative rounded-lg overflow-hidden border border-border bg-bg-primary">
              <img src={api.inventoryPhotoUrl(itemId, photo.id)} alt={photo.original_filename || "Inventar-Foto"} className="w-full aspect-square object-cover" />
              <div className="absolute left-1 bottom-1 right-1 flex items-center justify-between gap-1">
                {index === 0 ? (
                  <span className="bg-lego-yellow text-black text-[11px] font-bold px-1.5 py-0.5 rounded">
                    Titelbild
                  </span>
                ) : (
                  <button
                    type="button"
                    onClick={() => onMakePrimaryExistingPhoto(photo.id)}
                    disabled={isUpdatingPrimary}
                    className="bg-black/70 text-white text-[11px] px-1.5 py-0.5 rounded disabled:opacity-50"
                  >
                    Titelbild
                  </button>
                )}
              </div>
              <button type="button" onClick={() => onToggleExistingPhoto(photo.id)} className="absolute top-1 right-1 bg-black/70 text-white text-xs px-1.5 py-0.5 rounded">
                Entfernen
              </button>
            </div>
          ))}
          {localPhotos.map((photo) => (
            <div key={photo.id} className="relative rounded-lg overflow-hidden border border-lego-blue/30 bg-bg-primary">
              <img src={photo.previewUrl} alt={photo.file.name} className="w-full aspect-square object-cover" />
              <span className="absolute left-1 bottom-1 bg-lego-blue/80 text-white text-[11px] px-1.5 py-0.5 rounded">
                Neu
              </span>
              <button type="button" onClick={() => onRemoveLocalPhoto(photo.id)} className="absolute top-1 right-1 bg-black/70 text-white text-xs px-1.5 py-0.5 rounded">
                X
              </button>
            </div>
          ))}
        </div>
      ) : (
        <div className="rounded-lg border border-dashed border-border px-3 py-4 text-center text-text-muted text-sm">
          {PHOTO_ICON} Noch keine Fotos hinterlegt
        </div>
      )}
      <div className="space-y-2">
        <label className="block text-text-muted text-xs">Externer Foto-Link</label>
        <input
          type="url"
          value={externalImageUrl}
          onChange={(e) => onExternalImageUrlChange(e.target.value)}
          placeholder="Optional, z. B. Google Photos oder Cloud-Link"
          className="w-full bg-bg-primary border border-border rounded-lg px-3 py-2 text-text-primary text-sm"
        />
        <p className="text-text-muted text-xs">
          Praktisch, wenn Bilder nicht direkt hochgeladen werden sollen.
        </p>
      </div>
      <p className="text-text-muted text-xs">
        Mehrere Bilder möglich. Gespeicherte Fotos können als Titelbild nach vorn gezogen werden.
      </p>
    </div>
  );
}
