import { useState, useCallback } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { api } from "../api/client";
import StatCard from "../components/StatCard";

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
  const [addForm, setAddForm] = useState({
    set_number: "", set_name: "", theme: "", buy_price: "", buy_shipping: "0",
    buy_date: new Date().toISOString().split("T")[0], buy_platform: "", condition: "NEW_SEALED",
  });

  // Queries
  const { data: summary } = useQuery({
    queryKey: ["portfolio"],
    queryFn: api.portfolioSummary,
  });

  const { data: items, isLoading } = useQuery({
    queryKey: ["inventory"],
    queryFn: () => api.listInventory({ status: "HOLDING" }),
  });

  // Mutations
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

  const editMutation = useMutation({
    mutationFn: ({ id, data }) => api.updateInventory(id, data),
    onSuccess: () => {
      setEditModal(null);
      queryClient.invalidateQueries({ queryKey: ["inventory"] });
      queryClient.invalidateQueries({ queryKey: ["portfolio"] });
    },
  });

  const addMutation = useMutation({
    mutationFn: (data) => api.addInventory(data),
    onSuccess: () => {
      setAddModal(false);
      setAddForm({ set_number: "", set_name: "", theme: "", buy_price: "", buy_shipping: "0", buy_date: new Date().toISOString().split("T")[0], buy_platform: "", condition: "NEW_SEALED" });
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

  // Auto-lookup set info when set number changes
  const handleSetNumberChange = useCallback(async (setNumber) => {
    setAddForm((prev) => ({ ...prev, set_number: setNumber }));
    // Auto-lookup when 4-6 digits entered
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
        // Lookup failed, user enters manually
      } finally {
        setLookupLoading(false);
      }
    }
  }, []);

  const openEdit = (item) => {
    setEditModal(item);
    setEditForm({
      set_name: item.set_name,
      theme: item.theme || "",
      buy_price: String(item.buy_price),
      buy_shipping: String(item.buy_shipping),
      buy_date: item.buy_date,
      buy_platform: item.buy_platform || "",
      condition: item.condition,
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
        condition: editForm.condition,
        notes: editForm.notes || null,
      },
    });
  };

  const handleAdd = (e) => {
    e.preventDefault();
    addMutation.mutate({
      ...addForm,
      buy_price: parseFloat(addForm.buy_price),
      buy_shipping: parseFloat(addForm.buy_shipping || "0"),
    });
  };

  const profitColor = (val) => (val > 0 ? "text-go-star" : val < 0 ? "text-no-go" : "text-text-primary");

  return (
    <div>
      {/* Header */}
      <div className="flex items-center justify-between mb-6">
        <h1 className="text-2xl font-bold text-text-primary">Inventar</h1>
        <button
          onClick={() => setAddModal(true)}
          className="bg-lego-yellow text-black font-bold px-4 py-2 rounded-lg text-sm hover:bg-lego-yellow/90 transition-colors"
        >
          + Hinzufügen
        </button>
      </div>

      {/* Portfolio Summary */}
      {summary && (
        <div className="grid grid-cols-2 md:grid-cols-4 gap-3 mb-6">
          <StatCard label="Sets" value={summary.holding_items} sub={`${summary.sold_items} verkauft`} />
          <StatCard label="Investiert" value={`${summary.total_invested.toLocaleString("de-DE")}€`} />
          <StatCard
            label="Aktueller Wert"
            value={`${summary.current_value.toLocaleString("de-DE")}€`}
            color="text-lego-yellow"
          />
          <StatCard
            label="Unrealisiert"
            value={`${summary.unrealized_profit >= 0 ? "+" : ""}${summary.unrealized_profit.toLocaleString("de-DE")}€`}
            sub={`${summary.unrealized_roi_percent.toFixed(1)}%`}
            color={profitColor(summary.unrealized_profit)}
          />
        </div>
      )}

      {/* Valuation Info */}
      {summary && (
        <p className="text-text-muted text-xs mb-4">
          Marktwerte werden automatisch alle 6 Stunden aktualisiert (Celery Beat)
        </p>
      )}

      {/* Sell Signals Banner */}
      {summary?.sell_signals_active > 0 && (
        <div className="bg-sell-signal/10 border border-sell-signal/30 rounded-xl p-4 mb-6 flex items-center gap-3">
          <div className="w-3 h-3 bg-sell-signal rounded-full animate-pulse" />
          <span className="text-sell-signal font-medium text-sm">
            {summary.sell_signals_active} Sell-Signal{summary.sell_signals_active > 1 ? "e" : ""} aktiv — jetzt verkaufen!
          </span>
        </div>
      )}

      {/* Inventory Items */}
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
          <div className="text-4xl mb-4">📦</div>
          <h2 className="text-text-primary text-lg font-semibold mb-2">Noch keine Sets im Inventar</h2>
          <p className="text-text-muted text-sm">Kaufe Sets über den Deal Checker oder füge sie manuell hinzu.</p>
        </div>
      ) : (
        <div className="space-y-3">
          {items.map((item) => (
            <div
              key={item.id}
              className={`bg-bg-card border rounded-xl p-4 ${
                item.sell_signal_active ? "border-sell-signal/50" : "border-border"
              }`}
            >
              <div className="flex items-start justify-between gap-4">
                <div className="flex-1 min-w-0">
                  <div className="flex items-center gap-2 mb-1">
                    <span className="text-lego-yellow font-[family-name:var(--font-mono)] text-sm font-semibold">
                      {item.set_number}
                    </span>
                    {item.sell_signal_active && (
                      <span className="bg-sell-signal/20 text-sell-signal text-xs px-2 py-0.5 rounded-full animate-pulse font-medium">
                        SELL
                      </span>
                    )}
                  </div>
                  <h3 className="text-text-primary text-sm font-medium truncate">{item.set_name}</h3>
                  <div className="flex gap-4 mt-2 text-xs text-text-muted">
                    <span>Gekauft: {new Date(item.buy_date).toLocaleDateString("de-DE")}</span>
                    <span>{item.holding_days} Tage</span>
                    {item.buy_platform && <span>{item.buy_platform}</span>}
                  </div>
                  {item.sell_signal_active && item.sell_signal_reason && (
                    <p className="text-sell-signal text-xs mt-2">{item.sell_signal_reason}</p>
                  )}
                </div>

                <div className="text-right shrink-0">
                  <div className="text-text-muted text-xs">Kaufpreis</div>
                  <div className="text-text-primary font-[family-name:var(--font-mono)] font-semibold">
                    {item.total_invested.toFixed(0)}€
                  </div>
                  {item.current_market_price && (
                    <>
                      <div className="text-text-muted text-xs mt-2">Marktwert</div>
                      <div className="text-lego-yellow font-[family-name:var(--font-mono)] font-semibold">
                        {item.current_market_price.toFixed(0)}€
                      </div>
                      <div className={`font-[family-name:var(--font-mono)] text-sm font-bold ${profitColor(item.unrealized_profit)}`}>
                        {item.unrealized_profit > 0 ? "+" : ""}{item.unrealized_profit?.toFixed(0)}€
                        <span className="text-xs ml-1">({item.unrealized_roi_percent?.toFixed(1)}%)</span>
                      </div>
                    </>
                  )}
                </div>
              </div>

              {/* Actions */}
              <div className="flex gap-2 mt-3 pt-3 border-t border-border/50">
                <button
                  onClick={() => openEdit(item)}
                  className="bg-lego-blue/10 text-lego-blue text-xs px-3 py-1.5 rounded-lg hover:bg-lego-blue/20 transition-colors"
                >
                  Bearbeiten
                </button>
                <button
                  onClick={() => { setSellModal(item); setSellPrice(""); setSellPlatform(""); }}
                  className="bg-go-star/10 text-go-star text-xs px-3 py-1.5 rounded-lg hover:bg-go-star/20 transition-colors"
                >
                  Verkauft
                </button>
                <button
                  onClick={() => { if (confirm(`${item.set_name} entfernen?`)) deleteMutation.mutate(item.id); }}
                  className="bg-no-go/10 text-no-go text-xs px-3 py-1.5 rounded-lg hover:bg-no-go/20 transition-colors"
                >
                  Entfernen
                </button>
              </div>
            </div>
          ))}
        </div>
      )}

      {/* Sell Modal */}
      {sellModal && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 backdrop-blur-sm">
          <div className="bg-bg-card border border-border rounded-xl p-6 w-full max-w-md mx-4">
            <h2 className="text-text-primary text-lg font-bold mb-4">Verkauft markieren</h2>
            <div className="text-text-muted text-sm mb-4">
              <span className="text-lego-yellow font-[family-name:var(--font-mono)]">{sellModal.set_number}</span> — {sellModal.set_name}
            </div>
            <div className="space-y-3">
              <div>
                <label className="block text-text-muted text-xs mb-1">Verkaufspreis (€)</label>
                <input
                  type="number"
                  step="0.01"
                  value={sellPrice}
                  onChange={(e) => setSellPrice(e.target.value)}
                  autoFocus
                  className="w-full bg-bg-primary border border-border rounded-lg px-3 py-2 text-text-primary font-[family-name:var(--font-mono)]"
                />
              </div>
              <div>
                <label className="block text-text-muted text-xs mb-1">Verkaufsdatum</label>
                <input
                  type="date"
                  value={sellDate}
                  onChange={(e) => setSellDate(e.target.value)}
                  className="w-full bg-bg-primary border border-border rounded-lg px-3 py-2 text-text-primary text-sm"
                />
              </div>
              <div>
                <label className="block text-text-muted text-xs mb-1">Plattform</label>
                <input
                  type="text"
                  value={sellPlatform}
                  onChange={(e) => setSellPlatform(e.target.value)}
                  placeholder="z.B. eBay"
                  className="w-full bg-bg-primary border border-border rounded-lg px-3 py-2 text-text-primary text-sm"
                />
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

      {/* Edit Modal */}
      {editModal && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 backdrop-blur-sm">
          <div className="bg-bg-card border border-border rounded-xl p-6 w-full max-w-md mx-4">
            <h2 className="text-text-primary text-lg font-bold mb-2">Set bearbeiten</h2>
            <p className="text-lego-yellow font-[family-name:var(--font-mono)] text-sm mb-4">{editModal.set_number}</p>
            <form onSubmit={handleEdit} className="space-y-3">
              <div>
                <label className="block text-text-muted text-xs mb-1">Set-Name</label>
                <input type="text" value={editForm.set_name} onChange={(e) => setEditForm({ ...editForm, set_name: e.target.value })} required className="w-full bg-bg-primary border border-border rounded-lg px-3 py-2 text-text-primary text-sm" />
              </div>
              <div>
                <label className="block text-text-muted text-xs mb-1">Theme</label>
                <input type="text" value={editForm.theme} onChange={(e) => setEditForm({ ...editForm, theme: e.target.value })} className="w-full bg-bg-primary border border-border rounded-lg px-3 py-2 text-text-primary text-sm" />
              </div>
              <div className="grid grid-cols-2 gap-3">
                <div>
                  <label className="block text-text-muted text-xs mb-1">Kaufpreis (€)</label>
                  <input type="number" step="0.01" value={editForm.buy_price} onChange={(e) => setEditForm({ ...editForm, buy_price: e.target.value })} required className="w-full bg-bg-primary border border-border rounded-lg px-3 py-2 text-text-primary text-sm font-[family-name:var(--font-mono)]" />
                </div>
                <div>
                  <label className="block text-text-muted text-xs mb-1">Versand (€)</label>
                  <input type="number" step="0.01" value={editForm.buy_shipping} onChange={(e) => setEditForm({ ...editForm, buy_shipping: e.target.value })} className="w-full bg-bg-primary border border-border rounded-lg px-3 py-2 text-text-primary text-sm font-[family-name:var(--font-mono)]" />
                </div>
              </div>
              <div className="grid grid-cols-2 gap-3">
                <div>
                  <label className="block text-text-muted text-xs mb-1">Kaufdatum</label>
                  <input type="date" value={editForm.buy_date} onChange={(e) => setEditForm({ ...editForm, buy_date: e.target.value })} className="w-full bg-bg-primary border border-border rounded-lg px-3 py-2 text-text-primary text-sm" />
                </div>
                <div>
                  <label className="block text-text-muted text-xs mb-1">Plattform</label>
                  <input type="text" value={editForm.buy_platform} onChange={(e) => setEditForm({ ...editForm, buy_platform: e.target.value })} className="w-full bg-bg-primary border border-border rounded-lg px-3 py-2 text-text-primary text-sm" />
                </div>
              </div>
              <div>
                <label className="block text-text-muted text-xs mb-1">Zustand</label>
                <select value={editForm.condition} onChange={(e) => setEditForm({ ...editForm, condition: e.target.value })} className="w-full bg-bg-primary border border-border rounded-lg px-3 py-2 text-text-primary text-sm">
                  <option value="NEW_SEALED">Neu & Versiegelt</option>
                  <option value="NEW_OPEN">Neu & Geöffnet</option>
                  <option value="USED_COMPLETE">Gebraucht (komplett)</option>
                  <option value="USED_INCOMPLETE">Gebraucht (unvollständig)</option>
                </select>
              </div>
              <div>
                <label className="block text-text-muted text-xs mb-1">Notizen</label>
                <textarea value={editForm.notes} onChange={(e) => setEditForm({ ...editForm, notes: e.target.value })} placeholder="Eigene Notizen..." rows={2} className="w-full bg-bg-primary border border-border rounded-lg px-3 py-2 text-text-primary text-sm resize-none" />
              </div>
              <div className="flex gap-3 mt-4">
                <button type="button" onClick={() => setEditModal(null)} className="flex-1 bg-bg-hover text-text-secondary py-2 rounded-lg">Abbrechen</button>
                <button type="submit" disabled={editMutation.isPending} className="flex-1 bg-lego-yellow text-black font-bold py-2 rounded-lg disabled:opacity-50">
                  {editMutation.isPending ? "..." : "Speichern"}
                </button>
              </div>
              {editMutation.isError && (
                <p className="text-no-go text-sm">{editMutation.error.message}</p>
              )}
            </form>
          </div>
        </div>
      )}

      {/* Add Modal */}
      {addModal && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 backdrop-blur-sm">
          <div className="bg-bg-card border border-border rounded-xl p-6 w-full max-w-md mx-4">
            <h2 className="text-text-primary text-lg font-bold mb-4">Set manuell hinzufügen</h2>
            <form onSubmit={handleAdd} className="space-y-3">
              <div className="relative">
                <input type="text" placeholder="Set-Nummer *" value={addForm.set_number} onChange={(e) => handleSetNumberChange(e.target.value)} required className="w-full bg-bg-primary border border-border rounded-lg px-3 py-2 text-text-primary text-sm font-[family-name:var(--font-mono)]" />
                {lookupLoading && (
                  <span className="absolute right-3 top-1/2 -translate-y-1/2 text-lego-yellow text-xs animate-pulse">Suche...</span>
                )}
              </div>
              <input type="text" placeholder="Set-Name *" value={addForm.set_name} onChange={(e) => setAddForm({ ...addForm, set_name: e.target.value })} required className="w-full bg-bg-primary border border-border rounded-lg px-3 py-2 text-text-primary text-sm" />
              <input type="text" placeholder="Theme" value={addForm.theme} onChange={(e) => setAddForm({ ...addForm, theme: e.target.value })} className="w-full bg-bg-primary border border-border rounded-lg px-3 py-2 text-text-primary text-sm" />
              <div className="grid grid-cols-2 gap-3">
                <input type="number" step="0.01" placeholder="Kaufpreis € *" value={addForm.buy_price} onChange={(e) => setAddForm({ ...addForm, buy_price: e.target.value })} required className="w-full bg-bg-primary border border-border rounded-lg px-3 py-2 text-text-primary text-sm font-[family-name:var(--font-mono)]" />
                <input type="number" step="0.01" placeholder="Versand €" value={addForm.buy_shipping} onChange={(e) => setAddForm({ ...addForm, buy_shipping: e.target.value })} className="w-full bg-bg-primary border border-border rounded-lg px-3 py-2 text-text-primary text-sm font-[family-name:var(--font-mono)]" />
              </div>
              <div className="grid grid-cols-2 gap-3">
                <input type="date" value={addForm.buy_date} onChange={(e) => setAddForm({ ...addForm, buy_date: e.target.value })} className="w-full bg-bg-primary border border-border rounded-lg px-3 py-2 text-text-primary text-sm" />
                <input type="text" placeholder="Plattform" value={addForm.buy_platform} onChange={(e) => setAddForm({ ...addForm, buy_platform: e.target.value })} className="w-full bg-bg-primary border border-border rounded-lg px-3 py-2 text-text-primary text-sm" />
              </div>
              <div className="flex gap-3 mt-4">
                <button type="button" onClick={() => setAddModal(false)} className="flex-1 bg-bg-hover text-text-secondary py-2 rounded-lg">Abbrechen</button>
                <button type="submit" disabled={addMutation.isPending} className="flex-1 bg-lego-yellow text-black font-bold py-2 rounded-lg disabled:opacity-50">
                  {addMutation.isPending ? "..." : "Hinzufügen"}
                </button>
              </div>
            </form>
          </div>
        </div>
      )}
    </div>
  );
}
