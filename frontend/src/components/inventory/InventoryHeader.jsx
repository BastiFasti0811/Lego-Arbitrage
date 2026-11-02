import GoalProgress from "../GoalProgress";
import StatCard from "../StatCard";
import ValuationStatus from "../ValuationStatus";
import SalesCockpit from "./SalesCockpit";
import { formatMoney, profitColor } from "./inventoryFormat";

// Kopf des Inventars: Aktionen, Kennzahlen, Zielstand, Verkaufs-Cockpit,
// Bewertungslauf und Sell-Signale.
export default function InventoryHeader({
  summary,
  goalProgress,
  cockpit,
  onPhotoFirst,
  onAdd,
  onShowWithoutPhoto,
  onShowReady,
  onPrepareCandidate,
  preparingId,
}) {
  return (
    <>
      <div className="flex items-center justify-between mb-6">
        <h1 className="text-2xl font-bold text-text-primary">Inventar</h1>
        <div className="flex gap-2">
          <button onClick={onPhotoFirst} className="bg-lego-blue text-white font-bold px-4 py-2 rounded-lg text-sm hover:bg-lego-blue/90 transition-colors">
            Per Foto anlegen
          </button>
          <button onClick={onAdd} className="bg-lego-yellow text-black font-bold px-4 py-2 rounded-lg text-sm hover:bg-lego-yellow/90 transition-colors">
            + Hinzufügen
          </button>
        </div>
      </div>

      {summary && (
        <div className="grid grid-cols-2 md:grid-cols-5 gap-3 mb-6">
          <StatCard label="Sets" value={summary.holding_items} sub={`${summary.sold_items} verkauft`} />
          <StatCard
            label="Lego inseriert"
            value={`${summary.lego_listed}/${summary.lego_items}`}
            sub={`${summary.lego_sold} verkauft`}
          />
          <StatCard label="Investiert" value={formatMoney(summary.total_invested)} />
          <StatCard
            label="Aktueller Wert"
            value={formatMoney(summary.current_value)}
            sub={summary.stale_market_values > 0 ? `${summary.stale_market_values} Marktwert${summary.stale_market_values > 1 ? "e" : ""} veraltet` : undefined}
            color="text-lego-yellow"
          />
          <StatCard label="Unrealisiert" value={`${summary.unrealized_profit >= 0 ? "+" : ""}${formatMoney(summary.unrealized_profit)}`} sub={`${summary.unrealized_roi_percent.toFixed(1)}%`} color={profitColor(summary.unrealized_profit)} />
        </div>
      )}

      <GoalProgress progress={goalProgress} />

      <SalesCockpit
        cockpit={cockpit}
        onShowWithoutPhoto={onShowWithoutPhoto}
        onShowReady={onShowReady}
        onPrepare={onPrepareCandidate}
        preparingId={preparingId}
      />

      <ValuationStatus />

      {summary?.sell_signals_active > 0 && (
        <div className="bg-sell-signal/10 border border-sell-signal/30 rounded-xl p-4 mb-6 flex items-center gap-3">
          <div className="w-3 h-3 bg-sell-signal rounded-full animate-pulse" />
          <span className="text-sell-signal font-medium text-sm">
            {summary.sell_signals_active} Sell-Signal{summary.sell_signals_active > 1 ? "e" : ""} aktiv - jetzt verkaufen.
          </span>
        </div>
      )}
    </>
  );
}
