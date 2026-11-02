import { useEffect, useRef, useState } from "react";
import { api } from "../../api/client";
import ListingBadges from "../ListingBadges";
import MarketAge from "../MarketAge";
import PriceBasis from "../PriceBasis";
import { useToast } from "../toastContext";
import { referenceLinks } from "../../pages/inventoryLinks";
import { openListing, prepareListingWithToast, RUNNING_STATUSES } from "../../pages/listingPrep";
import {
  BOX_ICON,
  formatMoney,
  LINK_ICON,
  MAX_PREVIEW_PHOTOS,
  PHOTO_ICON,
  profitColor,
  SELL_PLATFORM_QUICK_PICKS,
} from "./inventoryFormat";

// Liste der Posten im Bestand: Ladezustand, leere Zustaende, Karten mit Aktionen.
// Zustand und Mutationen haelt die Seite (pages/Inventar.jsx).

function SellDropdown({ item, onPrepared, onMarkSold }) {
  const toast = useToast();
  const [open, setOpen] = useState(false);
  const [pendingPlatform, setPendingPlatform] = useState(null);
  const ref = useRef(null);

  useEffect(() => {
    const handler = (e) => {
      if (ref.current && !ref.current.contains(e.target)) setOpen(false);
    };
    document.addEventListener("mousedown", handler);
    return () => document.removeEventListener("mousedown", handler);
  }, []);

  // Ein Klick: Entwurf mit Text und Preisvorschlag anlegen (oder den
  // vorhandenen nehmen), Text in die Zwischenablage, Listing-Fenster auf.
  // Eingestellt wird von Hand (ADR 0002); dort reicht dann die URL.
  const prepare = async (platform) => {
    setOpen(false);
    setPendingPlatform(platform);
    try {
      if (await prepareListingWithToast(item.id, platform, toast)) onPrepared();
    } finally {
      setPendingPlatform(null);
    }
  };

  const options = SELL_PLATFORM_QUICK_PICKS.map((p) => {
    const running = (item.listings || []).find((x) => x.platform === p.value && RUNNING_STATUSES.includes(x.status));
    const draft = openListing(item, p.value)?.status === "DRAFT";
    return { ...p, running: !!running, draft };
  });

  return (
    <div ref={ref} className="relative">
      <button
        onClick={() => setOpen(!open)}
        disabled={pendingPlatform !== null}
        aria-haspopup="menu"
        aria-expanded={open}
        className={`text-xs px-3 py-1.5 rounded-lg font-bold transition-colors bg-lego-yellow text-black hover:bg-lego-yellow/90 disabled:opacity-60 ${
          item.sell_signal_active ? "animate-pulse shadow-[0_0_8px_rgba(255,206,0,0.6)]" : ""
        }`}
      >
        {pendingPlatform ? "Bereite vor …" : "Verkaufen \u25BE"}
      </button>
      {open && (
        <div role="menu" className="absolute left-0 top-full mt-1 bg-bg-primary border border-border rounded-lg shadow-xl z-50 min-w-[240px]">
          {options.map((option) => (
            <button
              key={option.value}
              role="menuitem"
              disabled={option.running}
              onClick={() => prepare(option.value)}
              className="w-full text-left text-sm text-text-primary px-3 py-2 hover:bg-bg-hover first:rounded-t-lg transition-colors disabled:text-text-muted disabled:hover:bg-transparent"
            >
              {option.running
                ? `${option.label}: läuft schon`
                : option.draft
                  ? `${option.label}: Entwurf öffnen`
                  : `Inserat vorbereiten: ${option.label}`}
            </button>
          ))}
          <button
            role="menuitem"
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

export default function InventoryList({
  items,
  visibleItems,
  isLoading,
  filtersActive,
  onResetFilters,
  onEdit,
  onPrepared,
  onMarkSold,
  onOpenListings,
  onDelete,
  revalueMutation,
  splitMutation,
}) {
  return (
    <>
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
      ) : visibleItems.length === 0 ? (
        <div className="text-center py-12">
          <p className="text-text-primary text-sm font-medium mb-2">Kein Posten passt zu Filter und Suche.</p>
          {filtersActive && (
            <button
              type="button"
              onClick={onResetFilters}
              className="text-lego-yellow text-sm hover:underline"
            >
              Filter zurücksetzen
            </button>
          )}
        </div>
      ) : (
        <div className="space-y-3">
          {visibleItems.map((item) => (
            <div key={item.id} className={`bg-bg-card border rounded-xl p-4 ${item.sell_signal_active ? "border-sell-signal/50" : "border-border"}`}>
              <div className="flex items-start justify-between gap-4">
                <div className="flex-1 min-w-0">
                  <div className="flex items-center gap-2 mb-1">
                    <span className="text-lego-yellow font-[family-name:var(--font-mono)] text-sm font-semibold">{item.set_number ?? item.product_group}</span>
                    {item.quantity > 1 && <span className="bg-lego-blue/20 text-lego-blue text-xs px-1.5 py-0.5 rounded font-[family-name:var(--font-mono)] font-bold">x{item.quantity}</span>}
                    {item.photos?.length > 0 && <span className="bg-lego-blue/10 text-lego-blue text-xs px-2 py-0.5 rounded-full">{PHOTO_ICON} {item.photos.length}</span>}
                    {item.sell_signal_active && <span className="bg-sell-signal/20 text-sell-signal text-xs px-2 py-0.5 rounded-full animate-pulse font-medium">SELL</span>}
                  </div>
                  <h3 className="text-text-primary text-sm font-medium truncate">{item.set_name}</h3>
                  <div className="flex flex-wrap gap-4 mt-2 text-xs text-text-muted">
                    <span>Gekauft: {new Date(item.buy_date).toLocaleDateString("de-DE")}</span>
                    <span>{item.holding_days} Tage</span>
                    {item.buy_platform && <span>{item.buy_platform}</span>}
                    {item.storage_location && <span>{BOX_ICON} {item.storage_location}</span>}
                    {item.buy_url && <a href={item.buy_url} target="_blank" rel="noreferrer" className="text-lego-yellow hover:text-lego-yellow/80 transition-colors">Original-Link</a>}
                    {item.image_url && <a href={item.image_url} target="_blank" rel="noreferrer" className="text-lego-blue hover:text-lego-blue/80 transition-colors">Externer Foto-Link</a>}
                    {referenceLinks(item).map((link) => (
                      <a
                        key={link.label}
                        href={link.href}
                        target="_blank"
                        rel="noopener noreferrer"
                        className="text-lego-yellow hover:underline"
                      >
                        {link.label} ↗
                      </a>
                    ))}
                  </div>
                  {item.notes && <p className="text-text-muted text-xs mt-2">{item.notes}</p>}
                  {item.sell_signal_active && item.sell_signal_reason && <p className="text-sell-signal text-xs mt-2">{item.sell_signal_reason}</p>}
                  <InventoryPhotoStrip item={item} />
                </div>
                <div className="text-right shrink-0">
                  <div className="text-text-muted text-xs">Kaufpreis</div>
                  <div className="text-text-primary font-[family-name:var(--font-mono)] font-semibold">
                    {item.total_invested != null ? formatMoney(item.total_invested) : "—"}
                  </div>
                  {item.current_market_price && (
                    <>
                      <div className="text-text-muted text-xs mt-2">Marktwert</div>
                      <div className={`text-lego-yellow font-[family-name:var(--font-mono)] font-semibold ${item.market_price_stale ? "opacity-60" : ""}`}>
                        <PriceBasis basis={item.market_price_basis}>{formatMoney(item.current_market_price)}</PriceBasis>
                      </div>
                      <MarketAge updatedAt={item.market_price_updated_at} stale={item.market_price_stale} />
                      {/* Ohne Kaufpreis (Dachbodenfund) gibt es keinen Gewinn -- nicht "+0 €" zeigen. */}
                      {item.unrealized_profit != null && (
                        <div className={`font-[family-name:var(--font-mono)] text-sm font-bold ${profitColor(item.unrealized_profit)} ${item.market_price_stale ? "opacity-60" : ""}`}>
                          {item.unrealized_profit > 0 ? "+" : ""}{formatMoney(item.unrealized_profit)}
                          {item.unrealized_roi_percent != null && (
                            <span className="text-xs ml-1">({item.unrealized_roi_percent.toFixed(1)}%)</span>
                          )}
                        </div>
                      )}
                    </>
                  )}
                  <div className="mt-2 flex justify-end">
                    <ListingBadges item={item} />
                  </div>
                </div>
              </div>
              <div className="flex gap-2 mt-3 pt-3 border-t border-border/50">
                <button onClick={() => onEdit(item)} className="bg-lego-blue/10 text-lego-blue text-xs px-3 py-1.5 rounded-lg hover:bg-lego-blue/20 transition-colors">Bearbeiten</button>
                <SellDropdown
                  item={item}
                  onPrepared={() => onPrepared(item)}
                  onMarkSold={() => onMarkSold(item)}
                />
                <button onClick={() => onOpenListings(item)} className="bg-lego-yellow/10 text-lego-yellow text-xs px-3 py-1.5 rounded-lg hover:bg-lego-yellow/20 transition-colors">Listings</button>
                {item.item_type === "GENERIC" && item.search_query && (
                  <button
                    onClick={() => revalueMutation.mutate(item.id)}
                    disabled={revalueMutation.isPending && revalueMutation.variables === item.id}
                    className="bg-lego-blue/10 text-lego-blue text-xs px-3 py-1.5 rounded-lg hover:bg-lego-blue/20 transition-colors disabled:opacity-50"
                  >
                    {revalueMutation.isPending && revalueMutation.variables === item.id ? "..." : "Neu bewerten"}
                  </button>
                )}
                {item.quantity > 1 && (
                  <button
                    onClick={() => splitMutation.mutate(item.id)}
                    disabled={splitMutation.isPending}
                    className="bg-lego-blue/10 text-lego-blue text-xs px-3 py-1.5 rounded-lg hover:bg-lego-blue/20 transition-colors disabled:opacity-50"
                  >
                    Teilen
                  </button>
                )}
                <button onClick={() => { if (confirm(`${item.set_name} entfernen?`)) onDelete(item); }} className="bg-no-go/10 text-no-go text-xs px-3 py-1.5 rounded-lg hover:bg-no-go/20 transition-colors">Entfernen</button>
              </div>
              {revalueMutation.isError && revalueMutation.variables === item.id && (
                <p className="text-no-go text-xs mt-2">{revalueMutation.error.message}</p>
              )}
            </div>
          ))}
        </div>
      )}
    </>
  );
}
