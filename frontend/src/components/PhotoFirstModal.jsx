import { useEffect, useRef, useState } from "react";
import { useMutation, useQuery } from "@tanstack/react-query";
import { api } from "../api/client";

const PHOTO_ICON = "\u{1F4F8}";
const HINTS_MAX_LENGTH = 500;

function createLocalPhotoEntries(fileList) {
  return Array.from(fileList).map((file, index) => ({
    id: `${file.name}-${file.lastModified}-${index}-${Math.random().toString(36).slice(2, 7)}`,
    file,
    previewUrl: URL.createObjectURL(file),
  }));
}

function revokeLocalPhotoEntries(entries) {
  entries.forEach((entry) => URL.revokeObjectURL(entry.previewUrl));
}

function formatEuro(value) {
  return `${Math.round(value)}€`;
}

function draftToForm(draft) {
  return {
    set_name: draft.name || "",
    product_group: draft.product_group || "",
    condition: draft.condition || "NEW_SEALED",
    notes: draft.description || "",
    search_query: draft.search_query || "",
    buy_price: "",
    quantity: "1",
  };
}

export default function PhotoFirstModal({ onClose, onCreated }) {
  const { data: productGroups = [] } = useQuery({ queryKey: ["productGroups"], queryFn: api.listProductGroups });

  const [itemId, setItemId] = useState(null);
  const [photoEntries, setPhotoEntries] = useState([]);
  const [hints, setHints] = useState("");
  const [dragOver, setDragOver] = useState(false);
  const [analyzing, setAnalyzing] = useState(false);
  const [analyzeError, setAnalyzeError] = useState(null);
  const [review, setReview] = useState(null);
  const [confirmError, setConfirmError] = useState(null);
  const [cancelling, setCancelling] = useState(false);

  const photoEntriesRef = useRef([]);
  const uploadedIdsRef = useRef(new Set());

  useEffect(() => {
    photoEntriesRef.current = photoEntries;
  }, [photoEntries]);

  // Objekt-URLs hängen an diesem Modal – egal ob sauber über Abbrechen/
  // Übernehmen geschlossen oder einfach ausgehängt, sie müssen mit weg.
  useEffect(() => () => revokeLocalPhotoEntries(photoEntriesRef.current), []);

  function appendPhotos(fileList) {
    if (fileList?.length) setPhotoEntries((prev) => [...prev, ...createLocalPhotoEntries(fileList)]);
  }

  function removePhoto(photoId) {
    setPhotoEntries((prev) => {
      const removed = prev.find((entry) => entry.id === photoId);
      if (removed) URL.revokeObjectURL(removed.previewUrl);
      return prev.filter((entry) => entry.id !== photoId);
    });
  }

  async function handleAnalyze() {
    setAnalyzeError(null);
    setAnalyzing(true);
    try {
      let id = itemId;
      if (id == null) {
        const draftItem = await api.createDraft();
        id = draftItem.id;
        setItemId(id);
      }
      // Nur Fotos hochladen, die dieser Entwurf noch nicht hat – ein "Erneut
      // versuchen" nach einem 503 von /analyze soll nicht dieselben Dateien
      // ein zweites Mal an den Artikel hängen.
      const pending = photoEntries.filter((entry) => !uploadedIdsRef.current.has(entry.id));
      if (pending.length > 0) {
        await api.uploadInventoryPhotos(id, pending.map((entry) => entry.file));
        pending.forEach((entry) => uploadedIdsRef.current.add(entry.id));
      }
      const result = await api.analyzeItem(id, hints.trim());
      setReview({
        form: draftToForm(result.draft),
        priceMin: result.draft.price_min,
        priceMax: result.draft.price_max,
        ebay: result.ebay,
        ebayError: result.ebay_error,
      });
    } catch (err) {
      setAnalyzeError(err.message);
    } finally {
      setAnalyzing(false);
    }
  }

  async function handleCancel() {
    if (itemId == null) {
      onClose();
      return;
    }
    setCancelling(true);
    try {
      await api.deleteInventory(itemId);
    } catch {
      // Schließen darf am Aufräumen nicht scheitern – die Portfolio-Summary
      // blendet DRAFT-Artikel ohnehin aus, ein liegen gebliebener Entwurf
      // verzerrt also keine Zahl, er ist nur unschön.
    } finally {
      setCancelling(false);
      onClose();
    }
  }

  const confirmMutation = useMutation({
    mutationFn: async () => {
      const { form } = review;
      await api.updateInventory(itemId, {
        set_name: form.set_name.trim(),
        // Leere Warengruppe nicht als null schicken (Backend lehnt das ab) –
        // weglassen lässt den vorhandenen Wert in Ruhe, wie beim normalen Edit-Formular.
        product_group: form.product_group.trim() || undefined,
        condition: form.condition,
        notes: form.notes.trim() || null,
        search_query: form.search_query.trim() || null,
        buy_price: form.buy_price === "" ? null : Number(form.buy_price),
        quantity: parseInt(form.quantity || "1", 10),
      });
      return api.confirmItem(itemId);
    },
    onSuccess: () => onCreated(),
    onError: (err) => setConfirmError(err.message),
  });

  function updateForm(patch) {
    setReview((prev) => ({ ...prev, form: { ...prev.form, ...patch } }));
  }

  return (
    <div className="fixed inset-0 z-50 flex items-start justify-center md:items-center bg-black/60 backdrop-blur-sm overflow-y-auto p-4">
      <div className="bg-bg-card border border-border rounded-xl p-6 w-full max-w-2xl my-6">
        <h2 className="text-text-primary text-lg font-bold mb-4">
          {review ? "Entwurf prüfen" : "Per Foto anlegen"}
        </h2>

        {!review ? (
          <div className="space-y-4">
            <label
              onDragOver={(e) => {
                e.preventDefault();
                setDragOver(true);
              }}
              onDragLeave={() => setDragOver(false)}
              onDrop={(e) => {
                e.preventDefault();
                setDragOver(false);
                if (e.dataTransfer.files?.length) appendPhotos(e.dataTransfer.files);
              }}
              className={`block cursor-pointer rounded-lg border-2 border-dashed px-4 py-6 text-center text-sm transition-colors ${
                dragOver ? "border-lego-yellow bg-lego-yellow/5 text-lego-yellow" : "border-border text-text-muted hover:border-lego-yellow/50"
              }`}
            >
              {PHOTO_ICON} Fotos hierher ziehen oder klicken zum Auswählen
              <input
                type="file"
                accept="image/*"
                multiple
                className="hidden"
                onChange={(e) => {
                  appendPhotos(e.target.files);
                  e.target.value = "";
                }}
              />
            </label>

            {photoEntries.length > 0 && (
              <div className="grid grid-cols-3 gap-2">
                {photoEntries.map((entry) => (
                  <div key={entry.id} className="relative rounded-lg overflow-hidden border border-border bg-bg-primary">
                    <img src={entry.previewUrl} alt={entry.file.name} className="w-full aspect-square object-cover" />
                    <button
                      type="button"
                      onClick={() => removePhoto(entry.id)}
                      className="absolute top-1 right-1 bg-black/70 text-white text-xs px-1.5 py-0.5 rounded"
                    >
                      X
                    </button>
                  </div>
                ))}
              </div>
            )}

            <div>
              <label className="block text-text-muted text-xs mb-1">Hinweise (optional)</label>
              <textarea
                value={hints}
                onChange={(e) => setHints(e.target.value)}
                maxLength={HINTS_MAX_LENGTH}
                rows={3}
                placeholder="z. B. Zustand, Besonderheiten, was fehlt ..."
                className="w-full bg-bg-primary border border-border rounded-lg px-3 py-2 text-text-primary text-sm resize-none"
              />
              <p className="text-text-muted text-xs mt-1 text-right">{hints.length}/{HINTS_MAX_LENGTH}</p>
            </div>

            {analyzeError && (
              <div className="rounded-lg border border-no-go/40 bg-no-go/5 px-3 py-2 text-xs text-no-go">
                {analyzeError}
              </div>
            )}

            <div className="flex gap-3">
              <button
                type="button"
                onClick={handleCancel}
                disabled={cancelling || analyzing}
                className="flex-1 bg-bg-hover text-text-secondary py-2 rounded-lg disabled:opacity-50"
              >
                Abbrechen
              </button>
              <button
                type="button"
                onClick={handleAnalyze}
                disabled={analyzing || photoEntries.length === 0}
                className="flex-1 bg-lego-yellow text-black font-bold py-2 rounded-lg disabled:opacity-50"
              >
                {analyzing ? "Claude schaut sich die Fotos an…" : analyzeError ? "Erneut versuchen" : "Analysieren"}
              </button>
            </div>
          </div>
        ) : (
          <div className="space-y-4">
            <div className="rounded-lg bg-lego-yellow/10 border border-lego-yellow/30 px-3 py-2 text-xs text-text-primary">
              KI-Schätzung {formatEuro(review.priceMin)}–{formatEuro(review.priceMax)}
              {review.ebay && review.ebay.source === "EBAY_ACTIVE" && (
                <>
                  {" · eBay-Angebote "}
                  {formatEuro(review.ebay.median)} (Median aus {review.ebay.sold_count ?? 0} aktiven Angeboten, keine
                  Verkaufspreise)
                </>
              )}
              {review.ebay && review.ebay.source !== "EBAY_ACTIVE" && (
                <>
                  {" · eBay-Median "}
                  {formatEuro(review.ebay.median)} ({review.ebay.sold_count ?? 0} Verkäufe)
                  {review.ebay.is_reliable === false && " (wenige Daten)"}
                </>
              )}
            </div>
            {review.ebayError && (
              <p className="text-xs text-check">{review.ebayError} — die KI-Schätzung oben bleibt nutzbar.</p>
            )}

            <div className="space-y-3">
              <div>
                <label className="block text-text-muted text-xs mb-1">Name</label>
                <input
                  type="text"
                  value={review.form.set_name}
                  onChange={(e) => updateForm({ set_name: e.target.value })}
                  className="w-full bg-bg-primary border border-border rounded-lg px-3 py-2 text-text-primary text-sm"
                />
              </div>
              <div>
                <label className="block text-text-muted text-xs mb-1">Warengruppe</label>
                <input
                  type="text"
                  list="product-groups-photo-first-list"
                  value={review.form.product_group}
                  onChange={(e) => updateForm({ product_group: e.target.value })}
                  className="w-full bg-bg-primary border border-border rounded-lg px-3 py-2 text-text-primary text-sm"
                />
                <datalist id="product-groups-photo-first-list">
                  {productGroups.map((group) => (
                    <option key={group} value={group} />
                  ))}
                </datalist>
              </div>
              <div>
                <label className="block text-text-muted text-xs mb-1">Zustand</label>
                <select
                  value={review.form.condition}
                  onChange={(e) => updateForm({ condition: e.target.value })}
                  className="w-full bg-bg-primary border border-border rounded-lg px-3 py-2 text-text-primary text-sm"
                >
                  <option value="NEW_SEALED">Neu & Versiegelt</option>
                  <option value="NEW_OPEN_BOX">Neu & Geöffnet</option>
                  <option value="USED_COMPLETE">Gebraucht (komplett)</option>
                  <option value="USED_INCOMPLETE">Gebraucht (unvollständig)</option>
                </select>
              </div>
              <div>
                <label className="block text-text-muted text-xs mb-1">Beschreibung</label>
                <textarea
                  value={review.form.notes}
                  onChange={(e) => updateForm({ notes: e.target.value })}
                  rows={3}
                  className="w-full bg-bg-primary border border-border rounded-lg px-3 py-2 text-text-primary text-sm resize-none"
                />
              </div>
              <div>
                <label className="block text-text-muted text-xs mb-1">Such-Query für Marktpreis-Recherche</label>
                <input
                  type="text"
                  value={review.form.search_query}
                  onChange={(e) => updateForm({ search_query: e.target.value })}
                  className="w-full bg-bg-primary border border-border rounded-lg px-3 py-2 text-text-primary text-sm"
                />
              </div>
              <div className="grid grid-cols-2 gap-3">
                <div>
                  <label className="block text-text-muted text-xs mb-1">Kaufpreis (€)</label>
                  <input
                    type="number"
                    step="0.01"
                    placeholder="unbekannt"
                    value={review.form.buy_price}
                    onChange={(e) => updateForm({ buy_price: e.target.value })}
                    className="w-full bg-bg-primary border border-border rounded-lg px-3 py-2 text-text-primary text-sm font-[family-name:var(--font-mono)]"
                  />
                </div>
                <div>
                  <label className="block text-text-muted text-xs mb-1">Anzahl</label>
                  <input
                    type="number"
                    min="1"
                    value={review.form.quantity}
                    onChange={(e) => updateForm({ quantity: e.target.value })}
                    className="w-full bg-bg-primary border border-border rounded-lg px-3 py-2 text-text-primary text-sm font-[family-name:var(--font-mono)]"
                  />
                </div>
              </div>
            </div>

            {confirmError && (
              <div className="rounded-lg border border-no-go/40 bg-no-go/5 px-3 py-2 text-xs text-no-go">
                {confirmError}
              </div>
            )}

            <div className="flex gap-3">
              <button
                type="button"
                onClick={handleCancel}
                disabled={cancelling || confirmMutation.isPending}
                className="flex-1 bg-bg-hover text-text-secondary py-2 rounded-lg disabled:opacity-50"
              >
                Abbrechen
              </button>
              <button
                type="button"
                onClick={() => confirmMutation.mutate()}
                disabled={confirmMutation.isPending || !review.form.set_name.trim()}
                className="flex-1 bg-lego-yellow text-black font-bold py-2 rounded-lg disabled:opacity-50"
              >
                {confirmMutation.isPending ? "Übernehmen ..." : "Übernehmen"}
              </button>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
