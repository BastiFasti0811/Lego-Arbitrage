import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { api } from "../api/client";
import { isRunActive } from "../lib/valuationRuns";

function formatWhen(iso) {
  if (!iso) return "nie";
  const d = new Date(iso);
  return d.toLocaleString("de-DE", {
    day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit",
  });
}

function summarize(run) {
  if (!run) return "Noch kein Lauf aufgezeichnet.";
  if (run.status === "running") {
    if (isRunActive(run)) return "Aktualisierung läuft …";
    return "Keine Rückmeldung seit dem Start — ein neuer Lauf ist möglich.";
  }
  if (run.status === "failed") {
    // items_total bleibt bei einem abgebrochenen Lauf immer 0 (der Recorder
    // schreibt erst am Laufende) — "0 von 0 bewertet" waere hier genau das
    // beruhigend-leere Signal, das dieser Branch abschaffen soll.
    return `Abgebrochen — ${run.error || "unbekannter Fehler"}`;
  }
  const parts = [`${run.items_valued} von ${run.items_total} bewertet`];
  if (run.items_skipped) parts.push(`${run.items_skipped} übersprungen`);
  if (run.items_failed) parts.push(`${run.items_failed} fehlgeschlagen`);
  return parts.join(" · ");
}

export default function ValuationStatus() {
  const queryClient = useQueryClient();
  const { data: runs = [] } = useQuery({
    queryKey: ["valuationRuns"],
    queryFn: () => api.listValuationRuns(1),
    // Ein Lauf dauert rund elf Minuten. Solange er laeuft, muss die Anzeige
    // nachziehen — sonst klickt man ihn ein zweites Mal an. Jenseits von
    // STALE_RUN_MINUTES gilt ein "running"-Lauf als vermutlich abgestuerzt;
    // schnelles Pollen braechte dann nichts mehr, also faellt das Intervall
    // auf 60s zurueck.
    refetchInterval: (query) => (isRunActive(query.state.data?.[0]) ? 10_000 : 60_000),
  });

  const latest = runs[0];
  const running = isRunActive(latest);
  const failed = latest?.status === "failed";

  const start = useMutation({
    mutationFn: api.startValuationRun,
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["valuationRuns"] }),
  });

  return (
    <div className="flex flex-wrap items-center gap-x-3 gap-y-2 mb-4 text-xs">
      <span className="text-text-muted">
        Letzte Aktualisierung: {formatWhen(latest?.finished_at || latest?.started_at)} —{" "}
        <span className={failed ? "text-no-go" : undefined}>{summarize(latest)}</span>
      </span>
      <button
        type="button"
        onClick={() => start.mutate()}
        disabled={running || start.isPending}
        className="px-3 py-1 rounded-lg bg-lego-yellow text-bg-primary font-medium disabled:opacity-50 disabled:cursor-not-allowed"
      >
        {running ? "Läuft …" : "Jetzt aktualisieren"}
      </button>
      <Link to="/protokoll" className="text-lego-yellow hover:underline">
        Protokoll
      </Link>
      {start.isError && (
        <span className="text-no-go">
          {start.error?.message || "Start fehlgeschlagen"}
        </span>
      )}
      <span className="text-text-muted">Automatisch alle 6 Stunden.</span>
    </div>
  );
}
