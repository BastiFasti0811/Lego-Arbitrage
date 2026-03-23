import { useQuery } from "@tanstack/react-query";
import { api } from "../api/client";
import { useAppStore } from "../stores/appStore";
import DealCard from "../components/DealCard";

const VERDICT_OPTIONS = ["ALL", "GO_STAR", "GO", "CHECK", "NO_GO"];

export default function LiveFeed() {
  const { feedFilters, setFeedFilters } = useAppStore();

  // Fetch watchlist to get set numbers
  const { data: watchlist } = useQuery({
    queryKey: ["watchlist"],
    queryFn: api.listWatchlist,
    retry: 1,
  });

  const setNumbers = watchlist?.map((w) => w.set_number) || [];

  // Fetch deals from scout
  const { data, isLoading, dataUpdatedAt } = useQuery({
    queryKey: ["feed", setNumbers],
    queryFn: () => api.scoutScan({ set_numbers: setNumbers, min_roi: 0 }),
    enabled: setNumbers.length > 0,
    refetchInterval: 30_000,
  });

  const deals = data?.deals || [];

  // Client-side filtering
  const filtered = deals.filter((d) => {
    if (feedFilters.verdict && feedFilters.verdict !== "ALL" && d.recommendation !== feedFilters.verdict) return false;
    if (d.estimated_roi < feedFilters.minRoi) return false;
    if (d.risk_score > feedFilters.maxRisk) return false;
    return true;
  });

  return (
    <div>
      {/* Header */}
      <div className="flex items-center justify-between mb-6">
        <div>
          <h1 className="text-2xl font-bold text-text-primary">Live Feed</h1>
          <p className="text-text-muted text-sm mt-1">
            {dataUpdatedAt
              ? `Letztes Update: ${new Date(dataUpdatedAt).toLocaleTimeString("de-DE")}`
              : "Warte auf Daten..."}
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
                {v === "GO_STAR" ? "GO \u2B50" : v === "NO_GO" ? "NO-GO" : v}
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
        </div>
      </div>

      {/* Content */}
      {setNumbers.length === 0 ? (
        <div className="text-center py-20">
          <div className="text-4xl mb-4">{"\uD83D\uDCE1"}</div>
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
          <div className="text-4xl mb-4">{"\uD83D\uDD0D"}</div>
          <h2 className="text-text-primary text-lg font-semibold mb-2">Keine Deals gefunden</h2>
          <p className="text-text-muted text-sm">
            Passe die Filter an oder warte auf neue Angebote.
          </p>
        </div>
      ) : (
        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
          {filtered.map((deal, i) => (
            <DealCard key={`${deal.set_number}-${deal.offer_url}-${i}`} deal={deal} />
          ))}
        </div>
      )}
    </div>
  );
}
