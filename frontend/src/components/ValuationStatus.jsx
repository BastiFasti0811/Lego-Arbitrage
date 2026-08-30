import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { api } from "../api/client";

// Muss STALE_RUN_MINUTES in backend/app/api/routes/inventory.py spiegeln.
// Dort entscheidet is_run_blocking() serverseitig, ob ein "running"-Lauf noch
// blockiert oder ein abgestuerzter Worker war. Bewusst dupliziert statt ueber
// die API geteilt — ein eigenes Antwortfeld waere fuer diesen einen Wert mehr
// Aufwand als er wert ist. Drift ist ungefaehrlich: der Server greift mit 409
// durch, wenn er anderer Meinung ist. Das Frontend kann hier also hoechstens
// zu freizuegig sein, nie zu restriktiv.
const STALE_RUN_MINUTES = 30;

function isRunActive(run) {
  if (!run || run.status !== "running") return false;
  const startedMs = new Date(run.started_at).getTime();
  return Date.now() - startedMs < STALE_RUN_MINUTES * 60_000;
}

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

  const start = useMutation({
    mutationFn: api.startValuationRun,
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["valuationRuns"] }),
  });

  return (
    <div className="flex flex-wrap items-center gap-x-3 gap-y-2 mb-4 text-xs">
      <span className="text-text-muted">
        Letzte Aktualisierung: {formatWhen(latest?.finished_at || latest?.started_at)} — {summarize(latest)}
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
