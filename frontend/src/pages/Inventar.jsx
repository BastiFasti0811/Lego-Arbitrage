import { useEffect, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../api/client";
import StatCard from "../components/StatCard";

const EURO = "\u20ac";
const PHOTO_ICON = "\u{1F4F8}";
const BOX_ICON = "\u{1F4E6}";
const LINK_ICON = "\u2197";
const MAX_PREVIEW_PHOTOS = 4;

const emptyAddForm = () => ({
  set_number: "",
  set_name: "",
  theme: "",
  buy_price: "",
  buy_shipping: "",
  buy_date: new Date().toISOString().split("T")[0],
  buy_platform: "",
  buy_url: "",
  image_url: "",
  condition: "NEW_SEALED",
  quantity: "1",
  notes: "",
});

function formatMoney(value, digits = 0) {
  return `${Number(value || 0).toLocaleString("de-DE", {
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  })}${EURO}`;
}

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

function SellDropdown({ item, onMarkSold }) {
  const [open, setOpen] = useState(false);
  const [loading, setLoading] = useState(false);
  const [toast, setToast] = useState(false);
  const ref = useRef(null);

  useEffect(() => {
    const handler = (e) => {
      if (ref.current && !ref.current.contains(e.target)) setOpen(false);
    };
    document.addEventListener("mousedown", handler);
    return () => document.removeEventListener("mousedown", handler);
  }, []);

  const fetchAndAct = async (action) => {
    setLoading(true);
    try {
      const links = await api.getSellLinks(item.id);
      if (action === "ebay") {
        window.open(links.ebay_url, "_blank");
      } else if (action === "kleinanzeigen") {
        await navigator.clipboard.writeText(links.kleinanzeigen_text);
        setToast(true);
        setTimeout(() => setToast(false), 2000);
        window.open("https://www.kleinanzeigen.de/p-anzeige-aufgeben.html", "_blank");
      }
    } catch (err) {
      console.error("Sell links error:", err);
    } finally {
      setLoading(false);
      setOpen(false);
    }
  };

  return (
    <div ref={ref} className="relative">
      <button
        onClick={() => setOpen(!open)}
        className={`text-xs px-3 py-1.5 rounded-lg font-bold transition-colors bg-lego-yellow text-black hover:bg-lego-yellow/90 ${
          item.sell_signal_active ? "animate-pulse shadow-[0_0_8px_rgba(255,206,0,0.6)]" : ""
        }`}
      >
        {loading ? "..." : "Verkaufen \u25BE"}
      </button>
      {toast && (
        <div className="absolute left-0 -top-8 bg-go-star text-black text-xs font-bold px-2 py-1 rounded shadow-lg whitespace-nowrap z-50">
          Text kopiert
        </div>
      )}
      {open && (
        <div className="absolute left-0 top-full mt-1 bg-bg-primary border border-border rounded-lg shadow-xl z-50 min-w-[200px]">
          <button
            onClick={() => fetchAndAct("ebay")}
            className="w-full text-left text-sm text-text-primary px-3 py-2 hover:bg-bg-hover rounded-t-lg transition-colors"
          >
            Auf eBay einstellen
          </button>
          <button
            onClick={() => fetchAndAct("kleinanzeigen")}
            className="w-full text-left text-sm text-text-primary px-3 py-2 hover:bg-bg-hover transition-colors"
          >
            Auf Kleinanzeigen einstellen
          </button>
          <button
            onClick={() => {
              setOpen(false);
              onMarkSold();
            }}
            className="w-full text-left text-sm text-text-primary px-3 py-2 hover:bg-bg-hover rounded-b-lg transition-colors border-t border-border/50"
          >
            Als verkauft markieren
          </button>
        </div>
      )}
    </div>
  );
}

function PhotoPicker({
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

function FormSection({ title, description, children }) {
  return (
    <section className="space-y-3 rounded-2xl border border-border/80 bg-bg-primary/40 px-4 py-4 shadow-[inset_0_1px_0_rgba(255,255,255,0.03)]">
      <div className="space-y-1">
        <h3 className="text-sm font-semibold tracking-tight text-text-primary">{title}</h3>
        {description && <p className="text-xs leading-5 text-text-muted">{description}</p>}
      </div>
      {children}
    </section>
  );
}

function FieldLabel({ children, required = false }) {
  return (
    <label className="block pl-1 text-[11px] font-medium uppercase tracking-[0.14em] text-text-muted/80">
      {children}{required ? " *" : ""}
    </label>
  );
}

function FieldGroup({ label, required = false, children }) {
  return (
    <div className="space-y-1.5">
      <FieldLabel required={required}>{label}</FieldLabel>
      {children}
    </div>
  );
}

function inputClassName(extra = "") {
  return `w-full rounded-xl border border-border bg-bg-primary px-3 py-2.5 text-sm text-text-primary transition-colors placeholder:text-text-muted/70 focus:border-lego-yellow/70 focus:outline-none focus:ring-2 focus:ring-lego-yellow/20 ${extra}`.trim();
}

function textareaClassName(extra = "") {
  return `w-full rounded-xl border border-border bg-bg-primary px-3 py-2.5 text-sm text-text-primary transition-colors placeholder:text-text-muted/70 focus:border-lego-yellow/70 focus:outline-none focus:ring-2 focus:ring-lego-yellow/20 resize-none ${extra}`.trim();
}

function InventoryPhotoStrip({ item }) {
  const uploadedPhotos = item.photos || [];
  if (!uploadedPhotos.length && !item.image_url) return null;

  return (
    <div className="flex flex-wrap gap-2 mt-3">
      {uploadedPhotos.slice(0, MAX_PREVIEW_PHOTOS).map((photo) => {
        const photoUrl = api.inventoryPhotoUrl(item.id, photo.id);
        return (
          <a key={photo.id} href={photoUrl} target="_blank" rel="noreferrer" className="block rounded-lg overflow-hidden border border-border bg-bg-primary hover:border-lego-yellow transition-colors">
            <img src={photoUrl} alt={photo.original_filename || item.set_name} className="w-16 h-16 object-cover" />
          </a>
        );
      })}
      {uploadedPhotos.length > MAX_PREVIEW_PHOTOS && (
        <div className="w-16 h-16 rounded-lg border border-border bg-bg-primary flex items-center justify-center text-xs text-text-muted">
          +{uploadedPhotos.length - MAX_PREVIEW_PHOTOS}
        </div>
      )}
      {item.image_url && (
        <a
          href={item.image_url}
          target="_blank"
          rel="noreferrer"
          className="min-h-16 px-3 rounded-lg border border-border bg-bg-primary hover:border-lego-yellow transition-colors text-xs text-text-muted inline-flex items-center gap-2"
        >
          <span>{PHOTO_ICON}</span>
          <span>Externer Foto-Link {LINK_ICON}</span>
        </a>
      )}
    </div>
  );
}

export default function Inventar() {
  const queryClient = useQueryClient();
  const [sellModal, setSellModal] = useState(null);
  const [sellPrice, setSellPrice] = useState("");
  const [sellDate, setSellDate] = useState(new Date().toISOString().split("T")[0]);
  const [sellPlatform, setSellPlatform] = useState("");
  const [addModal, setAddModal] = useState(false);
  const [editModal, setEditModal] = useState(null);
  const [editForm, setEditForm] = useState({});
  const [lookupLoading, setLookupLoading] = useState(false);
  const [addForm, setAddForm] = useState(emptyAddForm());
  const [addPhotoEntries, setAddPhotoEntries] = useState([]);
  const [editPhotoEntries, setEditPhotoEntries] = useState([]);
  const [editRemovedPhotoIds, setEditRemovedPhotoIds] = useState([]);
  const addPhotoEntriesRef = useRef([]);
  const editPhotoEntriesRef = useRef([]);

  useEffect(() => {
    addPhotoEntriesRef.current = addPhotoEntries;
  }, [addPhotoEntries]);

  useEffect(() => {
    editPhotoEntriesRef.current = editPhotoEntries;
  }, [editPhotoEntries]);

  useEffect(() => () => {
    revokeLocalPhotoEntries(addPhotoEntriesRef.current);
    revokeLocalPhotoEntries(editPhotoEntriesRef.current);
  }, []);

  const { data: platforms = [] } = useQuery({ queryKey: ["platforms"], queryFn: api.listPlatforms });
  const { data: summary } = useQuery({ queryKey: ["portfolio"], queryFn: api.portfolioSummary });
  const { data: items, isLoading } = useQuery({
    queryKey: ["inventory"],
    queryFn: () => api.listInventory({ status: "HOLDING" }),
  });

  function closeAddModal() {
    revokeLocalPhotoEntries(addPhotoEntries);
    setAddPhotoEntries([]);
    setAddForm(emptyAddForm());
    setAddModal(false);
  }

  function closeEditModal() {
    revokeLocalPhotoEntries(editPhotoEntries);
    setEditPhotoEntries([]);
    setEditRemovedPhotoIds([]);
    setEditModal(null);
    setEditForm({});
  }

  function appendAddPhotos(fileList) {
    if (fileList?.length) setAddPhotoEntries((prev) => [...prev, ...createLocalPhotoEntries(fileList)]);
  }

  function appendEditPhotos(fileList) {
    if (fileList?.length) setEditPhotoEntries((prev) => [...prev, ...createLocalPhotoEntries(fileList)]);
  }

  function removeLocalPhoto(setter, photoId) {
    setter((prev) => {
      const removed = prev.find((entry) => entry.id === photoId);
      if (removed) {
        URL.revokeObjectURL(removed.previewUrl);
      }
      return prev.filter((entry) => entry.id !== photoId);
    });
  }

  const sellMutation = useMutation({
    mutationFn: ({ id, data }) => api.sellInventory(id, data),
    onSuccess: () => {
      setSellModal(null);
      setSellPrice("");
      queryClient.invalidateQueries({ queryKey: ["inventory"] });
      queryClient.invalidateQueries({ queryKey: ["portfolio"] });
      queryClient.invalidateQueries({ queryKey: ["history"] });
    },
  });

  const deleteMutation = useMutation({
    mutationFn: (id) => api.deleteInventory(id),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["inventory"] });
      queryClient.invalidateQueries({ queryKey: ["portfolio"] });
    },
  });

  const makePrimaryPhotoMutation = useMutation({
    mutationFn: ({ itemId, photoId }) => api.makePrimaryInventoryPhoto(itemId, photoId),
    onSuccess: (photos, variables) => {
      queryClient.setQueryData(["inventory"], (current) => {
        if (!Array.isArray(current)) return current;
        return current.map((item) => (item.id === variables.itemId ? { ...item, photos } : item));
      });
      setEditModal((prev) => (
        prev && prev.id === variables.itemId
          ? { ...prev, photos }
          : prev
      ));
      queryClient.invalidateQueries({ queryKey: ["inventory"] });
    },
  });

  const editMutation = useMutation({
    mutationFn: async ({ id, data, photoFiles, deletedPhotoIds }) => {
      const updated = await api.updateInventory(id, data);
      for (const photoId of deletedPhotoIds) {
        await api.deleteInventoryPhoto(id, photoId);
      }
      if (photoFiles.length > 0) {
        await api.uploadInventoryPhotos(id, photoFiles);
      }
      return updated;
    },
    onSuccess: () => {
      closeEditModal();
      queryClient.invalidateQueries({ queryKey: ["inventory"] });
      queryClient.invalidateQueries({ queryKey: ["portfolio"] });
      queryClient.invalidateQueries({ queryKey: ["history"] });
    },
  });

  const addMutation = useMutation({
    mutationFn: async ({ photoFiles, ...data }) => {
      const item = await api.addInventory(data);
      if (photoFiles.length > 0) {
        await api.uploadInventoryPhotos(item.id, photoFiles);
      }
      return item;
    },
    onSuccess: () => {
      closeAddModal();
      queryClient.invalidateQueries({ queryKey: ["inventory"] });
      queryClient.invalidateQueries({ queryKey: ["portfolio"] });
    },
  });

  const handleSell = () => {
    if (!sellModal || !sellPrice) return;
    sellMutation.mutate({
      id: sellModal.id,
      data: { sell_price: parseFloat(sellPrice), sell_date: sellDate, sell_platform: sellPlatform || null },
    });
  };

  const handleSetNumberChange = async (setNumber) => {
    setAddForm((prev) => ({ ...prev, set_number: setNumber }));
    if (/^\d{4,6}$/.test(setNumber.trim())) {
      setLookupLoading(true);
      try {
        const info = await api.lookupSet(setNumber.trim());
        if (info.found) {
          setAddForm((prev) => ({
            ...prev,
            set_name: info.set_name || prev.set_name,
            theme: info.theme || prev.theme,
          }));
        }
      } catch {
        // User can fill data manually.
      } finally {
        setLookupLoading(false);
      }
    }
  };

  const openEdit = (item) => {
    closeEditModal();
    setEditModal(item);
    setEditForm({
      set_name: item.set_name,
      theme: item.theme || "",
      buy_price: String(item.buy_price),
      buy_shipping: String(item.buy_shipping),
      buy_date: item.buy_date,
      buy_platform: item.buy_platform || "",
      buy_url: item.buy_url || "",
      image_url: item.image_url || "",
      condition: item.condition,
      quantity: String(item.quantity || 1),
      notes: item.notes || "",
    });
  };

  const handleEdit = (e) => {
    e.preventDefault();
    editMutation.mutate({
      id: editModal.id,
      data: {
        set_name: editForm.set_name,
        theme: editForm.theme || null,
        buy_price: parseFloat(editForm.buy_price),
        buy_shipping: parseFloat(editForm.buy_shipping || "0"),
        buy_date: editForm.buy_date,
        buy_platform: editForm.buy_platform || null,
        buy_url: editForm.buy_url || null,
        image_url: editForm.image_url || null,
        condition: editForm.condition,
        quantity: parseInt(editForm.quantity || "1", 10),
        notes: editForm.notes || null,
      },
      photoFiles: editPhotoEntries.map((entry) => entry.file),
      deletedPhotoIds: editRemovedPhotoIds,
    });
  };

  const handleAdd = (e) => {
    e.preventDefault();
    addMutation.mutate({
      ...addForm,
      buy_price: parseFloat(addForm.buy_price),
      buy_shipping: parseFloat(addForm.buy_shipping || "0"),
      quantity: parseInt(addForm.quantity || "1", 10),
      buy_url: addForm.buy_url || null,
      image_url: addForm.image_url || null,
      notes: addForm.notes || null,
      photoFiles: addPhotoEntries.map((entry) => entry.file),
    });
  };

  const profitColor = (value) => (value > 0 ? "text-go-star" : value < 0 ? "text-no-go" : "text-text-primary");

  return (
    <div>
      <div className="flex items-center justify-between mb-6">
        <h1 className="text-2xl font-bold text-text-primary">Inventar</h1>
        <button onClick={() => setAddModal(true)} className="bg-lego-yellow text-black font-bold px-4 py-2 rounded-lg text-sm hover:bg-lego-yellow/90 transition-colors">
          + Hinzufügen
        </button>
      </div>

      {summary && (
        <div className="grid grid-cols-2 md:grid-cols-4 gap-3 mb-6">
          <StatCard label="Sets" value={summary.holding_items} sub={`${summary.sold_items} verkauft`} />
          <StatCard label="Investiert" value={formatMoney(summary.total_invested)} />
          <StatCard label="Aktueller Wert" value={formatMoney(summary.current_value)} color="text-lego-yellow" />
          <StatCard label="Unrealisiert" value={`${summary.unrealized_profit >= 0 ? "+" : ""}${formatMoney(summary.unrealized_profit)}`} sub={`${summary.unrealized_roi_percent.toFixed(1)}%`} color={profitColor(summary.unrealized_profit)} />
        </div>
      )}

      {summary && <p className="text-text-muted text-xs mb-4">Marktwerte werden automatisch alle 6 Stunden aktualisiert.</p>}

      {summary?.sell_signals_active > 0 && (
        <div className="bg-sell-signal/10 border border-sell-signal/30 rounded-xl p-4 mb-6 flex items-center gap-3">
          <div className="w-3 h-3 bg-sell-signal rounded-full animate-pulse" />
          <span className="text-sell-signal font-medium text-sm">
            {summary.sell_signals_active} Sell-Signal{summary.sell_signals_active > 1 ? "e" : ""} aktiv - jetzt verkaufen.
          </span>
        </div>
      )}

      {isLoading ? (
        <div className="space-y-3">
          {[...Array(3)].map((_, i) => (
            <div key={i} className="bg-bg-card border border-border rounded-xl p-4 animate-pulse">
              <div className="h-4 bg-bg-hover rounded w-1/4 mb-2" />
              <div className="h-3 bg-bg-hover rounded w-1/2" />
            </div>
          ))}
        </div>
      ) : !items?.length ? (
        <div className="text-center py-20">
          <div className="text-4xl mb-4">{BOX_ICON}</div>
          <h2 className="text-text-primary text-lg font-semibold mb-2">Noch keine Sets im Inventar</h2>
          <p className="text-text-muted text-sm">Kaufe Sets über den Deal-Check oder füge sie manuell hinzu.</p>
        </div>
      ) : (
        <div className="space-y-3">
          {items.map((item) => (
            <div key={item.id} className={`bg-bg-card border rounded-xl p-4 ${item.sell_signal_active ? "border-sell-signal/50" : "border-border"}`}>
              <div className="flex items-start justify-between gap-4">
                <div className="flex-1 min-w-0">
                  <div className="flex items-center gap-2 mb-1">
                    <span className="text-lego-yellow font-[family-name:var(--font-mono)] text-sm font-semibold">{item.set_number}</span>
                    {item.quantity > 1 && <span className="bg-lego-blue/20 text-lego-blue text-xs px-1.5 py-0.5 rounded font-[family-name:var(--font-mono)] font-bold">x{item.quantity}</span>}
                    {item.photos?.length > 0 && <span className="bg-lego-blue/10 text-lego-blue text-xs px-2 py-0.5 rounded-full">{PHOTO_ICON} {item.photos.length}</span>}
                    {item.sell_signal_active && <span className="bg-sell-signal/20 text-sell-signal text-xs px-2 py-0.5 rounded-full animate-pulse font-medium">SELL</span>}
                  </div>
                  <h3 className="text-text-primary text-sm font-medium truncate">{item.set_name}</h3>
                  <div className="flex flex-wrap gap-4 mt-2 text-xs text-text-muted">
                    <span>Gekauft: {new Date(item.buy_date).toLocaleDateString("de-DE")}</span>
                    <span>{item.holding_days} Tage</span>
                    {item.buy_platform && <span>{item.buy_platform}</span>}
                    {item.buy_url && <a href={item.buy_url} target="_blank" rel="noreferrer" className="text-lego-yellow hover:text-lego-yellow/80 transition-colors">Original-Link</a>}
                    {item.image_url && <a href={item.image_url} target="_blank" rel="noreferrer" className="text-lego-blue hover:text-lego-blue/80 transition-colors">Externer Foto-Link</a>}
                  </div>
                  {item.notes && <p className="text-text-muted text-xs mt-2">{item.notes}</p>}
                  {item.sell_signal_active && item.sell_signal_reason && <p className="text-sell-signal text-xs mt-2">{item.sell_signal_reason}</p>}
                  <InventoryPhotoStrip item={item} />
                </div>
                <div className="text-right shrink-0">
                  <div className="text-text-muted text-xs">Kaufpreis</div>
                  <div className="text-text-primary font-[family-name:var(--font-mono)] font-semibold">{formatMoney(item.total_invested)}</div>
                  {item.current_market_price && (
                    <>
                      <div className="text-text-muted text-xs mt-2">Marktwert</div>
                      <div className="text-lego-yellow font-[family-name:var(--font-mono)] font-semibold">{formatMoney(item.current_market_price)}</div>
                      <div className={`font-[family-name:var(--font-mono)] text-sm font-bold ${profitColor(item.unrealized_profit || 0)}`}>
                        {(item.unrealized_profit || 0) > 0 ? "+" : ""}{formatMoney(item.unrealized_profit || 0)}
                        <span className="text-xs ml-1">({item.unrealized_roi_percent?.toFixed(1)}%)</span>
                      </div>
                    </>
                  )}
                </div>
              </div>
              <div className="flex gap-2 mt-3 pt-3 border-t border-border/50">
                <button onClick={() => openEdit(item)} className="bg-lego-blue/10 text-lego-blue text-xs px-3 py-1.5 rounded-lg hover:bg-lego-blue/20 transition-colors">Bearbeiten</button>
                <SellDropdown item={item} onMarkSold={() => { setSellModal(item); setSellPrice(""); setSellPlatform(""); }} />
                <button onClick={() => { if (confirm(`${item.set_name} entfernen?`)) deleteMutation.mutate(item.id); }} className="bg-no-go/10 text-no-go text-xs px-3 py-1.5 rounded-lg hover:bg-no-go/20 transition-colors">Entfernen</button>
              </div>
            </div>
          ))}
        </div>
      )}

      {sellModal && (
        <div className="fixed inset-0 z-50 flex items-start justify-center md:items-center bg-black/60 backdrop-blur-sm overflow-y-auto p-4">
          <div className="bg-bg-card border border-border rounded-xl p-6 w-full max-w-md my-6">
            <h2 className="text-text-primary text-lg font-bold mb-4">Verkauft markieren</h2>
            <div className="text-text-muted text-sm mb-4">
              <span className="text-lego-yellow font-[family-name:var(--font-mono)]">{sellModal.set_number}</span> - {sellModal.set_name}
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
      )}

      {editModal && (
        <div className="fixed inset-0 z-50 flex items-start justify-center md:items-center bg-black/60 backdrop-blur-sm overflow-y-auto p-4">
          <div className="w-full max-w-3xl my-6 overflow-hidden rounded-[28px] border border-border/80 bg-bg-card shadow-[0_24px_80px_rgba(0,0,0,0.45)]">
            <div className="border-b border-border/70 bg-[linear-gradient(135deg,rgba(255,206,0,0.14),rgba(255,206,0,0.03)_38%,transparent_70%)] px-6 py-5">
              <p className="text-[11px] font-medium uppercase tracking-[0.18em] text-text-muted/80">Inventar</p>
              <h2 className="mt-1 text-xl font-bold tracking-tight text-text-primary">Set bearbeiten</h2>
              <p className="mt-1 font-[family-name:var(--font-mono)] text-sm text-lego-yellow">{editModal.set_number}</p>
            </div>
            <form onSubmit={handleEdit} className="space-y-5 p-6">
              <div className="grid md:grid-cols-2 gap-4">
                <div className="space-y-4">
                  <FieldGroup label="Set-Name" required>
                    <input type="text" value={editForm.set_name} onChange={(e) => setEditForm({ ...editForm, set_name: e.target.value })} required className={inputClassName()} />
                  </FieldGroup>
                  <FieldGroup label="Theme">
                    <input type="text" value={editForm.theme} onChange={(e) => setEditForm({ ...editForm, theme: e.target.value })} placeholder="z. B. Technic oder Star Wars" className={inputClassName()} />
                  </FieldGroup>
                  <div className="grid grid-cols-2 gap-3">
                    <div className="space-y-1">
                      <label className="block pl-1 text-[11px] font-medium uppercase tracking-[0.14em] text-text-muted/80">Kaufpreis</label>
                      <input type="number" step="0.01" value={editForm.buy_price} onChange={(e) => setEditForm({ ...editForm, buy_price: e.target.value })} required className="w-full bg-bg-primary border border-border rounded-lg px-3 py-2 text-text-primary text-sm font-[family-name:var(--font-mono)]" />
                    </div>
                    <div className="space-y-1">
                      <label className="block pl-1 text-[11px] font-medium uppercase tracking-[0.14em] text-text-muted/80">Versand</label>
                      <input type="number" step="0.01" value={editForm.buy_shipping} onChange={(e) => setEditForm({ ...editForm, buy_shipping: e.target.value })} className="w-full bg-bg-primary border border-border rounded-lg px-3 py-2 text-text-primary text-sm font-[family-name:var(--font-mono)]" />
                    </div>
                  </div>
                  <div className="grid grid-cols-2 gap-3">
                    <FieldGroup label="Kaufdatum">
                      <input type="date" value={editForm.buy_date} onChange={(e) => setEditForm({ ...editForm, buy_date: e.target.value })} className={inputClassName()} />
                    </FieldGroup>
                    <FieldGroup label="Plattform">
                      <input type="text" list="platforms-list" value={editForm.buy_platform} onChange={(e) => setEditForm({ ...editForm, buy_platform: e.target.value })} placeholder="z. B. Amazon.de" className={inputClassName()} />
                    </FieldGroup>
                  </div>
                  <datalist id="platforms-list">{platforms.map((platform) => <option key={platform} value={platform} />)}</datalist>
                  <FieldGroup label="Original-Link">
                    <input type="url" value={editForm.buy_url} onChange={(e) => setEditForm({ ...editForm, buy_url: e.target.value })} placeholder="Produktseite oder Bestelllink" className={inputClassName()} />
                  </FieldGroup>
                  <div className="grid grid-cols-2 gap-3">
                    <select value={editForm.condition} onChange={(e) => setEditForm({ ...editForm, condition: e.target.value })} className="w-full bg-bg-primary border border-border rounded-lg px-3 py-2 text-text-primary text-sm">
                      <option value="NEW_SEALED">Neu & Versiegelt</option>
                      <option value="NEW_OPEN">Neu & Geöffnet</option>
                      <option value="USED_COMPLETE">Gebraucht (komplett)</option>
                      <option value="USED_INCOMPLETE">Gebraucht (unvollständig)</option>
                    </select>
                    <input type="number" min="1" value={editForm.quantity} onChange={(e) => setEditForm({ ...editForm, quantity: e.target.value })} className="w-full bg-bg-primary border border-border rounded-lg px-3 py-2 text-text-primary text-sm font-[family-name:var(--font-mono)]" />
                  </div>
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
              <div className="mt-2 flex gap-3 border-t border-border/70 pt-5">
                <button type="button" onClick={closeEditModal} className="flex-1 rounded-xl border border-border bg-bg-hover px-4 py-3 text-sm font-medium text-text-secondary transition-colors hover:bg-bg-primary hover:text-text-primary">Abbrechen</button>
                <button type="submit" disabled={editMutation.isPending} className="flex-1 rounded-xl bg-lego-yellow px-4 py-3 text-sm font-bold text-black transition-all hover:brightness-105 disabled:opacity-50">
                  {editMutation.isPending ? "Speichern..." : "Speichern"}
                </button>
              </div>
              {editMutation.isError && <p className="text-no-go text-sm">{editMutation.error.message}</p>}
            </form>
          </div>
        </div>
      )}

      {addModal && (
        <div className="fixed inset-0 z-50 flex items-start justify-center md:items-center bg-black/60 backdrop-blur-sm overflow-y-auto p-4">
          <div className="w-full max-w-3xl my-6 overflow-hidden rounded-[28px] border border-border/80 bg-bg-card shadow-[0_24px_80px_rgba(0,0,0,0.45)]">
            <div className="border-b border-border/70 bg-[linear-gradient(135deg,rgba(59,130,246,0.16),rgba(59,130,246,0.05)_36%,transparent_72%)] px-6 py-5">
              <p className="text-[11px] font-medium uppercase tracking-[0.18em] text-text-muted/80">Inventar</p>
              <h2 className="mt-1 text-xl font-bold tracking-tight text-text-primary">Set manuell hinzufügen</h2>
              <p className="mt-1 text-sm text-text-muted">Ein klar gegliedertes Formular für schnellen, sauberen Inventar-Input.</p>
            </div>
            <form onSubmit={handleAdd} className="space-y-5 p-6">
              <div className="grid md:grid-cols-2 gap-4">
                <div className="space-y-4">
                  <FieldGroup label="Set-Nummer" required>
                    <div className="relative">
                      <input type="text" placeholder="z. B. 42100" value={addForm.set_number} onChange={(e) => handleSetNumberChange(e.target.value)} required className={inputClassName("pr-16 font-[family-name:var(--font-mono)]")} />
                      {lookupLoading && <span className="absolute right-3 top-1/2 -translate-y-1/2 text-lego-yellow text-xs animate-pulse">Suche...</span>}
                    </div>
                  </FieldGroup>
                  <FieldGroup label="Set-Name" required>
                    <input type="text" placeholder="Offizieller oder eigener Name" value={addForm.set_name} onChange={(e) => setAddForm({ ...addForm, set_name: e.target.value })} required className={inputClassName()} />
                  </FieldGroup>
                  <FieldGroup label="Theme">
                    <input type="text" placeholder="z. B. Technic oder Icons" value={addForm.theme} onChange={(e) => setAddForm({ ...addForm, theme: e.target.value })} className={inputClassName()} />
                  </FieldGroup>
                  <div className="grid grid-cols-2 gap-3">
                    <div className="space-y-1">
                      <label className="block pl-1 text-[11px] font-medium uppercase tracking-[0.14em] text-text-muted/80">Kaufpreis</label>
                      <input type="number" step="0.01" placeholder={`Kaufpreis (${EURO})`} value={addForm.buy_price} onChange={(e) => setAddForm({ ...addForm, buy_price: e.target.value })} required className="w-full bg-bg-primary border border-border rounded-lg px-3 py-2 text-text-primary text-sm font-[family-name:var(--font-mono)]" />
                    </div>
                    <div className="space-y-1">
                      <label className="block pl-1 text-[11px] font-medium uppercase tracking-[0.14em] text-text-muted/80">Versand</label>
                      <input type="number" step="0.01" placeholder={`Versand (${EURO})`} value={addForm.buy_shipping} onChange={(e) => setAddForm({ ...addForm, buy_shipping: e.target.value })} className="w-full bg-bg-primary border border-border rounded-lg px-3 py-2 text-text-primary text-sm font-[family-name:var(--font-mono)]" />
                    </div>
                  </div>
                  <div className="grid grid-cols-2 gap-3">
                    <FieldGroup label="Kaufdatum">
                      <input type="date" value={addForm.buy_date} onChange={(e) => setAddForm({ ...addForm, buy_date: e.target.value })} className={inputClassName()} />
                    </FieldGroup>
                    <FieldGroup label="Plattform">
                      <input type="text" list="platforms-add-list" placeholder="z. B. Amazon.de" value={addForm.buy_platform} onChange={(e) => setAddForm({ ...addForm, buy_platform: e.target.value })} className={inputClassName()} />
                    </FieldGroup>
                  </div>
                  <datalist id="platforms-add-list">{platforms.map((platform) => <option key={platform} value={platform} />)}</datalist>
                  <FieldGroup label="Original-Link">
                    <input type="url" placeholder="Produktseite oder Bestelllink" value={addForm.buy_url} onChange={(e) => setAddForm({ ...addForm, buy_url: e.target.value })} className={inputClassName()} />
                  </FieldGroup>
                  <div className="grid grid-cols-2 gap-3">
                    <select value={addForm.condition} onChange={(e) => setAddForm({ ...addForm, condition: e.target.value })} className="w-full bg-bg-primary border border-border rounded-lg px-3 py-2 text-text-primary text-sm">
                      <option value="NEW_SEALED">Neu & Versiegelt</option>
                      <option value="NEW_OPEN">Neu & Geöffnet</option>
                      <option value="USED_COMPLETE">Gebraucht (komplett)</option>
                      <option value="USED_INCOMPLETE">Gebraucht (unvollständig)</option>
                    </select>
                    <input type="number" min="1" placeholder="Anzahl" value={addForm.quantity} onChange={(e) => setAddForm({ ...addForm, quantity: e.target.value })} className="w-full bg-bg-primary border border-border rounded-lg px-3 py-2 text-text-primary text-sm font-[family-name:var(--font-mono)]" />
                  </div>
                  <FieldGroup label="Eigene Notizen">
                    <textarea value={addForm.notes} onChange={(e) => setAddForm({ ...addForm, notes: e.target.value })} placeholder="z. B. Gutschein, Lagerort oder Besonderheiten" rows={4} className={textareaClassName()} />
                  </FieldGroup>
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
              <div className="mt-2 flex gap-3 border-t border-border/70 pt-5">
                <button type="button" onClick={closeAddModal} className="flex-1 rounded-xl border border-border bg-bg-hover px-4 py-3 text-sm font-medium text-text-secondary transition-colors hover:bg-bg-primary hover:text-text-primary">Abbrechen</button>
                <button type="submit" disabled={addMutation.isPending} className="flex-1 rounded-xl bg-lego-yellow px-4 py-3 text-sm font-bold text-black transition-all hover:brightness-105 disabled:opacity-50">
                  {addMutation.isPending ? "Hinzufügen..." : "Hinzufügen"}
                </button>
              </div>
              {addMutation.isError && <p className="text-no-go text-sm">{addMutation.error.message}</p>}
            </form>
          </div>
        </div>
      )}
    </div>
  );
}





