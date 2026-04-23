import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../api/client";

const EURO = "\u20ac";

function formatMoney(value, digits = 0) {
  if (value == null) return "--";
  return `${Number(value).toFixed(digits)}${EURO}`;
}

function formatStamp(value) {
  if (!value) return "Noch nie";
  return new Date(value).toLocaleString("de-DE", { dateStyle: "short", timeStyle: "short" });
}

export default function AuctionWatch() {
  const queryClient = useQueryClient();
  const [selectedPlatform, setSelectedPlatform] = useState("CATAWIKI");
  const [categoryUrls, setCategoryUrls] = useState("");
  const [maxResults, setMaxResults] = useState("20");

  const { data: items = [], isLoading } = useQuery({
    queryKey: ["auction-watch"],
    queryFn: api.listAuctionWatch,
    refetchInterval: 60_000,
  });

  const { data: scanSettings = [] } = useQuery({
    queryKey: ["settings", "auction-sources"],
    queryFn: () => api.listSettings(),
    staleTime: 60_000,
  });

  const refreshMutation = useMutation({
    mutationFn: (id) => api.refreshAuctionWatch(id),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["auction-watch"] }),
  });

  const removeMutation = useMutation({
    mutationFn: (id) => api.removeAuctionWatch(id),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["auction-watch"] }),
  });

  const discoverMutation = useMutation({
    mutationFn: (data) => api.discoverAuctions(data),
  });

  const addMutation = useMutation({
    mutationFn: (data) => api.addAuctionWatch(data),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["auction-watch"] }),
  });

  const settingsMap = Object.fromEntries(scanSettings.map((setting) => [setting.key, setting.value]));
  const scanUrlKey = `${selectedPlatform.toLowerCase()}_scan_urls`;
  const hasConfiguredScanUrls = Boolean(settingsMap[scanUrlKey] && settingsMap[scanUrlKey].trim());
  const placeholderByPlatform = {
    CATAWIKI: "https://www.catawiki.com/de/c/708-lego\r\nhttps://www.catawiki.com/de/c/714-vintage-toys",
    WHATNOT: "https://www.whatnot.com/category/toys\r\nhttps://www.whatnot.com/search?query=lego",
    BRICKLINK: "https://www.bricklink.com/v2/search.page?q=lego%2075313\r\nhttps://store.bricklink.com/",
  };

  const handleDiscover = () => {
    const trimmedUrls = categoryUrls
      .split("\n")
      .map((line) => line.trim())
      .filter(Boolean);

    discoverMutation.mutate({
      source_platform: selectedPlatform,
      category_urls: trimmedUrls,
      max_results_per_url: Math.max(1, parseInt(maxResults || "20", 10) || 20),
    });
  };

  const handleAddDiscovery = (item) => {
    addMutation.mutate({
      set_number: item.set_number,
      source_url: item.source_url,
      source_platform: item.source_platform || "CATAWIKI",
      lot_title: item.lot_title,
      current_bid: item.current_bid,
      purchase_shipping: item.purchase_shipping || 0,
    });
  };

  if (isLoading) {
    return (
      <div className="flex items-center justify-center py-20">
        <div className="h-8 w-8 border-2 border-lego-yellow border-t-transparent rounded-full animate-spin" />
      </div>
    );
  }

  return (
    <div>
      <div className="flex items-center justify-between mb-6">
        <div>
          <h1 className="text-2xl font-bold text-text-primary">Auktions-Watchlist</h1>
          <p className="text-text-muted text-sm mt-1">
            Konkrete Lots mit Maximalgebot, Break-even und taeglichem Recheck.
          </p>
        </div>
        <div className="text-right">
          <div className="text-text-primary font-[family-name:var(--font-mono)] text-xl font-bold">{items.length}</div>
          <div className="text-text-muted text-xs">aktive Lots</div>
        </div>
      </div>

      <div className="bg-bg-card border border-border rounded-xl p-6 mb-6">
        <div className="flex items-start justify-between gap-4 mb-4">
          <div>
            <h2 className="text-text-primary text-lg font-semibold">Discovery-Scan</h2>
            <p className="text-text-muted text-sm mt-1">
              Holt neue Lots, bewertet sie direkt und zeigt, bis wohin sich ein Gebot oder Kauf lohnt.
            </p>
          </div>
          <div className="flex items-start gap-3">
            <select
              value={selectedPlatform}
              onChange={(event) => setSelectedPlatform(event.target.value)}
              className="bg-bg-primary border border-border rounded-lg px-3 py-2 text-text-primary text-sm"
            >
              <option value="CATAWIKI">Catawiki</option>
              <option value="WHATNOT">Whatnot</option>
              <option value="BRICKLINK">BrickLink</option>
            </select>
            <div className="text-right text-xs text-text-muted">
              <div>{hasConfiguredScanUrls ? "Konfiguriert" : "Noch nicht konfiguriert"}</div>
              <div>Scheduler nutzt die URLs aus Einstellungen</div>
            </div>
          </div>
        </div>

        <div className="grid gap-3 md:grid-cols-[1fr_180px]">
          <div>
            <label className="block text-text-muted text-xs mb-1">Kategorie-URLs (optional, eine pro Zeile)</label>
            <textarea
              rows={4}
              value={categoryUrls}
              onChange={(event) => setCategoryUrls(event.target.value)}
              placeholder={
                hasConfiguredScanUrls
                  ? `Leer lassen, um die in Einstellungen gespeicherten ${selectedPlatform}-URLs zu nutzen`
                  : placeholderByPlatform[selectedPlatform]
              }
              className="w-full bg-bg-primary border border-border rounded-lg px-3 py-3 text-text-primary text-sm placeholder:text-text-muted"
            />
          </div>
          <div>
            <label className="block text-text-muted text-xs mb-1">Max Ergebnisse pro URL</label>
            <input
              type="number"
              min="1"
              max="100"
              value={maxResults}
              onChange={(event) => setMaxResults(event.target.value)}
              className="w-full bg-bg-primary border border-border rounded-lg px-3 py-3 text-text-primary font-[family-name:var(--font-mono)]"
            />
            <button
              type="button"
              onClick={handleDiscover}
              disabled={discoverMutation.isPending || (!categoryUrls.trim() && !hasConfiguredScanUrls)}
              className="w-full mt-3 px-4 py-3 rounded-lg bg-lego-blue text-white font-bold hover:bg-lego-blue/90 transition-colors disabled:opacity-50"
            >
              {discoverMutation.isPending ? "Scanne..." : "Neue Lots scannen"}
            </button>
          </div>
        </div>

        {discoverMutation.isError && (
          <p className="text-no-go text-sm mt-3">Fehler: {discoverMutation.error.message}</p>
        )}
        {addMutation.isError && (
          <p className="text-no-go text-sm mt-3">Watchlist-Fehler: {addMutation.error.message}</p>
        )}
        {addMutation.isSuccess && (
          <p className="text-go text-sm mt-3">Lot zur Watchlist hinzugefuegt.</p>
        )}

        {discoverMutation.data && (
          <div className="mt-4 border border-border rounded-xl overflow-hidden">
            <div className="px-4 py-3 bg-bg-hover border-b border-border flex items-center justify-between">
              <h3 className="text-text-primary font-medium">Gefundene Lots</h3>
              <span className="text-text-muted text-xs">{discoverMutation.data.length} Treffer</span>
            </div>
            {discoverMutation.data.length === 0 ? (
              <div className="p-4 text-sm text-text-muted">
                Kein passendes Lot gefunden oder aktuell liegt nichts im Zielkorridor.
              </div>
            ) : (
              <div className="divide-y divide-border">
                {discoverMutation.data.map((item) => (
                  <div key={item.source_url} className="p-4">
                    <div className="flex items-start justify-between gap-4">
                      <div className="min-w-0">
                        <div className="flex items-center gap-2 flex-wrap">
                          <span className="text-lego-yellow font-[family-name:var(--font-mono)] text-sm">
                            {item.set_number || "--"}
                          </span>
                          <span
                            className={`px-2 py-1 rounded-full text-xs font-medium ${
                              item.can_bid_now ? "bg-go/15 text-go" : "bg-no-go/15 text-no-go"
                            }`}
                          >
                            {item.can_bid_now ? "Unter Limit" : "Zu teuer"}
                          </span>
                        </div>
                        <div className="text-text-primary font-medium mt-1">{item.lot_title}</div>
                        <div className="text-text-muted text-sm mt-1">
                          Aktuell {formatMoney(item.current_bid)} - Max {formatMoney(item.recommended_max_bid)} - ROI{" "}
                          {item.expected_roi_current != null ? `${item.expected_roi_current.toFixed(1)}%` : "--"}
                        </div>
                        {item.recommendation_text && (
                          <div className="text-text-secondary text-sm mt-2">{item.recommendation_text}</div>
                        )}
                        {item.warning_text && <div className="text-check text-sm mt-1">{item.warning_text}</div>}
                      </div>
                      <div className="flex flex-col items-end gap-2 shrink-0">
                        <a
                          href={item.source_url}
                          target="_blank"
                          rel="noreferrer"
                          className="text-lego-yellow text-sm hover:underline"
                        >
                          Lot oeffnen
                        </a>
                        <button
                          type="button"
                          onClick={() => handleAddDiscovery(item)}
                          disabled={addMutation.isPending || !item.set_number || item.current_bid == null}
                          className="px-3 py-2 rounded-lg bg-lego-yellow text-black text-sm font-bold hover:bg-lego-yellow/90 transition-colors disabled:opacity-50"
                        >
                          Beobachten
                        </button>
                      </div>
                    </div>
                  </div>
                ))}
              </div>
            )}
          </div>
        )}
      </div>

      {items.length === 0 ? (
        <div className="bg-bg-card border border-border rounded-xl p-8 text-center">
          <h2 className="text-text-primary text-lg font-semibold mb-2">Noch keine beobachteten Auktionen</h2>
          <p className="text-text-muted text-sm">
            Im Deal-Checker ein Catawiki-Lot berechnen und dann zur Watchlist hinzufuegen.
          </p>
        </div>
      ) : (
        <div className="grid grid-cols-1 xl:grid-cols-2 gap-4">
          {items.map((item) => (
            <div key={item.id} className="bg-bg-card border border-border rounded-xl p-5">
              <div className="flex items-start justify-between gap-4">
                <div>
                  <div className="flex items-center gap-2">
                    <span className="text-lego-yellow font-[family-name:var(--font-mono)] text-sm">
                      {item.set_number}
                    </span>
                    <span
                      className={`px-2 py-1 rounded-full text-xs font-medium ${
                        (item.bid_gap || 0) >= 0 ? "bg-go/15 text-go" : "bg-no-go/15 text-no-go"
                      }`}
                    >
                      {item.bid_status || item.status}
                    </span>
                  </div>
                  <h2 className="text-text-primary font-semibold mt-1">{item.set_name}</h2>
                  <p className="text-text-muted text-sm mt-1">
                    {item.source_platform} - letzter Check {formatStamp(item.last_checked_at)}
                  </p>
                </div>
                <a
                  href={item.source_url}
                  target="_blank"
                  rel="noreferrer"
                  className="text-lego-yellow text-sm hover:underline shrink-0"
                >
                  Lot oeffnen
                </a>
              </div>

              <div className="grid grid-cols-2 md:grid-cols-4 gap-3 mt-4">
                <div className="bg-bg-hover rounded-lg p-3">
                  <div className="text-text-muted text-xs uppercase">Aktuell</div>
                  <div className="text-text-primary font-[family-name:var(--font-mono)] font-bold">
                    {formatMoney(item.current_bid)}
                  </div>
                </div>
                <div className="bg-bg-hover rounded-lg p-3">
                  <div className="text-text-muted text-xs uppercase">Maximalgebot</div>
                  <div className="text-go-star font-[family-name:var(--font-mono)] font-bold">
                    {formatMoney(item.max_bid)}
                  </div>
                </div>
                <div className="bg-bg-hover rounded-lg p-3">
                  <div className="text-text-muted text-xs uppercase">Luft</div>
                  <div
                    className={`font-[family-name:var(--font-mono)] font-bold ${
                      (item.bid_gap || 0) >= 0 ? "text-go" : "text-no-go"
                    }`}
                  >
                    {formatMoney(item.bid_gap)}
                  </div>
                </div>
                <div className="bg-bg-hover rounded-lg p-3">
                  <div className="text-text-muted text-xs uppercase">ROI jetzt</div>
                  <div
                    className={`font-[family-name:var(--font-mono)] font-bold ${
                      (item.expected_roi_current || 0) >= 0 ? "text-go" : "text-no-go"
                    }`}
                  >
                    {item.expected_roi_current != null ? `${item.expected_roi_current.toFixed(1)}%` : "--"}
                  </div>
                </div>
              </div>

              <div className="grid grid-cols-2 gap-3 mt-3">
                <div className="bg-bg-hover rounded-lg p-3">
                  <div className="text-text-muted text-xs uppercase">All-in jetzt</div>
                  <div className="text-text-primary font-[family-name:var(--font-mono)] font-bold">
                    {formatMoney(item.all_in_cost_current)}
                  </div>
                </div>
                <div className="bg-bg-hover rounded-lg p-3">
                  <div className="text-text-muted text-xs uppercase">Break-even</div>
                  <div className="text-text-primary font-[family-name:var(--font-mono)] font-bold">
                    {formatMoney(item.break_even_bid)}
                  </div>
                </div>
              </div>

              {item.recommendation_text && (
                <p className="text-text-secondary text-sm mt-4">{item.recommendation_text}</p>
              )}
              {item.warning_text && <p className="text-check text-sm mt-2">{item.warning_text}</p>}

              <div className="flex items-center gap-3 mt-4">
                <button
                  type="button"
                  onClick={() => refreshMutation.mutate(item.id)}
                  disabled={refreshMutation.isPending}
                  className="px-4 py-2 rounded-lg bg-lego-blue text-white text-sm font-medium hover:bg-lego-blue/90 transition-colors disabled:opacity-50"
                >
                  Neu bewerten
                </button>
                <button
                  type="button"
                  onClick={() => removeMutation.mutate(item.id)}
                  disabled={removeMutation.isPending}
                  className="text-no-go text-sm hover:underline disabled:opacity-50"
                >
                  Entfernen
                </button>
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
