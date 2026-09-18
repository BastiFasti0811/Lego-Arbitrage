import { useEffect, useRef, useState } from "react";
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

function taskLabel(name) {
  return TASK_LABELS[name] || name.split(".").pop();
}

function formatAge(seconds) {
  if (seconds == null) return null;
  const hours = seconds / 3600;
  if (hours < 48) return `${Math.round(hours)} h`;
  return `${Math.round(hours / 24)} Tage`;
}

// Bei "failing" ist das Detail die Fehlermeldung. Bei "stale" ist es das
// Ergebnis des letzten ERFOLGREICHEN Laufs und damit irrefuehrend — dort
// zaehlt, wie lange der Task schon ueberfaellig ist.
function describeProblem(task) {
  if (task.status === "stale") {
    const age = formatAge(task.age_seconds);
    return { state: "ueberfaellig", text: age ? `letzter erfolgreicher Lauf vor ${age}` : null };
  }
  return { state: "fehlgeschlagen", text: task.detail };
}

export default function SystemStatus() {
  const [open, setOpen] = useState(false);
  const containerRef = useRef(null);

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
  const hasProblems = problems.length > 0;

  // Verschwinden die Probleme, darf das Popover nicht offen stehen bleiben —
  // sonst springt es beim naechsten Problem ungefragt wieder auf. Zustand beim
  // Rendern nachfuehren statt per Effect (vermeidet einen Extra-Renderdurchlauf).
  const [hadProblems, setHadProblems] = useState(hasProblems);
  if (hasProblems !== hadProblems) {
    setHadProblems(hasProblems);
    if (!hasProblems) setOpen(false);
  }

  useEffect(() => {
    if (!open) return undefined;
    const onKey = (event) => {
      if (event.key === "Escape") setOpen(false);
    };
    const onPointer = (event) => {
      if (containerRef.current && !containerRef.current.contains(event.target)) setOpen(false);
    };
    document.addEventListener("keydown", onKey);
    document.addEventListener("pointerdown", onPointer);
    return () => {
      document.removeEventListener("keydown", onKey);
      document.removeEventListener("pointerdown", onPointer);
    };
  }, [open]);

  const apiDown = healthError || (health && health.status !== "healthy");

  let dotClass = "bg-check";
  let label = health?.version || "...";
  if (apiDown) {
    dotClass = "bg-no-go";
    label = "API nicht erreichbar";
  } else if (pipelineError) {
    // Vor den gecachten Daten pruefen: TanStack behaelt beim fehlgeschlagenen
    // Refetch die letzten Daten, ein toter Status-Endpoint pulsierte sonst gruen.
    dotClass = "bg-check";
    label = "Status unbekannt";
  } else if (hasProblems) {
    dotClass = "bg-no-go animate-pulse";
    label = `${problems.length} ${problems.length === 1 ? "Problem" : "Probleme"}`;
  } else if (pipeline?.healthy) {
    dotClass = "bg-go-star animate-pulse";
  }

  const indicator = (
    <>
      <span className={`w-2 h-2 rounded-full shrink-0 ${dotClass}`} aria-hidden="true" />
      <span className={`font-[family-name:var(--font-mono)] ${hasProblems ? "text-no-go" : "text-text-muted"}`}>
        {label}
      </span>
    </>
  );

  return (
    <div ref={containerRef} className="relative">
      <span className="sr-only" role="status" aria-live="polite">
        Systemstatus: {label}
      </span>

      {hasProblems ? (
        <button
          type="button"
          onClick={() => setOpen((value) => !value)}
          aria-expanded={open}
          aria-label={`Systemstatus: ${label}, Details anzeigen`}
          className="flex items-center gap-2 text-xs"
        >
          {indicator}
        </button>
      ) : (
        <div className="flex items-center gap-2 text-xs" aria-hidden="true">
          {indicator}
        </div>
      )}

      {open && hasProblems && (
        // Mobil am Viewport verankert: rechts neben dem Punkt sitzt noch das
        // Zahnrad, ein rechtsbuendiges Popover ragte auf 320 px links hinaus.
        <div className="fixed inset-x-4 top-14 z-50 max-h-[60vh] overflow-y-auto rounded-lg border border-border bg-bg-card p-3 shadow-lg md:absolute md:inset-x-auto md:right-0 md:top-auto md:mt-2 md:w-80">
          <p className="mb-2 text-xs font-semibold text-text-primary">Pipeline-Probleme</p>
          <ul className="space-y-2">
            {problems.map((task) => {
              const { state, text } = describeProblem(task);
              return (
                <li key={task.task_name} className="text-xs">
                  <span className="font-medium text-text-primary">{taskLabel(task.task_name)}</span>
                  <span className="text-no-go"> — {state}</span>
                  {text && <p className="mt-0.5 break-words text-text-muted line-clamp-4">{text}</p>}
                </li>
              );
            })}
          </ul>
        </div>
      )}
    </div>
  );
}
