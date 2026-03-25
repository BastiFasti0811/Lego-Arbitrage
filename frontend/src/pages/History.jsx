import { useQuery } from "@tanstack/react-query";
import { BarChart, Bar, LineChart, Line, XAxis, YAxis, Tooltip, ResponsiveContainer, CartesianGrid } from "recharts";
import { api } from "../api/client";
import StatCard from "../components/StatCard";

export default function History() {
  const { data: items, isLoading } = useQuery({
    queryKey: ["history"],
    queryFn: api.inventoryHistory,
  });

  if (isLoading) {
    return (
      <div className="space-y-4">
        <div className="h-8 bg-bg-card rounded w-40 animate-pulse" />
        <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
          {[...Array(4)].map((_, i) => (
            <div key={i} className="bg-bg-card border border-border rounded-xl p-4 animate-pulse h-20" />
          ))}
        </div>
      </div>
    );
  }

  if (!items?.length) {
    return (
      <div>
        <h1 className="text-2xl font-bold text-text-primary mb-6">History</h1>
        <div className="text-center py-20">
          <div className="text-4xl mb-4">📊</div>
          <h2 className="text-text-primary text-lg font-semibold mb-2">Noch keine Verkäufe</h2>
          <p className="text-text-muted text-sm">Sobald du Sets verkaufst, erscheint hier deine Performance.</p>
        </div>
      </div>
    );
  }

  // Compute stats
  const totalProfit = items.reduce((sum, i) => sum + (i.realized_profit || 0), 0);
  const avgRoi = items.reduce((sum, i) => sum + (i.realized_roi_percent || 0), 0) / items.length;
  const bestDeal = items.reduce((best, i) => (i.realized_profit || 0) > (best.realized_profit || 0) ? i : best, items[0]);
  const worstDeal = items.reduce((worst, i) => (i.realized_profit || 0) < (worst.realized_profit || 0) ? i : worst, items[0]);
  const winRate = (items.filter((i) => (i.realized_profit || 0) > 0).length / items.length) * 100;

  // Monthly profit data for chart
  const monthlyMap = {};
  items.forEach((item) => {
    if (!item.sell_date) return;
    const month = item.sell_date.slice(0, 7); // YYYY-MM
    monthlyMap[month] = (monthlyMap[month] || 0) + (item.realized_profit || 0);
  });
  const monthlyData = Object.entries(monthlyMap)
    .sort(([a], [b]) => a.localeCompare(b))
    .map(([month, profit]) => ({ month, profit: Math.round(profit) }));

  // Cumulative profit
  const cumulativeData = monthlyData.reduce((acc, current) => {
    const previousTotal = acc.length > 0 ? acc[acc.length - 1].total : 0;
    acc.push({ month: current.month, total: Math.round(previousTotal + current.profit) });
    return acc;
  }, []);

  const profitColor = (val) => (val > 0 ? "text-go-star" : val < 0 ? "text-no-go" : "text-text-primary");

  return (
    <div>
      <h1 className="text-2xl font-bold text-text-primary mb-6">History</h1>

      {/* Stats */}
      <div className="grid grid-cols-2 md:grid-cols-5 gap-3 mb-6">
        <StatCard
          label="Gesamtgewinn"
          value={`${totalProfit >= 0 ? "+" : ""}${totalProfit.toFixed(0)}€`}
          color={profitColor(totalProfit)}
        />
        <StatCard label="Ø ROI" value={`${avgRoi.toFixed(1)}%`} color={avgRoi >= 0 ? "text-go" : "text-no-go"} />
        <StatCard
          label="Bester Deal"
          value={`+${(bestDeal.realized_profit || 0).toFixed(0)}€`}
          sub={bestDeal.set_number}
          color="text-go-star"
        />
        <StatCard
          label="Schlechtester"
          value={`${(worstDeal.realized_profit || 0).toFixed(0)}€`}
          sub={worstDeal.set_number}
          color="text-no-go"
        />
        <StatCard label="Win Rate" value={`${winRate.toFixed(0)}%`} sub={`${items.length} Deals`} />
      </div>

      {/* Charts */}
      <div className="grid grid-cols-1 md:grid-cols-2 gap-4 mb-6">
        {/* Monthly Profit */}
        <div className="bg-bg-card border border-border rounded-xl p-4">
          <h3 className="text-text-secondary text-xs uppercase tracking-wider mb-4">Gewinn pro Monat</h3>
          <ResponsiveContainer width="100%" height={200}>
            <BarChart data={monthlyData}>
              <CartesianGrid strokeDasharray="3 3" stroke="#334155" />
              <XAxis dataKey="month" tick={{ fill: "#64748b", fontSize: 10 }} />
              <YAxis tick={{ fill: "#64748b", fontSize: 10 }} />
              <Tooltip
                contentStyle={{ background: "#1e293b", border: "1px solid #334155", borderRadius: 8 }}
                labelStyle={{ color: "#94a3b8" }}
                formatter={(val) => [`${val}€`, "Gewinn"]}
              />
              <Bar dataKey="profit" fill="#22c55e" radius={[4, 4, 0, 0]} />
            </BarChart>
          </ResponsiveContainer>
        </div>

        {/* Cumulative Profit */}
        <div className="bg-bg-card border border-border rounded-xl p-4">
          <h3 className="text-text-secondary text-xs uppercase tracking-wider mb-4">Kumulativer Gewinn</h3>
          <ResponsiveContainer width="100%" height={200}>
            <LineChart data={cumulativeData}>
              <CartesianGrid strokeDasharray="3 3" stroke="#334155" />
              <XAxis dataKey="month" tick={{ fill: "#64748b", fontSize: 10 }} />
              <YAxis tick={{ fill: "#64748b", fontSize: 10 }} />
              <Tooltip
                contentStyle={{ background: "#1e293b", border: "1px solid #334155", borderRadius: 8 }}
                labelStyle={{ color: "#94a3b8" }}
                formatter={(val) => [`${val}€`, "Gesamt"]}
              />
              <Line type="monotone" dataKey="total" stroke="#f5c518" strokeWidth={2} dot={{ fill: "#f5c518", r: 3 }} />
            </LineChart>
          </ResponsiveContainer>
        </div>
      </div>

      {/* Deal List */}
      <div className="bg-bg-card border border-border rounded-xl overflow-hidden">
        <div className="p-4 border-b border-border">
          <h3 className="text-text-secondary text-xs uppercase tracking-wider">Abgeschlossene Deals</h3>
        </div>
        <div className="divide-y divide-border">
          {items.map((item) => (
            <div key={item.id} className="p-4 flex items-center justify-between gap-4">
              <div className="flex-1 min-w-0">
                <div className="flex items-center gap-2">
                  <span className="text-lego-yellow font-[family-name:var(--font-mono)] text-sm font-semibold">
                    {item.set_number}
                  </span>
                  <span className="text-text-primary text-sm truncate">{item.set_name}</span>
                </div>
                <div className="flex gap-4 mt-1 text-xs text-text-muted">
                  <span>{item.holding_days} Tage gehalten</span>
                  {item.sell_platform && <span>via {item.sell_platform}</span>}
                  <span>{item.sell_date ? new Date(item.sell_date).toLocaleDateString("de-DE") : ""}</span>
                </div>
              </div>
              <div className="text-right shrink-0">
                <div className="text-text-muted text-xs">
                  {item.total_invested.toFixed(0)}€ → {item.sell_price?.toFixed(0)}€
                </div>
                <div className={`font-[family-name:var(--font-mono)] font-bold ${profitColor(item.realized_profit)}`}>
                  {(item.realized_profit || 0) > 0 ? "+" : ""}{(item.realized_profit || 0).toFixed(0)}€
                  <span className="text-xs ml-1">({(item.realized_roi_percent || 0).toFixed(1)}%)</span>
                </div>
              </div>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}
