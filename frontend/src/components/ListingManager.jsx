import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../api/client";
import { copyListingText, downloadPhotos, PLATFORM_LABELS, platformCreateUrl } from "../pages/listingPrep";
import { useToast } from "./toastContext";

const PLATFORMS = ["KLEINANZEIGEN", "EBAY"];
const SMALL_BUTTON = "text-xs px-2 py-1 rounded bg-bg-hover text-text-primary border border-border hover:border-lego-yellow";
const INPUT = "w-full bg-bg-primary border border-border rounded px-2 py-1 text-sm text-text-primary";

function today() {
  return new Date().toISOString().split("T")[0];
}
const STATUS_LABELS = { ACTIVE: "aktiv", PAUSED: "pausiert", ENDED: "beendet", SOLD: "verkauft", DRAFT: "Entwurf" };

function formatDate(value) {
  return value ? new Date(value).toLocaleDateString("de-DE") : "—";
}

function ActivateForm({ itemId, platform, onDone }) {
  const [price, setPrice] = useState("");
  const [listedAt, setListedAt] = useState(today());
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

function DraftPanel({ item, listing, onDone }) {
  const toast = useToast();
  const [url, setUrl] = useState("");
  const [price, setPrice] = useState(listing.current_price != null ? String(listing.current_price) : "");
  const [listedAt, setListedAt] = useState(today());
  const [minPrice, setMinPrice] = useState("");
  const photoCount = item.photos?.length || 0;
  const platformLabel = PLATFORM_LABELS[listing.platform];

  const savePrice = useMutation({
    mutationFn: (value) => api.updateListing(item.id, listing.id, { current_price: value }),
    onSuccess: onDone,
    onError: (err) => toast.error(`Preis nicht gespeichert: ${err.message}`),
  });
  const refreshText = useMutation({
    mutationFn: () => api.refreshListingText(item.id, listing.id),
    onSuccess: () => {
      toast.success("Text neu geschrieben");
      onDone();
    },
    onError: (err) => toast.error(`Text nicht neu geschrieben: ${err.message}`),
  });
  const activate = useMutation({
    mutationFn: () =>
      api.activateListing(item.id, listing.id, {
        url: url.trim(),
        current_price: price === "" ? null : Number(price),
        listed_at: listedAt,
        min_price: minPrice === "" ? null : Number(minPrice),
      }),
    onSuccess: () => {
      toast.success(`${platformLabel}: Anzeige ist aktiv`);
      onDone();
    },
    onError: (err) => toast.error(err.message),
  });
  const discard = useMutation({
    mutationFn: () => api.deleteListing(item.id, listing.id),
    onSuccess: () => {
      toast.info("Entwurf verworfen");
      onDone();
    },
    onError: (err) => toast.error(err.message),
  });

  const handleCopy = async () => {
    if (await copyListingText(listing)) toast.success("Titel und Text kopiert");
    else toast.error("Kopieren hat der Browser verweigert — Text markieren und mit Strg+C kopieren");
  };

  const handlePhotos = () => {
    const count = downloadPhotos(item);
    if (count === 0) toast.warning("Keine Fotos hinterlegt — vor dem Einstellen unter „Bearbeiten“ ergänzen");
    else toast.info(`${count} Foto${count > 1 ? "s" : ""} werden geladen`);
  };

  const commitPrice = () => {
    const value = Number(price);
    if (price === "" || !(value > 0) || value === listing.current_price) return;
    savePrice.mutate(value);
  };

  const suggestedMin = price ? Math.round(Number(price) * 0.7 * 100) / 100 : null;
  const busy = activate.isPending || refreshText.isPending || discard.isPending;

  return (
    <div className="space-y-3">
      <div className="rounded-lg border border-lego-yellow/30 bg-lego-yellow/5 p-3 space-y-2">
        <p className="text-xs text-text-secondary">Entwurf · noch nicht eingestellt</p>
        <p className="text-sm font-bold text-text-primary select-all">{listing.title || "— kein Titel —"}</p>
        <p className="text-xs text-text-secondary whitespace-pre-wrap select-all">{listing.body}</p>
        <div className="flex flex-wrap items-center gap-2">
          <button type="button" onClick={handleCopy} className={SMALL_BUTTON}>Text kopieren</button>
          <button type="button" onClick={handlePhotos} className={SMALL_BUTTON}>
            Fotos laden ({photoCount})
          </button>
          <a href={platformCreateUrl(item, listing.platform)} target="_blank" rel="noreferrer" className={SMALL_BUTTON}>
            {platformLabel} öffnen {"↗"}
          </a>
          <button type="button" onClick={() => refreshText.mutate()} disabled={busy} className={`${SMALL_BUTTON} disabled:opacity-50`}>
            {refreshText.isPending ? "Claude schreibt …" : "Text neu"}
          </button>
        </div>
      </div>
      <form
        className="space-y-2"
        onSubmit={(e) => {
          e.preventDefault();
          activate.mutate();
        }}
      >
        <div className="grid grid-cols-[1fr_7rem] gap-2">
          <label className="text-xs text-text-secondary">
            Anzeigen-URL nach dem Einstellen*
            <input type="url" required value={url} onChange={(e) => setUrl(e.target.value)} placeholder="https://www.kleinanzeigen.de/s-anzeige/…"
              className={INPUT} />
          </label>
          <label className="text-xs text-text-secondary">
            Preis (€)*
            <input type="number" step="0.01" min="0.01" required value={price} onChange={(e) => setPrice(e.target.value)} onBlur={commitPrice}
              className={INPUT} />
          </label>
        </div>
        <details className="text-xs text-text-secondary">
          <summary className="cursor-pointer select-none">Datum und Schmerzgrenze</summary>
          <div className="grid grid-cols-2 gap-2 mt-2">
            <label>
              Eingestellt am
              <input type="date" value={listedAt} onChange={(e) => setListedAt(e.target.value)} className={INPUT} />
            </label>
            <label>
              Schmerzgrenze (€)
              <input type="number" step="0.01" min="0.01" value={minPrice} onChange={(e) => setMinPrice(e.target.value)}
                placeholder={suggestedMin ? `Vorschlag: ${suggestedMin}` : ""} className={INPUT} />
            </label>
          </div>
        </details>
        <div className="flex items-center gap-2">
          <button type="submit" disabled={busy}
            className="text-xs px-3 py-1.5 rounded-lg font-bold bg-lego-yellow text-black hover:bg-lego-yellow/90 disabled:opacity-50">
            {activate.isPending ? "…" : "Aktiv schalten"}
          </button>
          <button type="button" disabled={busy}
            onClick={() => { if (confirm("Entwurf verwerfen?")) discard.mutate(); }}
            className="text-xs px-2 py-1 rounded text-no-go hover:underline disabled:opacity-50">
            Entwurf verwerfen
          </button>
        </div>
      </form>
    </div>
  );
}

function PreparePanel({ item, platform, onDone }) {
  const toast = useToast();
  const [price, setPrice] = useState("");
  const prepare = useMutation({
    mutationFn: () => api.prepareListing(item.id, platform, price === "" ? null : Number(price)),
    onSuccess: async (result) => {
      if (result.warning) toast.warning(result.warning);
      const copied = await copyListingText(result.listing);
      toast.success(copied ? "Entwurf angelegt, Text kopiert" : "Entwurf angelegt");
      onDone();
    },
    onError: (err) => toast.error(err.message),
  });

  return (
    <div className="space-y-2">
      <form
        className="flex flex-wrap items-end gap-2"
        onSubmit={(e) => {
          e.preventDefault();
          prepare.mutate();
        }}
      >
        <label className="text-xs text-text-secondary">
          Preis (€), leer = Vorschlag
          <input type="number" step="0.01" min="0.01" value={price} onChange={(e) => setPrice(e.target.value)}
            className={`${INPUT} w-32`} />
        </label>
        <button type="submit" disabled={prepare.isPending}
          className="text-xs px-3 py-1.5 rounded-lg font-bold bg-lego-yellow text-black hover:bg-lego-yellow/90 disabled:opacity-50">
          {prepare.isPending ? "Claude schreibt …" : "Inserat vorbereiten"}
        </button>
      </form>
      <details className="text-xs text-text-secondary">
        <summary className="cursor-pointer select-none">Schon eingestellt? Direkt erfassen</summary>
        <div className="mt-2">
          <ActivateForm itemId={item.id} platform={platform} onDone={onDone} />
        </div>
      </details>
    </div>
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
          {listing.current_price != null ? `${Math.round(listing.current_price)}€` : "—"} {listing.price_type === "VB" ? "VB" : ""} · {STATUS_LABELS[listing.status]}
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
          // Ein DRAFT ist noch keine echte Anzeige: OpenListing (Preis aendern/
          // Pausieren) passt nur auf ACTIVE/PAUSED. Solange der Artikel nicht
          // verkauft ist, bekommt der DRAFT das DraftPanel (Text, Fotos, URL
          // einfuegen -> aktiv). Bleibt ein DRAFT auf einem SOLD-Artikel liegen
          // (über eine andere Plattform verkauft), zeigt weiter OpenListing,
          // damit "Beendet/geloescht" ihn wie jede andere offene Anzeige abräumt.
          const isDraft = open?.status === "DRAFT";
          return (
            <div key={platform} className="border-t border-border/50 py-3">
              <p className="text-sm font-bold text-text-primary mb-2">{PLATFORM_LABELS[platform]}</p>
              {open && (!isDraft || item.status === "SOLD") ? (
                <OpenListing itemId={item.id} listing={open} onDone={refresh} />
              ) : item.status !== "SOLD" ? (
                isDraft ? (
                  <DraftPanel item={item} listing={open} onDone={refresh} />
                ) : (
                  <PreparePanel item={item} platform={platform} onDone={refresh} />
                )
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
