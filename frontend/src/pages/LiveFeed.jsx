import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../api/client";
import { useAppStore } from "../stores/appStore";
import { exactMoment, money, relativeAge } from "../lib/datetime";
import DealCard from "../components/DealCard";

const VERDICT_OPTIONS = ["ALL", "GO_STAR", "GO", "CHECK", "NO_GO"];
const DISMISS_KEY = ["scout", "dismiss"];

export default function LiveFeed() {
  const { feedFilters, setFeedFilters } = useAppStore();
  const [showDismissed, setShowDismissed] = useState(false);
  const queryClient = useQueryClient();

  // Fetch watchlist to get set numbers
  const { data: watchlist } = useQuery({
    queryKey: ["watchlist"],
    queryFn: api.listWatchlist,
    retry: 1,
  });

  // Sortiert, weil die Watchlist-Abfrage kein ORDER BY hat: kommen die Zeilen
  // nach einem Update in anderer Heap-Reihenfolge, änderte sich der
  // Query-Key — und das optimistische Update schriebe in einen Cache-Eintrag,
  // den die montierte Abfrage nicht mehr benutzt.
  const setNumbers = [...(watchlist?.map((w) => w.set_number) || [])].sort();
  const feedKey = ["feed", setNumbers];

  // Fetch deals from scout
  const { data, isLoading, isError, dataUpdatedAt } = useQuery({
    queryKey: feedKey,
    queryFn: () => api.feedList(setNumbers),
    enabled: setNumbers.length > 0,
    refetchInterval: 30_000,
  });

  const {
    data: dismissedOffers,
    isLoading: dismissedLoading,
    isError: dismissedFailed,
  } = useQuery({
    queryKey: ["dismissed"],
    queryFn: api.listDismissed,
    enabled: showDismissed,
  });

  // Erst neu laden, wenn keine weitere Abwahl mehr unterwegs ist. Sonst holt
  // der Refetch der ersten Karte die zweite zurück, deren POST noch läuft.
  const refreshWhenSettled = () => {
    if (queryClient.isMutating({ mutationKey: DISMISS_KEY }) > 1) return;
    queryClient.invalidateQueries({ queryKey: feedKey });
    queryClient.invalidateQueries({ queryKey: ["dismissed"] });
  };

  const dismiss = useMutation({
    mutationKey: DISMISS_KEY,
    mutationFn: api.dismissOffer,
    // Die Karte verschwindet sofort. Auf den Refetch zu warten hieße, dass
    // der Klick bis zu einer Sekunde lang folgenlos aussieht.
    onMutate: async (deal) => {
      await queryClient.cancelQueries({ queryKey: feedKey });
      const previous = queryClient.getQueryData(feedKey);
      queryClient.setQueryData(feedKey, (old) =>
        old ? { ...old, deals: old.deals.filter((d) => d.offer_url !== deal.offer_url) } : old,
      );
      return { previous };
    },
    // Scheitert der Request, gehört die Karte zurück in den Feed — sonst
    // sieht es aus wie erledigt und ist es nicht. Der Fehler wird unten
    // angezeigt: ein stumm zurückspringendes Kärtchen liest sich wie ein
    // Fehlklick, und man klickt es wieder und wieder.
    onError: (_error, _deal, context) => {
      if (context?.previous) queryClient.setQueryData(feedKey, context.previous);
    },
    onSettled: refreshWhenSettled,
  });

  const restore = useMutation({
    mutationFn: api.restoreOffer,
    onSettled: () => {
      queryClient.invalidateQueries({ queryKey: feedKey });
      queryClient.invalidateQueries({ queryKey: ["dismissed"] });
    },
  });

  const deals = data?.deals || [];
  const dismissedCount = dismissedOffers?.length ?? 0;

  // Client-side filtering
  const filtered = deals.filter((d) => {
    if (feedFilters.verdict && feedFilters.verdict !== "ALL" && d.recommendation !== feedFilters.verdict) return false;
    if (d.estimated_roi < feedFilters.minRoi) return false;
    if (d.risk_score > feedFilters.maxRisk) return false;
    return true;
  });

  // Die belastbare Zahl steht oben: sie ist aus den Angeboten selbst gerechnet.
  // Der Pipeline-Lauf darunter sagt nur, dass ein Scrape-Task zurückgekehrt
  // ist — beide Tasks fangen einen 403 ab und melden trotzdem Erfolg. Bleibt
  // die obere Zeile hinter der unteren zurück, läuft die Pipeline zwar, bringt
  // aber nichts mehr mit.
  const offersSeen = exactMoment(data?.last_offer_seen_at);
  const offersSeenAge = relativeAge(data?.last_offer_seen_at);
  const pipelineRun = exactMoment(data?.last_scan_at);

  const freshnessLine = () => {
    if (setNumbers.length === 0) return "Keine Sets auf der Watchlist";
    if (isLoading) return "Lade Angebote …";
    if (isError) return "Feed nicht erreichbar";
    if (!offersSeen) return "Noch keine Angebote gespeichert";
    return `Angebote bestätigt bis ${offersSeen}${offersSeenAge ? ` (${offersSeenAge})` : ""}`;
  };

  return (
    <div>
      {/* Header */}
      <div className="flex items-center justify-between mb-6">
        <div>
          <h1 className="text-2xl font-bold text-text-primary">Live Feed</h1>
          <p className="text-text-muted text-sm mt-1">{freshnessLine()}</p>
          <p className="text-text-muted text-xs">
            {pipelineRun ? `Pipeline-Lauf ${pipelineRun}` : "Kein Pipeline-Lauf gemeldet"}
            {dataUpdatedAt && ` · Ansicht ${new Date(dataUpdatedAt).toLocaleTimeString("de-DE")}`}
          </p>
        </div>
        <div className="text-right">
          <div className="text-text-muted text-xs font-[family-name:var(--font-mono)]">
            {deals.length} Deals gefunden
          </div>
          <div className="text-text-muted text-xs">
            {data?.sets_analyzed || 0} Sets gescannt
          </div>
        </div>
      </div>

      {dismiss.isError && (
        <p className="text-no-go text-sm mb-4">
          {"Ausblenden fehlgeschlagen: "}
          {dismiss.error.message}
        </p>
      )}

      {/* Filter Bar */}
      <div className="bg-bg-card border border-border rounded-xl p-4 mb-6">
        <div className="flex flex-wrap items-center gap-4">
          {/* Verdict Toggles */}
          <div className="flex gap-1">
            {VERDICT_OPTIONS.map((v) => (
              <button
                key={v}
                onClick={() => setFeedFilters({ verdict: v === "ALL" ? null : v })}
                className={`px-3 py-1.5 rounded-lg text-xs font-medium transition-colors ${
                  (feedFilters.verdict === v || (!feedFilters.verdict && v === "ALL"))
                    ? "bg-lego-yellow text-black"
                    : "bg-bg-hover text-text-secondary hover:text-text-primary"
                }`}
              >
                {v === "GO_STAR" ? "GO ⭐" : v === "NO_GO" ? "NO-GO" : v}
              </button>
            ))}
          </div>

          {/* Min ROI */}
          <div className="flex items-center gap-2">
            <span className="text-text-muted text-xs">Min ROI</span>
            <input
              type="range"
              min="0"
              max="100"
              value={feedFilters.minRoi}
              onChange={(e) => setFeedFilters({ minRoi: Number(e.target.value) })}
              className="w-20 accent-lego-yellow"
            />
            <span className="text-text-secondary text-xs font-[family-name:var(--font-mono)] w-8">
              {feedFilters.minRoi}%
            </span>
          </div>

          {/* Max Risk */}
          <div className="flex items-center gap-2">
            <span className="text-text-muted text-xs">Max Risk</span>
            <input
              type="range"
              min="1"
              max="10"
              value={feedFilters.maxRisk}
              onChange={(e) => setFeedFilters({ maxRisk: Number(e.target.value) })}
              className="w-20 accent-lego-yellow"
            />
            <span className="text-text-secondary text-xs font-[family-name:var(--font-mono)] w-8">
              {feedFilters.maxRisk}
            </span>
          </div>

          {/* Ausgeblendete */}
          <button
            onClick={() => setShowDismissed((open) => !open)}
            className={`ml-auto px-3 py-1.5 rounded-lg text-xs font-medium transition-colors ${
              showDismissed ? "bg-lego-yellow text-black" : "bg-bg-hover text-text-secondary hover:text-text-primary"
            }`}
          >
            {showDismissed ? "Ausgeblendete verbergen" : "Ausgeblendete anzeigen"}
          </button>
        </div>
      </div>

      {/* Ausgeblendete Inserate */}
      {showDismissed && (
        <div className="bg-bg-card border border-border rounded-xl p-4 mb-6">
          <h2 className="text-text-primary text-sm font-semibold mb-3">
            {"Ausgeblendet"}
            {!dismissedLoading && !dismissedFailed && (
              <span className="text-text-muted font-normal">{` (${dismissedCount})`}</span>
            )}
          </h2>
          {restore.isError && (
            <p className="text-no-go text-xs mb-2">
              {"Einblenden fehlgeschlagen: "}
              {restore.error.message}
            </p>
          )}
          {/* Ohne diese beiden Zweige stünde hier "Noch nichts abgewählt" —
              die falsche Antwort für genau den Nutzer, der nachsieht, ob seine
              Abwahl angekommen ist. */}
          {dismissedLoading ? (
            <p className="text-text-muted text-xs">{"Lade …"}</p>
          ) : dismissedFailed ? (
            <p className="text-no-go text-xs">Liste nicht erreichbar.</p>
          ) : dismissedCount === 0 ? (
            <p className="text-text-muted text-xs">Noch nichts abgewählt.</p>
          ) : (
            <ul className="divide-y divide-border/50">
              {dismissedOffers.map((entry) => (
                <li key={entry.id} className="flex flex-wrap items-center gap-x-3 gap-y-1 py-2">
                  <span className="text-lego-yellow font-[family-name:var(--font-mono)] text-xs shrink-0">
                    {entry.set_number || "–"}
                  </span>
                  <a
                    href={entry.offer_url}
                    target="_blank"
                    rel="noopener noreferrer"
                    className="text-text-secondary text-xs truncate min-w-[8rem] flex-1 hover:text-lego-blue transition-colors"
                  >
                    {entry.offer_title || entry.offer_url}
                  </a>
                  <span className="text-text-muted text-xs shrink-0">{money(entry.price_eur)}</span>
                  <span className="text-text-muted text-xs shrink-0">{exactMoment(entry.dismissed_at)}</span>
                  <button
                    onClick={() => restore.mutate(entry.id)}
                    disabled={restore.isPending && restore.variables === entry.id}
                    className="text-lego-blue text-xs shrink-0 hover:underline disabled:opacity-50"
                  >
                    Wieder einblenden
                  </button>
                </li>
              ))}
            </ul>
          )}
        </div>
      )}

      {/* Content */}
      {setNumbers.length === 0 ? (
        <div className="text-center py-20">
          <div className="text-4xl mb-4">{"📡"}</div>
          <h2 className="text-text-primary text-lg font-semibold mb-2">Kein Live Feed aktiv</h2>
          <p className="text-text-muted text-sm">
            Füge Sets zur Watchlist hinzu, um den Live Feed zu aktivieren.
          </p>
        </div>
      ) : isLoading ? (
        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
          {[...Array(6)].map((_, i) => (
            <div key={i} className="bg-bg-card border border-border rounded-xl p-4 animate-pulse">
              <div className="h-4 bg-bg-hover rounded w-1/3 mb-3" />
              <div className="h-3 bg-bg-hover rounded w-2/3 mb-2" />
              <div className="h-3 bg-bg-hover rounded w-1/2" />
            </div>
          ))}
        </div>
      ) : filtered.length === 0 ? (
        <div className="text-center py-20">
          <div className="text-4xl mb-4">{"🔍"}</div>
          <h2 className="text-text-primary text-lg font-semibold mb-2">Keine Deals gefunden</h2>
          <p className="text-text-muted text-sm">
            Passe die Filter an oder warte auf neue Angebote.
          </p>
        </div>
      ) : (
        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
          {filtered.map((deal) => (
            /* Kein Index im Key: beim Entfernen aus der Mitte änderte sich
               jeder Key dahinter, und React montierte den ganzen Rest neu —
               Fokus weg, Übergänge von vorn. build_feed dedupet vorher auf
               die Identität, offer_url allein ist also eindeutig. */
            <DealCard
              key={`${deal.platform}-${deal.offer_url}`}
              deal={deal}
              onDismiss={(target) => dismiss.mutate(target)}
            />
          ))}
        </div>
      )}
    </div>
  );
}
