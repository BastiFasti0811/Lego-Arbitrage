import PhotoPicker from "./PhotoPicker";
import { EURO, SELL_PLATFORM_QUICK_PICKS } from "./inventoryFormat";

// Dialoge zu einem Posten: Verkauft markieren und Bearbeiten (mit Fotos).
// Zustand und Mutationen haelt die Seite (pages/Inventar.jsx).

export function InventorySellDialog({
  sellModal,
  setSellModal,
  sellPrice,
  setSellPrice,
  sellDate,
  setSellDate,
  sellPlatform,
  setSellPlatform,
  handleSell,
  sellMutation,
}) {
  return (
    <div className="fixed inset-0 z-50 flex items-start justify-center md:items-center bg-black/60 backdrop-blur-sm overflow-y-auto p-4">
      <div className="bg-bg-card border border-border rounded-xl p-6 w-full max-w-md my-6">
        <h2 className="text-text-primary text-lg font-bold mb-4">Verkauft markieren</h2>
        <div className="text-text-muted text-sm mb-4">
          <span className="text-lego-yellow font-[family-name:var(--font-mono)]">{sellModal.set_number ?? sellModal.product_group}</span> - {sellModal.set_name}
        </div>
        <div className="space-y-3">
          <div>
            <label className="block text-text-muted text-xs mb-1">Verkaufspreis ({EURO})</label>
            <input type="number" step="0.01" value={sellPrice} onChange={(e) => setSellPrice(e.target.value)} autoFocus className="w-full bg-bg-primary border border-border rounded-lg px-3 py-2 text-text-primary font-[family-name:var(--font-mono)]" />
          </div>
          <div>
            <label className="block text-text-muted text-xs mb-1">Verkaufsdatum</label>
            <input type="date" value={sellDate} onChange={(e) => setSellDate(e.target.value)} className="w-full bg-bg-primary border border-border rounded-lg px-3 py-2 text-text-primary text-sm" />
          </div>
          <div>
            <label className="block text-text-muted text-xs mb-1">Plattform</label>
            {sellModal.listings?.length > 0 && (
              <div className="flex gap-2 mb-2">
                {SELL_PLATFORM_QUICK_PICKS.map((p) => (
                  <button
                    key={p.value}
                    type="button"
                    onClick={() => setSellPlatform(p.label)}
                    className="text-xs px-2 py-1 rounded bg-bg-hover text-text-primary border border-border"
                  >
                    {p.label}
                  </button>
                ))}
              </div>
            )}
            <input type="text" value={sellPlatform} onChange={(e) => setSellPlatform(e.target.value)} placeholder="z.B. eBay" className="w-full bg-bg-primary border border-border rounded-lg px-3 py-2 text-text-primary text-sm" />
          </div>
        </div>
        <div className="flex gap-3 mt-6">
          <button onClick={() => setSellModal(null)} className="flex-1 bg-bg-hover text-text-secondary py-2 rounded-lg">Abbrechen</button>
          <button onClick={handleSell} disabled={!sellPrice || sellMutation.isPending} className="flex-1 bg-go-star text-black font-bold py-2 rounded-lg disabled:opacity-50">
            {sellMutation.isPending ? "..." : "Verkauft"}
          </button>
        </div>
      </div>
    </div>
  );
}

