import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../api/client";

const PLATFORMS = ["KLEINANZEIGEN", "EBAY"];
const PLATFORM_LABELS = { KLEINANZEIGEN: "Kleinanzeigen", EBAY: "eBay" };
const STATUS_LABELS = { ACTIVE: "aktiv", PAUSED: "pausiert", ENDED: "beendet", SOLD: "verkauft", DRAFT: "Entwurf" };

function formatDate(value) {
  return value ? new Date(value).toLocaleDateString("de-DE") : "—";
}

function ActivateForm({ itemId, platform, onDone }) {
  const [price, setPrice] = useState("");
  const [listedAt, setListedAt] = useState(new Date().toISOString().split("T")[0]);
  const [url, setUrl] = useState("");
  const [minPrice, setMinPrice] = useState("");
  const [error, setError] = useState(null);

  const create = useMutation({
    mutationFn: () =>
      api.createListing(itemId, {
        platform,
        current_price: Number(price),
        listed_at: listedAt,
        url: url.trim() || null,
        min_price: minPrice === "" ? null : Number(minPrice),
      }),
    onSuccess: onDone,
    onError: (err) => setError(err.message),
  });

  const suggestedMin = price ? Math.round(Number(price) * 0.7 * 100) / 100 : null;

  return (
    <form
      className="space-y-2"
      onSubmit={(e) => {
        e.preventDefault();
        create.mutate();
      }}
    >
      <div className="grid grid-cols-2 gap-2">
        <label className="text-xs text-text-secondary">
          Preis (€)*
          <input type="number" step="0.01" min="0.01" required value={price} onChange={(e) => setPrice(e.target.value)}
            className="w-full bg-bg-primary border border-border rounded px-2 py-1 text-sm text-text-primary" />
        </label>
        <label className="text-xs text-text-secondary">
          Eingestellt am
          <input type="date" value={listedAt} onChange={(e) => setListedAt(e.target.value)}
            className="w-full bg-bg-primary border border-border rounded px-2 py-1 text-sm text-text-primary" />
        </label>
      </div>
      <label className="text-xs text-text-secondary block">
        URL zur Anzeige (aus der Zwischenablage einfuegen)
        <input type="url" value={url} onChange={(e) => setUrl(e.target.value)} placeholder="https://…"
          className="w-full bg-bg-primary border border-border rounded px-2 py-1 text-sm text-text-primary" />
      </label>
      <label className="text-xs text-text-secondary block">
        Schmerzgrenze (€) — darunter schlaegt nichts vor
        <input type="number" step="0.01" min="0.01" value={minPrice} onChange={(e) => setMinPrice(e.target.value)}
          placeholder={suggestedMin ? `Vorschlag: ${suggestedMin}` : ""}
          className="w-full bg-bg-primary border border-border rounded px-2 py-1 text-sm text-text-primary" />
      </label>
      {error && <p className="text-xs text-no-go">{error}</p>}
      <button type="submit" disabled={create.isPending}
        className="text-xs px-3 py-1.5 rounded-lg font-bold bg-lego-yellow text-black hover:bg-lego-yellow/90">
        Als eingestellt markieren
      </button>
    </form>
  );
}

