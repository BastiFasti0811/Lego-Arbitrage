import VerdictBadge from "./VerdictBadge";

export default function DealCard({ deal, onClick }) {
  const roi = deal.estimated_roi ?? deal.roi_percent;
  const roiColor = roi >= 30 ? "text-go-star" : roi >= 15 ? "text-go" : roi >= 0 ? "text-check" : "text-no-go";

  return (
    <div
      onClick={onClick}
      className="bg-bg-card border border-border rounded-xl p-4 hover:border-lego-blue/50 transition-all cursor-pointer group"
    >
      <div className="flex items-start justify-between gap-3">
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-2 mb-1">
            <span className="text-lego-yellow font-[family-name:var(--font-mono)] text-sm font-semibold">
              {deal.set_number}
            </span>
            <VerdictBadge verdict={deal.recommendation} size="sm" />
          </div>
          <h3 className="text-text-primary text-sm font-medium truncate group-hover:text-lego-blue transition-colors">
            {deal.set_name || deal.offer_title}
          </h3>
          <p className="text-text-muted text-xs mt-1">{deal.platform || deal.theme}</p>
        </div>
        <div className="text-right shrink-0">
          <div className="text-text-primary font-[family-name:var(--font-mono)] font-semibold">
            {deal.price ?? deal.offer_price}\u20AC
          </div>
          <div className="text-text-muted text-xs">\u2192 {deal.market_price}\u20AC</div>
          <div className={`font-[family-name:var(--font-mono)] text-sm font-bold ${roiColor}`}>
            {roi > 0 ? "+" : ""}{roi?.toFixed(1)}%
          </div>
        </div>
      </div>
      <div className="flex items-center justify-between mt-3 pt-3 border-t border-border/50">
        <span className="text-text-muted text-xs">Risk {deal.risk_score}/10</span>
        <span className="text-text-muted text-xs">Score {deal.opportunity_score}</span>
      </div>
    </div>
  );
}
