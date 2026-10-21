import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../api/client";
import SuggestionActions from "../components/SuggestionActions";
import { useToast } from "../components/toastContext";
import { formatEuro } from "../lib/euro";

// "Fällige Anpassungen" (Spec Inventar, PR 3): was der tägliche Listing-Check
// vorschlägt. Ausgeführt wird von Hand auf der Plattform (ADR 0002).
const PLATFORM_LABELS = { KLEINANZEIGEN: "Kleinanzeigen", EBAY: "eBay" };

function ListingTitle({ entry }) {
  const { listing } = entry;
  const platform = PLATFORM_LABELS[listing.platform] || listing.platform;
  return (
    <div className="min-w-0">
      <p className="font-medium text-text-primary truncate">{entry.item_label}</p>
      <p className="text-xs text-text-secondary">
        {platform}
        {entry.days_online != null && ` · seit ${entry.days_online} ${entry.days_online === 1 ? "Tag" : "Tagen"} inseriert`}
        {listing.min_price != null && ` · Schmerzgrenze ${formatEuro(listing.min_price)}`}
      </p>
    </div>
  );
}

function AdLink({ url }) {
  if (!url) return <span className="text-xs text-text-muted">keine Anzeigen-URL</span>;
  return (
    <a href={url} target="_blank" rel="noreferrer" className="text-xs text-lego-yellow hover:underline whitespace-nowrap">
      Anzeige öffnen {"↗"}
    </a>
  );
}

function SuggestionCard({ entry }) {
  const { listing } = entry;
  return (
    <li className={`bg-bg-card border rounded-xl p-4 space-y-3 ${entry.snoozed ? "border-border opacity-70" : "border-lego-yellow/40"}`}>
      <div className="flex items-start justify-between gap-3">
        <ListingTitle entry={entry} />
        <AdLink url={listing.url} />
      </div>
      <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1">
        <span className="font-[family-name:var(--font-mono)] text-lg">
          <span className="text-text-secondary line-through">{formatEuro(listing.current_price)}</span>
          {" → "}
          <span className="text-go-star font-bold">{formatEuro(listing.suggested_price)}</span>
        </span>
        {entry.snoozed && listing.snoozed_until && (
          <span className="text-xs text-text-muted">
            zurückgestellt bis {new Date(listing.snoozed_until).toLocaleDateString("de-DE")}
          </span>
        )}
      </div>
      {listing.suggestion_reason && <p className="text-sm text-text-secondary">{listing.suggestion_reason}</p>}
      <SuggestionActions itemId={entry.item_id} listing={listing} />
    </li>
  );
}

function EndLeftoverButton({ entry }) {
  const toast = useToast();
  const queryClient = useQueryClient();
  const end = useMutation({
    mutationFn: () => api.endListing(entry.item_id, entry.listing.id),
    onSuccess: () => {
      toast.success("Anzeige als beendet markiert");
      queryClient.invalidateQueries({ queryKey: ["dueAdjustments"] });
      queryClient.invalidateQueries({ queryKey: ["inventory"] });
    },
    onError: (err) => toast.error(err.message),
  });
  return (
    <button
      type="button"
      disabled={end.isPending}
      onClick={() => end.mutate()}
      className="text-xs px-3 py-1.5 rounded-lg border bg-bg-hover text-text-primary border-border hover:border-lego-yellow disabled:opacity-50"
    >
      Gelöscht – beenden
    </button>
  );
}

function Section({ title, hint, children, count }) {
  return (
    <section className="space-y-3">
      <div>
        <h2 className="text-lg font-bold text-text-primary">
          {title} <span className="text-text-muted font-normal">({count})</span>
        </h2>
        {hint && <p className="text-sm text-text-secondary">{hint}</p>}
      </div>
      {children}
    </section>
  );
}

export default function Anpassungen() {
  const { data, isLoading, error } = useQuery({
    queryKey: ["dueAdjustments"],
    queryFn: api.dueAdjustments,
    refetchInterval: 5 * 60_000,
  });

  if (isLoading) {
    return <p className="text-text-secondary">Lade fällige Anpassungen …</p>;
  }
  if (error) {
    return <p className="text-no-go">Anpassungen nicht geladen: {error.message}</p>;
  }

  const { suggestions = [], at_floor: atFloor = [], sold_leftovers: leftovers = [] } = data || {};
  const nothing = suggestions.length === 0 && atFloor.length === 0 && leftovers.length === 0;

  return (
    <div className="space-y-8">
      <header>
        <h1 className="text-2xl font-bold text-text-primary">Fällige Anpassungen</h1>
        <p className="text-sm text-text-secondary">
          Der tägliche Check (08:05) schlägt Preise nach Alter der Anzeige und Zustand vor, nie unter der
          Schmerzgrenze. Preis zuerst auf der Plattform ändern, dann hier „Übernommen“.
        </p>
      </header>

      {nothing && (
        <p className="bg-bg-card border border-border rounded-xl p-6 text-text-secondary">
          Nichts fällig. Neue Vorschläge kommen, sobald eine Anzeige ein Prüfintervall (Standard 14 Tage) ohne
          Preisänderung online ist.
        </p>
      )}

      {suggestions.length > 0 && (
        <Section title="Preisvorschläge" count={suggestions.length}>
          <ul className="grid gap-3 md:grid-cols-2">
            {suggestions.map((entry) => (
              <SuggestionCard key={entry.listing.id} entry={entry} />
            ))}
          </ul>
        </Section>
      )}

      {leftovers.length > 0 && (
        <Section
          title="Verkauft, Anzeige läuft noch"
          count={leftovers.length}
          hint="Anzeige auf der Plattform löschen, dann hier beenden."
        >
          <ul className="grid gap-3 md:grid-cols-2">
            {leftovers.map((entry) => (
              <li key={entry.listing.id} className="bg-bg-card border border-no-go/40 rounded-xl p-4 space-y-3">
                <div className="flex items-start justify-between gap-3">
                  <ListingTitle entry={entry} />
                  <AdLink url={entry.listing.url} />
                </div>
                <EndLeftoverButton entry={entry} />
              </li>
            ))}
          </ul>
        </Section>
      )}

      {atFloor.length > 0 && (
        <Section
          title="An der Schmerzgrenze"
          count={atFloor.length}
          hint="Tiefer schlägt der Check nicht vor. Schmerzgrenze im Inventar senken, Text oder Fotos auffrischen, oder die Anzeige beenden."
        >
          <ul className="grid gap-3 md:grid-cols-2">
            {atFloor.map((entry) => (
              <li key={entry.listing.id} className="bg-bg-card border border-border rounded-xl p-4">
                <div className="flex items-start justify-between gap-3">
                  <ListingTitle entry={entry} />
                  <AdLink url={entry.listing.url} />
                </div>
                <p className="mt-2 font-[family-name:var(--font-mono)] text-text-primary">
                  {formatEuro(entry.listing.current_price)}
                </p>
              </li>
            ))}
          </ul>
        </Section>
      )}
    </div>
  );
}
