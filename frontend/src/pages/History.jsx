import { useQuery } from "@tanstack/react-query";
import {
  Bar,
  BarChart,
  CartesianGrid,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { api } from "../api/client";
import StatCard from "../components/StatCard";

const EURO = "\u20ac";
const EMPTY_ICON = "\u{1F4CA}";
const RIGHT_ARROW = "\u2192";
const AVG_PREFIX = "\u00d8";

const formatMoney = (value, digits = 0) => `${Number(value).toFixed(digits)}${EURO}`;

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
        <h1 className="text-2xl font-bold text-text-primary mb-6">Verkäufe</h1>
        <div className="text-center py-20">
          <div className="text-4xl mb-4">{EMPTY_ICON}</div>
          <h2 className="text-text-primary text-lg font-semibold mb-2">Noch keine Verkäufe</h2>
          <p className="text-text-muted text-sm">Sobald du Sets verkaufst, erscheint hier deine Performance.</p>
        </div>
      </div>
    );
  }

  const totalProfit = items.reduce((sum, item) => sum + (item.realized_profit || 0), 0);
  const avgRoi = items.reduce((sum, item) => sum + (item.realized_roi_percent || 0), 0) / items.length;
  const bestDeal = items.reduce(
    (best, item) => ((item.realized_profit || 0) > (best.realized_profit || 0) ? item : best),
    items[0],
  );
  const worstDeal = items.reduce(
    (worst, item) => ((item.realized_profit || 0) < (worst.realized_profit || 0) ? item : worst),
    items[0],
  );
  const winRate = (items.filter((item) => (item.realized_profit || 0) > 0).length / items.length) * 100;

  const monthlyMap = {};
  items.forEach((item) => {
    if (!item.sell_date) return;
    const month = item.sell_date.slice(0, 7);
    monthlyMap[month] = (monthlyMap[month] || 0) + (item.realized_profit || 0);
  });

  const monthlyData = Object.entries(monthlyMap)
    .sort(([a], [b]) => a.localeCompare(b))
    .map(([month, profit]) => ({ month, profit: Math.round(profit) }));

  const cumulativeData = monthlyData.reduce((acc, current) => {
    const previousTotal = acc.length > 0 ? acc[acc.length - 1].total : 0;
    acc.push({ month: current.month, total: Math.round(previousTotal + current.profit) });
    return acc;
  }, []);

  const profitColor = (value) => (value > 0 ? "text-go-star" : value < 0 ? "text-no-go" : "text-text-primary");

  return (
    <div>
      <h1 className="text-2xl font-bold text-text-primary mb-6">Verkäufe</h1>

      <div className="grid grid-cols-2 md:grid-cols-5 gap-3 mb-6">
        <StatCard
          label="Gesamtgewinn"
          value={`${totalProfit >= 0 ? "+" : ""}${formatMoney(totalProfit)}`}
          color={profitColor(totalProfit)}
        />
        <StatCard label={`${AVG_PREFIX} ROI`} value={`${avgRoi.toFixed(1)}%`} color={avgRoi >= 0 ? "text-go" : "text-no-go"} />
        <StatCard
          label="Bester Deal"
          value={`+${formatMoney(bestDeal.realized_profit || 0)}`}
          sub={bestDeal.set_number}
          color="text-go-star"
        />
        <StatCard
          label="Schlechtester"
          value={formatMoney(worstDeal.realized_profit || 0)}
          sub={worstDeal.set_number}
          color="text-no-go"
        />
        <StatCard label="Win Rate" value={`${winRate.toFixed(0)}%`} sub={`${items.length} Deals`} />
      </div>

      <div className="grid grid-cols-1 md:grid-cols-2 gap-4 mb-6">
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
                formatter={(value) => [formatMoney(value), "Gewinn"]}
              />
              <Bar dataKey="profit" fill="#22c55e" radius={[4, 4, 0, 0]} />
            </BarChart>
          </ResponsiveContainer>
        </div>

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
                formatter={(value) => [formatMoney(value), "Gesamt"]}
              />
              <Line type="monotone" dataKey="total" stroke="#f5c518" strokeWidth={2} dot={{ fill: "#f5c518", r: 3 }} />
            </LineChart>
          </ResponsiveContainer>
        </div>
      </div>

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
                  {formatMoney(item.total_invested)} {RIGHT_ARROW} {formatMoney(item.sell_price || 0)}
                </div>
                <div className={`font-[family-name:var(--font-mono)] font-bold ${profitColor(item.realized_profit || 0)}`}>
                  {(item.realized_profit || 0) > 0 ? "+" : ""}
                  {formatMoney(item.realized_profit || 0)}
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
