import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { api } from "../api/client";

function formatWhen(iso) {
  if (!iso) return "nie";
  const d = new Date(iso);
  return d.toLocaleString("de-DE", {
    day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit",
  });
}

function summarize(run) {
  if (!run) return "Noch kein Lauf aufgezeichnet.";
  if (run.status === "running") return "Aktualisierung läuft …";
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
    // nachziehen — sonst klickt man ihn ein zweites Mal an.
    refetchInterval: (query) =>
      query.state.data?.[0]?.status === "running" ? 10_000 : 60_000,
  });

  const latest = runs[0];
  const running = latest?.status === "running";

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
