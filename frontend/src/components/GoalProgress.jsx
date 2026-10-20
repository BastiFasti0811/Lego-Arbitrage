// Zielstand im Inventar-Kopf (Memory projekt-ziel): inseriert/verkauft gesamt
// und gepruefte Deals der laufenden Woche. Dieselben Zahlen wie der
// Montags-Digest in Telegram, Quelle GET /api/goals/progress.

function formatDay(isoDate) {
  const [year, month, day] = isoDate.split("-");
  return year && month && day ? `${day}.${month}.` : isoDate;
}

function Meter({ value, target }) {
  const percent = target > 0 ? Math.min(100, Math.round((value / target) * 100)) : 0;
  return (
    <div
      className="h-1.5 w-full bg-bg-primary rounded-full overflow-hidden mt-1"
      role="progressbar"
      aria-valuemin={0}
      aria-valuemax={target}
      aria-valuenow={value}
    >
      <div className={`h-full ${value >= target ? "bg-go-star" : "bg-lego-yellow"}`} style={{ width: `${percent}%` }} />
    </div>
  );
}

function Goal({ label, value, of, target, hint }) {
  const reached = value >= target;
  return (
    <div className="min-w-[9rem] flex-1">
      <div className="flex items-baseline gap-1.5">
        <span className="text-text-muted text-xs">{label}</span>
        <span className={`font-[family-name:var(--font-mono)] font-bold ${reached ? "text-go-star" : "text-text-primary"}`}>
          {value}/{of}
        </span>
        {hint && <span className="text-text-secondary text-xs">{hint}</span>}
      </div>
      <Meter value={value} target={target} />
    </div>
  );
}

export default function GoalProgress({ progress }) {
  if (!progress) return null;
  const { current_week: week, previous_week: previous } = progress;
  return (
    <section
      aria-label="Zielstand"
      className="bg-bg-card border border-border rounded-xl px-4 py-3 mb-6 flex flex-wrap items-center gap-x-6 gap-y-3"
    >
      <div className="text-xs text-text-muted w-full sm:w-auto">
        Ziel bis {formatDay(progress.deadline)}
        <span className="block text-text-secondary">noch {progress.days_left} Tage</span>
      </div>
      <Goal
        label="inseriert"
        value={progress.lego_listed}
        of={progress.lego_items}
        target={progress.goal_listed}
        hint={`Ziel ${progress.goal_listed}`}
      />
      <Goal label="verkauft" value={progress.lego_sold} of={progress.goal_sold} target={progress.goal_sold} />
      <Goal
        label={`Deals KW ${week.iso_week}`}
        value={week.deals}
        of={progress.goal_deals_per_week}
        target={progress.goal_deals_per_week}
        hint={`Vorwoche ${previous.deals}`}
      />
    </section>
  );
}