export default function InventoryDetail({
  editModal,
  editForm,
  setEditForm,
  handleEdit,
  productGroups,
  platforms,
  editRemovedPhotoIds,
  setEditRemovedPhotoIds,
  makePrimaryPhotoMutation,
  editPhotoEntries,
  appendEditPhotos,
  removeLocalPhoto,
  setEditPhotoEntries,
  closeEditModal,
  editMutation,
}) {
  return (
    <div className="fixed inset-0 z-50 flex items-start justify-center md:items-center bg-black/60 backdrop-blur-sm overflow-y-auto p-4">
      <div className="bg-bg-card border border-border rounded-xl p-6 w-full max-w-2xl my-6">
        <h2 className="text-text-primary text-lg font-bold mb-2">Set bearbeiten</h2>
        <p className="text-lego-yellow font-[family-name:var(--font-mono)] text-sm mb-4">{editModal.set_number ?? editModal.product_group}</p>
        <form onSubmit={handleEdit} className="space-y-4">
          <div className="grid md:grid-cols-2 gap-4">
            <div className="space-y-3">
              <input type="text" value={editForm.set_name} onChange={(e) => setEditForm({ ...editForm, set_name: e.target.value })} required className="w-full bg-bg-primary border border-border rounded-lg px-3 py-2 text-text-primary text-sm" />
              <input type="text" value={editForm.theme} onChange={(e) => setEditForm({ ...editForm, theme: e.target.value })} placeholder="Theme" className="w-full bg-bg-primary border border-border rounded-lg px-3 py-2 text-text-primary text-sm" />
              {editModal.item_type === "GENERIC" && (
                <>
                  <input type="text" list="product-groups-edit-list" value={editForm.product_group} onChange={(e) => setEditForm({ ...editForm, product_group: e.target.value })} placeholder="Warengruppe" className="w-full bg-bg-primary border border-border rounded-lg px-3 py-2 text-text-primary text-sm" />
                  <datalist id="product-groups-edit-list">{productGroups.map((group) => <option key={group} value={group} />)}</datalist>
                </>
              )}
              <input type="text" value={editForm.search_query} onChange={(e) => setEditForm({ ...editForm, search_query: e.target.value })} placeholder="Such-Query fuer Marktpreis-Recherche" className="w-full bg-bg-primary border border-border rounded-lg px-3 py-2 text-text-primary text-sm" />
              <div className="grid grid-cols-2 gap-3">
                <input
                  type="number"
                  step="0.01"
                  value={editForm.buy_price}
                  onChange={(e) => setEditForm({ ...editForm, buy_price: e.target.value })}
                  required={editModal.item_type !== "GENERIC"}
                  placeholder={editModal.item_type === "GENERIC" ? "unbekannt" : undefined}
                  className="w-full bg-bg-primary border border-border rounded-lg px-3 py-2 text-text-primary text-sm font-[family-name:var(--font-mono)]"
                />
                <input type="number" step="0.01" value={editForm.buy_shipping} onChange={(e) => setEditForm({ ...editForm, buy_shipping: e.target.value })} className="w-full bg-bg-primary border border-border rounded-lg px-3 py-2 text-text-primary text-sm font-[family-name:var(--font-mono)]" />
              </div>
              <div className="grid grid-cols-2 gap-3">
                <input type="date" value={editForm.buy_date} onChange={(e) => setEditForm({ ...editForm, buy_date: e.target.value })} className="w-full bg-bg-primary border border-border rounded-lg px-3 py-2 text-text-primary text-sm" />
                <input type="text" list="platforms-list" value={editForm.buy_platform} onChange={(e) => setEditForm({ ...editForm, buy_platform: e.target.value })} placeholder="Plattform" className="w-full bg-bg-primary border border-border rounded-lg px-3 py-2 text-text-primary text-sm" />
              </div>
              <datalist id="platforms-list">{platforms.map((platform) => <option key={platform} value={platform} />)}</datalist>
              <input type="url" value={editForm.buy_url} onChange={(e) => setEditForm({ ...editForm, buy_url: e.target.value })} placeholder="Original-Link" className="w-full bg-bg-primary border border-border rounded-lg px-3 py-2 text-text-primary text-sm" />
              <div>
                <label className="block text-xs text-text-muted mb-1">Eigener Referenz-Link</label>
                <input
                  type="url"
                  value={editForm.reference_url || ""}
                  onChange={(e) => setEditForm({ ...editForm, reference_url: e.target.value })}
                  placeholder="https://…"
                  className="w-full px-3 py-2 rounded-lg bg-bg-primary border border-border text-sm"
                />
              </div>
              <div className="grid grid-cols-2 gap-3">
                <select value={editForm.condition} onChange={(e) => setEditForm({ ...editForm, condition: e.target.value })} className="w-full bg-bg-primary border border-border rounded-lg px-3 py-2 text-text-primary text-sm">
                  <option value="NEW_SEALED">Neu & Versiegelt</option>
                  <option value="NEW_OPEN_BOX">Neu & Geöffnet</option>
                  <option value="USED_COMPLETE">Gebraucht (komplett)</option>
                  <option value="USED_INCOMPLETE">Gebraucht (unvollständig)</option>
                  <option value="UNKNOWN">Zustand unbekannt</option>
                </select>
                <input type="number" min="1" value={editForm.quantity} onChange={(e) => setEditForm({ ...editForm, quantity: e.target.value })} className="w-full bg-bg-primary border border-border rounded-lg px-3 py-2 text-text-primary text-sm font-[family-name:var(--font-mono)]" />
              </div>
              <input type="text" maxLength={200} aria-label="Lagerort" value={editForm.storage_location} onChange={(e) => setEditForm({ ...editForm, storage_location: e.target.value })} placeholder="Lagerort, z. B. Dachboden Kiste 3" className="w-full bg-bg-primary border border-border rounded-lg px-3 py-2 text-text-primary text-sm" />
              <textarea value={editForm.notes} onChange={(e) => setEditForm({ ...editForm, notes: e.target.value })} placeholder="Eigene Notizen..." rows={3} className="w-full bg-bg-primary border border-border rounded-lg px-3 py-2 text-text-primary text-sm resize-none" />
            </div>
            <PhotoPicker
              itemId={editModal.id}
              existingPhotos={editModal.photos || []}
              removedPhotoIds={editRemovedPhotoIds}
              onToggleExistingPhoto={(photoId) => setEditRemovedPhotoIds((prev) => prev.includes(photoId) ? prev.filter((id) => id !== photoId) : [...prev, photoId])}
              onMakePrimaryExistingPhoto={(photoId) => makePrimaryPhotoMutation.mutate({ itemId: editModal.id, photoId })}
              localPhotos={editPhotoEntries}
              onSelectFiles={appendEditPhotos}
              onRemoveLocalPhoto={(photoId) => removeLocalPhoto(setEditPhotoEntries, photoId)}
              externalImageUrl={editForm.image_url || ""}
              onExternalImageUrlChange={(value) => setEditForm({ ...editForm, image_url: value })}
              isUpdatingPrimary={makePrimaryPhotoMutation.isPending}
            />
          </div>
          <div className="flex gap-3 mt-4">
            <button type="button" onClick={closeEditModal} className="flex-1 bg-bg-hover text-text-secondary py-2 rounded-lg">Abbrechen</button>
            <button type="submit" disabled={editMutation.isPending} className="flex-1 bg-lego-yellow text-black font-bold py-2 rounded-lg disabled:opacity-50">
              {editMutation.isPending ? "Speichern..." : "Speichern"}
            </button>
          </div>
          {editMutation.isError && <p className="text-no-go text-sm">{editMutation.error.message}</p>}
        </form>
      </div>
    </div>
  );
}
