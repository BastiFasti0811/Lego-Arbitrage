import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { api } from "../api/client";

// Kurze, sprechende Namen fuer die ueberwachten Celery-Tasks. Unbekannte
// Tasks fallen auf den letzten Namensteil zurueck.
const TASK_LABELS = {
  "app.tasks.scrape_daily.scrape_all_watched_sets": "Preis-Scrape",
  "app.tasks.scrape_daily.scrape_kleinanzeigen_watched": "Kleinanzeigen-Scan",
  "app.tasks.scrape_daily.refresh_known_set_metadata": "Set-Metadaten",
  "app.tasks.analyze_new.analyze_new_offers": "Angebotsanalyse",
  "app.tasks.analyze_new.send_daily_summary_task": "Tagesbericht",
  "app.tasks.weekly_report.send_weekly_report_task": "Wochenreport",
  "app.tasks.auction_watch.refresh_auction_watchlist": "Auktionen",
  "app.tasks.catawiki_scan.scan_configured_categories": "Catawiki-Scan",
  "app.tasks.update_inventory.update_inventory_valuations": "Inventar-Bewertung",
};

const STATUS_TEXT = { failing: "fehlgeschlagen", stale: "ueberfaellig" };

function taskLabel(name) {
  return TASK_LABELS[name] || name.split(".").pop();
}

export default function SystemStatus() {
  const [open, setOpen] = useState(false);

  // Liveness: antwortet die API ueberhaupt?
  const { data: health, isError: healthError } = useQuery({
    queryKey: ["health"],
    queryFn: () => api.health(),
    refetchInterval: 60_000,
  });

  // Pipeline: laufen die geplanten Tasks, und kommen die Benachrichtigungen an?
  // Das ist der einzige Kanal, der einen toten Telegram-Versand zeigen kann —
  // der Wachhund alarmiert selbst ueber Telegram.
  const { data: pipeline, isError: pipelineError } = useQuery({
    queryKey: ["pipeline-status"],
    queryFn: () => api.pipelineStatus(),
    refetchInterval: 5 * 60_000,
  });

  const problems = (pipeline?.tasks || []).filter((task) => task.status === "failing" || task.status === "stale");
  const apiDown = healthError || (health && health.status !== "healthy");

  let dotClass = "bg-check";
  let label = health?.version || "...";
  if (apiDown) {
    dotClass = "bg-no-go";
    label = "API nicht erreichbar";
  } else if (problems.length > 0) {
    dotClass = "bg-no-go animate-pulse";
    label = `${problems.length} ${problems.length === 1 ? "Problem" : "Probleme"}`;
  } else if (pipeline?.healthy) {
    dotClass = "bg-go-star animate-pulse";
  } else if (pipelineError) {
    label = `${label} · Status unbekannt`;
  }

  return (
    <div className="relative">
      <button
        type="button"
        onClick={() => setOpen((value) => !value)}
        disabled={problems.length === 0}
        aria-expanded={open}
        aria-label={problems.length > 0 ? `Systemstatus: ${label}, Details anzeigen` : `Systemstatus: ${label}`}
        className="flex items-center gap-2 text-xs disabled:cursor-default"
      >
        <span className={`w-2 h-2 rounded-full ${dotClass}`} aria-hidden="true" />
        <span
          className={`font-[family-name:var(--font-mono)] ${problems.length > 0 ? "text-no-go" : "text-text-muted"}`}
        >
          {label}
        </span>
      </button>

      {open && problems.length > 0 && (
        <div className="absolute right-0 z-20 mt-2 w-80 max-w-[calc(100vw-2rem)] rounded-lg border border-border bg-bg-card p-3 shadow-lg">
          <p className="mb-2 text-xs font-semibold text-text-primary">Pipeline-Probleme</p>
          <ul className="space-y-2">
            {problems.map((task) => (
              <li key={task.task_name} className="text-xs">
                <span className="font-medium text-text-primary">{taskLabel(task.task_name)}</span>
                <span className="text-no-go"> — {STATUS_TEXT[task.status] || task.status}</span>
                {task.detail && <p className="mt-0.5 break-words text-text-muted">{task.detail}</p>}
              </li>
            ))}
          </ul>
        </div>
      )}
    </div>
  );
}
