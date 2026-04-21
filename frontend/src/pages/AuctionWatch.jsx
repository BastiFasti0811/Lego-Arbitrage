import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../api/client";

const EURO = "\u20ac";

function money(value, digits = 0) {
  if (value == null) return "--";
  return `${Number(value).toFixed(digits)}${EURO}`;
}

function stamp(value) {
  if (!value) return "Noch nie";
  return new Date(value).toLocaleString("de-DE", { dateStyle: "short", timeStyle: "short" });
}

export default function AuctionWatch() {
  const queryClient = useQueryClient();
  const { data: items = [], isLoading } = useQuery({
    queryKey: ["auction-watch"],
    queryFn: api.listAuctionWatch,
    refetchInterval: 60_000,
  });

  const refreshItem = useMutation({
    mutationFn: (id) => api.refreshAuctionWatch(id),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["auction-watch"] }),
  });

  const removeItem = useMutation({
    mutationFn: (id) => api.removeAuctionWatch(id),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["auction-watch"] }),
  });

  return (
    <div>
      <div className="flex items-center justify-between mb-6">
        <div>
          <h1 className="text-2xl font-bold text-text-primary">Auktions-Watchlist</h1>
          <p className="text-text-muted text-sm mt-1">
            Konkrete Lots mit Maximalgebot, Break-even und täglichem Recheck.
          </p>
        </div>
        <div className="text-right text-sm">
          <div className="text-text-primary font-[family-name:var(--font-mono)] font-bold">{items.length}</div>
          <div className="text-text-muted text-xs">aktive Lots</div>
        </div>
      </div>

      {isLoading ? (
        <div className="text-text-muted">Lade Auktions-Watchlist...</div>
      ) : items.length === 0 ? (
        <div className="bg-bg-card border border-border rounded-xl p-8 text-center">
          <h2 className="text-text-primary font-semibold">Noch keine beobachteten Lots</h2>
          <p className="text-text-muted text-sm mt-2">
            Im Deal-Checker ein Catawiki-Lot rechnen und dann zur Watchlist hinzufügen.
          </p>
        </div>
      ) : (
        <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
          {items.map((item) => (
            <div key={item.id} className="bg-bg-card border border-border rounded-xl p-5">
              <div className="flex items-start justify-between gap-4">
                <div>
                  <div className="flex items-center gap-2">
                    <span className="text-lego-yellow font-[family-name:var(--font-mono)] text-sm">
                      {item.set_number}
                    </span>
                    <span
                      className={`text-xs px-2 py-1 rounded-full ${
                        item.current_bid_gap >= 0 ? "bg-go/15 text-go" : "bg-no-go/15 text-no-go"
                      }`}
                    >
                      {item.current_bid_status || item.status}
                    </span>
                  </div>
                  <h2 className="text-text-primary font-semibold mt-1">{item.set_name}</h2>
                  <p className="text-text-muted text-sm mt-1">
                    {item.source_platform} · letzter Check {stamp(item.last_checked_at)}
                  </p>
                </div>
                <div className="text-right">
                  <div className="text-text-muted text-xs uppercase">Maximalgebot</div>
                  <div className="text-go-star text-2xl font-bold font-[family-name:var(--font-mono)]">
                    {money(item.max_bid)}
                  </div>
                </div>
              </div>

              <div className="grid grid-cols-2 md:grid-cols-4 gap-3 mt-4">
                <div className="bg-bg-hover rounded-lg p-3">
                  <div className="text-text-muted text-xs uppercase">Aktuell</div>
                  <div className="text-text-primary font-[family-name:var(--font-mono)] font-bold">
                    {money(item.current_bid)}
                  </div>
                </div>
                <div className="bg-bg-hover rounded-lg p-3">
                  <div className="text-text-muted text-xs uppercase">Luft</div>
                  <div
                    className={`font-[family-name:var(--font-mono)] font-bold ${
                      (item.current_bid_gap || 0) >= 0 ? "text-go" : "text-no-go"
                    }`}
                  >
                    {money(item.current_bid_gap)}
                  </div>
                </div>
                <div className="bg-bg-hover rounded-lg p-3">
                  <div className="text-text-muted text-xs uppercase">ROI jetzt</div>
                  <div
                    className={`font-[family-name:var(--font-mono)] font-bold ${
                      (item.expected_roi_at_current_bid || 0) >= 0 ? "text-go" : "text-no-go"
                    }`}
                  >
                    {item.expected_roi_at_current_bid != null ? `${item.expected_roi_at_current_bid.toFixed(1)}%` : "--"}
                  </div>
                </div>
                <div className="bg-bg-hover rounded-lg p-3">
                  <div className="text-text-muted text-xs uppercase">Break-even</div>
                  <div className="text-text-primary font-[family-name:var(--font-mono)] font-bold">
                    {money(item.break_even_bid)}
                  </div>
                </div>
              </div>

              <div className="mt-4 text-sm text-text-muted">
                <p>{item.current_bid_recommendation || "Noch keine Empfehlung berechnet."}</p>
                {item.last_warning && <p className="mt-2 text-check">{item.last_warning}</p>}
              </div>

              <div className="flex flex-wrap items-center gap-3 mt-4">
                <button
                  type="button"
                  onClick={() => refreshItem.mutate(item.id)}
                  disabled={refreshItem.isPending}
                  className="bg-lego-blue text-white text-sm font-medium px-4 py-2 rounded-lg hover:bg-lego-blue/90 transition-colors disabled:opacity-50"
                >
                  Neu bewerten
                </button>
                <a
                  href={item.source_url}
                  target="_blank"
                  rel="noreferrer"
                  className="text-lego-yellow text-sm hover:underline"
                >
                  Lot öffnen
                </a>
                <button
                  type="button"
                  onClick={() => removeItem.mutate(item.id)}
                  disabled={removeItem.isPending}
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
