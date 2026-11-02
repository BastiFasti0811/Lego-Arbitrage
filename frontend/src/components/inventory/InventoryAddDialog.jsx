import { api } from "../../api/client";
import PhotoPicker from "./PhotoPicker";
import { describeDuplicate, EURO, normalizeLocation } from "./inventoryFormat";

// Dialog "Set manuell hinzufuegen" mit Dubletten-Hinweis und Fotos.
// Zustand und Mutationen haelt die Seite (pages/Inventar.jsx).

export default function InventoryAddDialog({
  productGroups,
  platforms,
  removeLocalPhoto,
  editMutation,
  addForm,
  setAddForm,
  handleAdd,
  handleSetNumberChange,
  lookupLoading,
  duplicates,
  setDuplicates,
  duplicateTarget,
  addStorageLocationConflict,
  enteredStorageLocation,
  addPhotoEntries,
  appendAddPhotos,
  setAddPhotoEntries,
  closeAddModal,
  addMutation,
}) {
  return (
    <div className="fixed inset-0 z-50 flex items-start justify-center md:items-center bg-black/60 backdrop-blur-sm overflow-y-auto p-4">
      <div className="bg-bg-card border border-border rounded-xl p-6 w-full max-w-2xl my-6">
        <h2 className="text-text-primary text-lg font-bold mb-4">Set manuell hinzufügen</h2>
        <form onSubmit={handleAdd} className="space-y-4">
          <div className="grid md:grid-cols-2 gap-4">
            <div className="space-y-3">
              <div className="flex gap-2">
                <button
                  type="button"
                  onClick={() => setAddForm((prev) => ({ ...prev, item_type: "LEGO" }))}
                  className={`flex-1 text-sm px-3 py-2 rounded-lg font-medium transition-colors ${
                    addForm.item_type === "LEGO" ? "bg-lego-yellow text-black" : "bg-bg-hover text-text-secondary hover:text-text-primary"
                  }`}
                >
                  Lego
                </button>
                <button
                  type="button"
                  onClick={() => setAddForm((prev) => ({ ...prev, item_type: "GENERIC" }))}
                  className={`flex-1 text-sm px-3 py-2 rounded-lg font-medium transition-colors ${
                    addForm.item_type === "GENERIC" ? "bg-lego-yellow text-black" : "bg-bg-hover text-text-secondary hover:text-text-primary"
                  }`}
                >
                  Sonstiges
                </button>
              </div>
              {addForm.item_type === "LEGO" ? (
                <>
                  <div className="relative">
                    <input
                      type="text"
                      placeholder="Set-Nummer *"
                      value={addForm.set_number}
                      onChange={(e) => handleSetNumberChange(e.target.value)}
                      onBlur={async (e) => {
                        // Jeder neue Abgleich ist ein neuer Fall: eine
                        // fehlgeschlagene "Menge erhoehen" fuer die zuvor
                        // eingetragene Setnummer darf nicht als Fehler unter
                        // dem naechsten Hinweis auftauchen (Tippen, Tab raus,
                        // Nummer korrigieren, wieder raus — alles ohne dass
                        // der Dialog je zu war).
                        editMutation.reset();
                        const value = e.target.value.trim();
                        if (!value) { setDuplicates([]); return; }
                        try {
                          setDuplicates(await api.lookupInventory(value));
                        } catch {
                          // Der Hinweis ist eine Hilfe, kein Tor: faellt die Abfrage aus,
                          // laesst sich trotzdem einbuchen.
                          setDuplicates([]);
                        }
                      }}
                      required
                      className="w-full bg-bg-primary border border-border rounded-lg px-3 py-2 text-text-primary text-sm font-[family-name:var(--font-mono)]"
                    />
                    {lookupLoading && <span className="absolute right-3 top-1/2 -translate-y-1/2 text-lego-yellow text-xs animate-pulse">Suche...</span>}
                  </div>
                  {duplicates.length > 0 && (
                    <div className="mt-2 p-3 rounded-lg border border-lego-yellow/40 bg-lego-yellow/5 text-xs">
                      <p className="mb-2">
                        <strong>{duplicates[0].set_number}</strong> liegt bereits{" "}
                        {duplicates.reduce((sum, d) => sum + (d.quantity || 1), 0)}× im Bestand
                        {" "}({duplicates
                          .map((d) => describeDuplicate(d))
                          .join(" · ")}).
                      </p>
                      {duplicates.length > 1 && (
                        <p className="mb-2 text-text-muted">Erhöht wird der Posten vom {describeDuplicate(duplicateTarget)}.</p>
                      )}
                      {addStorageLocationConflict && (
                        <p id="add-storage-location-conflict" role="status" className="mb-2 text-text-muted">
                          Dein Lagerort „{enteredStorageLocation}" weicht ab und wird beim Erhöhen nicht übernommen. Liegt das Exemplar woanders, lege es neu an.
                        </p>
                      )}
                      <div className="flex flex-wrap gap-2">
                        <button
                          type="button"
                          disabled={editMutation.isPending}
                          className="px-2 py-1 rounded bg-lego-yellow text-bg-primary font-medium disabled:opacity-50"
                          onClick={() => {
                            const target = duplicateTarget;
                            const data = { quantity: (target.quantity || 1) + 1 };
                            // Lagerort nur nachtragen, wenn der vorhandene Posten keinen hat;
                            // einen abweichenden nicht still ueberschreiben.
                            if (enteredStorageLocation && !normalizeLocation(target.storage_location)) {
                              data.storage_location = enteredStorageLocation;
                            }
                            editMutation.mutate(
                              {
                                id: target.id,
                                data,
                                photoFiles: [],
                                deletedPhotoIds: [],
                              },
                              {
                                // Nur bei Erfolg schliessen: sonst behauptet der
                                // Dialog eine Mengenerhoehung, die nie ankam.
                                onSuccess: () => {
                                  setDuplicates([]);
                                  closeAddModal();
                                },
                              },
                            );
                          }}
                        >
                          Menge erhöhen
                        </button>
                        <button
                          type="button"
                          className="px-2 py-1 rounded border border-bg-hover"
                          onClick={() => setDuplicates([])}
                        >
                          Trotzdem neu anlegen
                        </button>
                      </div>
                      {editMutation.isError && <p className="text-no-go text-sm mt-2">{editMutation.error.message}</p>}
                    </div>
                  )}
                </>
              ) : (
                <>
                  <input
                    type="text"
                    list="product-groups-add-list"
                    placeholder="Warengruppe"
                    value={addForm.product_group}
                    onChange={(e) => setAddForm({ ...addForm, product_group: e.target.value })}
                    className="w-full bg-bg-primary border border-border rounded-lg px-3 py-2 text-text-primary text-sm"
                  />
                  <datalist id="product-groups-add-list">{productGroups.map((group) => <option key={group} value={group} />)}</datalist>
                </>
              )}
              <input type="text" placeholder="Set-Name *" value={addForm.set_name} onChange={(e) => setAddForm({ ...addForm, set_name: e.target.value })} required className="w-full bg-bg-primary border border-border rounded-lg px-3 py-2 text-text-primary text-sm" />
              <input type="text" placeholder="Theme" value={addForm.theme} onChange={(e) => setAddForm({ ...addForm, theme: e.target.value })} className="w-full bg-bg-primary border border-border rounded-lg px-3 py-2 text-text-primary text-sm" />
              <div className="grid grid-cols-2 gap-3">
                <input
                  type="number"
                  step="0.01"
                  placeholder={addForm.item_type === "GENERIC" ? "unbekannt" : `Kaufpreis (${EURO})`}
                  value={addForm.buy_price}
                  onChange={(e) => setAddForm({ ...addForm, buy_price: e.target.value })}
                  required={addForm.item_type !== "GENERIC"}
                  className="w-full bg-bg-primary border border-border rounded-lg px-3 py-2 text-text-primary text-sm font-[family-name:var(--font-mono)]"
                />
                <input type="number" step="0.01" placeholder={`Versand (${EURO})`} value={addForm.buy_shipping} onChange={(e) => setAddForm({ ...addForm, buy_shipping: e.target.value })} className="w-full bg-bg-primary border border-border rounded-lg px-3 py-2 text-text-primary text-sm font-[family-name:var(--font-mono)]" />
              </div>
              <div className="grid grid-cols-2 gap-3">
                <input type="date" value={addForm.buy_date} onChange={(e) => setAddForm({ ...addForm, buy_date: e.target.value })} className="w-full bg-bg-primary border border-border rounded-lg px-3 py-2 text-text-primary text-sm" />
                <input type="text" list="platforms-add-list" placeholder="Plattform" value={addForm.buy_platform} onChange={(e) => setAddForm({ ...addForm, buy_platform: e.target.value })} className="w-full bg-bg-primary border border-border rounded-lg px-3 py-2 text-text-primary text-sm" />
              </div>
              <datalist id="platforms-add-list">{platforms.map((platform) => <option key={platform} value={platform} />)}</datalist>
              <input type="url" placeholder="Original-Link" value={addForm.buy_url} onChange={(e) => setAddForm({ ...addForm, buy_url: e.target.value })} className="w-full bg-bg-primary border border-border rounded-lg px-3 py-2 text-text-primary text-sm" />
              <div className="grid grid-cols-2 gap-3">
                <select value={addForm.condition} onChange={(e) => setAddForm({ ...addForm, condition: e.target.value })} className="w-full bg-bg-primary border border-border rounded-lg px-3 py-2 text-text-primary text-sm">
                  <option value="NEW_SEALED">Neu & Versiegelt</option>
                  <option value="NEW_OPEN_BOX">Neu & Geöffnet</option>
                  <option value="USED_COMPLETE">Gebraucht (komplett)</option>
                  <option value="USED_INCOMPLETE">Gebraucht (unvollständig)</option>
                  <option value="UNKNOWN">Zustand unbekannt</option>
                </select>
                <input type="number" min="1" placeholder="Anzahl" value={addForm.quantity} onChange={(e) => setAddForm({ ...addForm, quantity: e.target.value })} className="w-full bg-bg-primary border border-border rounded-lg px-3 py-2 text-text-primary text-sm font-[family-name:var(--font-mono)]" />
              </div>
              <input type="text" maxLength={200} aria-label="Lagerort" aria-describedby={addStorageLocationConflict ? "add-storage-location-conflict" : undefined} placeholder="Lagerort, z. B. Dachboden Kiste 3" value={addForm.storage_location} onChange={(e) => setAddForm({ ...addForm, storage_location: e.target.value })} className="w-full bg-bg-primary border border-border rounded-lg px-3 py-2 text-text-primary text-sm" />
              <textarea value={addForm.notes} onChange={(e) => setAddForm({ ...addForm, notes: e.target.value })} placeholder="Eigene Notizen..." rows={3} className="w-full bg-bg-primary border border-border rounded-lg px-3 py-2 text-text-primary text-sm resize-none" />
            </div>
            <PhotoPicker
              itemId={0}
              existingPhotos={[]}
              removedPhotoIds={[]}
              onToggleExistingPhoto={() => {}}
              onMakePrimaryExistingPhoto={() => {}}
              localPhotos={addPhotoEntries}
              onSelectFiles={appendAddPhotos}
              onRemoveLocalPhoto={(photoId) => removeLocalPhoto(setAddPhotoEntries, photoId)}
              externalImageUrl={addForm.image_url}
              onExternalImageUrlChange={(value) => setAddForm({ ...addForm, image_url: value })}
            />
          </div>
          <div className="flex gap-3 mt-4">
            <button type="button" onClick={closeAddModal} className="flex-1 bg-bg-hover text-text-secondary py-2 rounded-lg">Abbrechen</button>
            <button type="submit" disabled={addMutation.isPending} className="flex-1 bg-lego-yellow text-black font-bold py-2 rounded-lg disabled:opacity-50">
              {addMutation.isPending ? "Hinzufügen..." : "Hinzufügen"}
            </button>
          </div>
          {addMutation.isError && <p className="text-no-go text-sm">{addMutation.error.message}</p>}
        </form>
      </div>
    </div>
  );
}