function OpenListing({ itemId, listing, onDone }) {
  const [priceDraft, setPriceDraft] = useState("");
  const [error, setError] = useState(null);
  const patch = useMutation({
    mutationFn: (data) => api.updateListing(itemId, listing.id, data),
    onSuccess: () => {
      setError(null);
      onDone();
    },
    onError: (err) => setError(err.message),
  });
  const end = useMutation({
    mutationFn: () => api.endListing(itemId, listing.id),
    onSuccess: () => {
      setError(null);
      onDone();
    },
    onError: (err) => setError(err.message),
  });

  return (
    <div className="space-y-2">
      <div className="flex items-center justify-between text-sm text-text-primary">
        <span>
          {Math.round(listing.current_price)}€ {listing.price_type === "VB" ? "VB" : ""} · {STATUS_LABELS[listing.status]}
          {" seit "}{formatDate(listing.listed_at)}
          {listing.at_floor && <span className="text-no-go"> · an der Schmerzgrenze</span>}
        </span>
        {listing.url && (
          <a href={listing.url} target="_blank" rel="noreferrer" className="text-xs text-lego-yellow hover:underline">
            Anzeige {"↗"}
          </a>
        )}
      </div>
      <div className="flex gap-2 items-center">
        <input type="number" step="0.01" placeholder="Neuer Preis" value={priceDraft}
          onChange={(e) => setPriceDraft(e.target.value)}
          className="w-28 bg-bg-primary border border-border rounded px-2 py-1 text-sm text-text-primary" />
        <button onClick={() => priceDraft && patch.mutate({ current_price: Number(priceDraft) })}
          className="text-xs px-2 py-1 rounded bg-bg-hover text-text-primary border border-border">Preis aendern</button>
        <button onClick={() => patch.mutate({ status: listing.status === "PAUSED" ? "ACTIVE" : "PAUSED" })}
          className="text-xs px-2 py-1 rounded bg-bg-hover text-text-primary border border-border">
          {listing.status === "PAUSED" ? "Fortsetzen" : "Pausieren"}
        </button>
        <button onClick={() => end.mutate()}
          className="text-xs px-2 py-1 rounded bg-bg-hover text-no-go border border-border">Beendet/geloescht</button>
      </div>
      {error && <p className="text-xs text-no-go">{error}</p>}
      {listing.price_changes.length > 0 && (
        <p className="text-xs text-text-secondary">
          {listing.price_changes.map((c) => `${formatDate(c.changed_at)}: ${Math.round(c.old_price)}→${Math.round(c.new_price)}€`).join(" · ")}
        </p>
      )}
    </div>
  );
}

export default function ListingManager({ item, onClose, onChanged }) {
  const queryClient = useQueryClient();
  const { data: listings = [] } = useQuery({
    queryKey: ["listings", item.id],
    queryFn: () => api.listListings(item.id),
  });

  const refresh = () => {
    queryClient.invalidateQueries({ queryKey: ["listings", item.id] });
    onChanged();
  };

  const history = listings.filter((x) => x.status === "ENDED" || x.status === "SOLD");

  return (
    <div className="fixed inset-0 bg-black/60 flex items-center justify-center z-50 p-4" onClick={onClose}>
      <div className="bg-bg-card border border-border rounded-xl p-4 w-full max-w-lg max-h-[85vh] overflow-y-auto"
        onClick={(e) => e.stopPropagation()}>
        <div className="flex justify-between items-center mb-3">
          <h3 className="font-bold text-text-primary">Listings — {item.set_name}</h3>
          <button onClick={onClose} className="text-text-secondary hover:text-text-primary">✕</button>
        </div>
        {item.status === "SOLD" && (
          <p className="text-xs text-no-go mb-2">
            Artikel ist verkauft — offene Anzeigen unten mit „Beendet/geloescht" abraeumen!
          </p>
        )}
        {PLATFORMS.map((platform) => {
          const open = listings.find(
            (x) => x.platform === platform && (x.status === "ACTIVE" || x.status === "PAUSED" || x.status === "DRAFT"),
          );
          return (
            <div key={platform} className="border-t border-border/50 py-3">
              <p className="text-sm font-bold text-text-primary mb-2">{PLATFORM_LABELS[platform]}</p>
              {open ? (
                <OpenListing itemId={item.id} listing={open} onDone={refresh} />
              ) : item.status !== "SOLD" ? (
                <ActivateForm itemId={item.id} platform={platform} onDone={refresh} />
              ) : (
                <p className="text-xs text-text-secondary">Artikel ist verkauft.</p>
              )}
            </div>
          );
        })}
        {history.length > 0 && (
          <div className="border-t border-border/50 pt-3 mt-1">
            <p className="text-xs font-bold text-text-secondary mb-1">Historie</p>
            {history.map((x) => (
              <p key={x.id} className="text-xs text-text-secondary">
                {PLATFORM_LABELS[x.platform]}: {formatDate(x.listed_at)} eingestellt
                {x.current_price != null && ` fuer ${Math.round(x.current_price)}€`} · {STATUS_LABELS[x.status]}
              </p>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}
