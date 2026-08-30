import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { api } from "../api/client";
import { isRunActive } from "../lib/valuationRuns";

const OUTCOME_LABEL = {
  valued: "bewertet",
  skipped: "übersprungen",
  failed: "fehlgeschlagen",
};

const REASON_LABEL = {
  no_prices: "keine Quelle lieferte einen Preis",
  zero_consensus: "Konsens ergab keinen Betrag",
  single_source: "nur eine Quelle — zu wenig für einen Konsens",
  divergence: "Quellen weichen zu stark voneinander ab",
  implausible_price: "Preis unplausibel gegen UVP",
  exception: "Fehler während der Bewertung",
};

function formatWhen(iso) {
  if (!iso) return "—";
  return new Date(iso).toLocaleString("de-DE", {
    day: "2-digit", month: "2-digit", year: "numeric",
    hour: "2-digit", minute: "2-digit",
  });
}

function RunDetail({ runId }) {
  const { data, isLoading } = useQuery({
    queryKey: ["valuationRun", runId],
    queryFn: () => api.getValuationRun(runId),
  });

  if (isLoading) return <p className="text-text-muted text-xs p-3">Wird geladen …</p>;
  if (!data?.items?.length) return <p className="text-text-muted text-xs p-3">Keine Zeilen.</p>;

  return (
    <div className="overflow-x-auto">
      <table className="w-full text-xs">
        <thead>
          <tr className="text-text-muted text-left">
            <th className="p-2">Set</th>
            <th className="p-2">Ergebnis</th>
            <th className="p-2">Grund</th>
            <th className="p-2">Quellenlage</th>
          </tr>
        </thead>
        <tbody>
          {data.items.map((item, index) => (
            <tr key={`${item.set_number}-${index}`} className="border-t border-bg-hover">
              <td className="p-2 font-[family-name:var(--font-mono)] text-lego-yellow">
                {item.set_number}
              </td>
              <td className="p-2">{OUTCOME_LABEL[item.outcome] || item.outcome}</td>
              <td className="p-2 text-text-secondary">
                {item.reason ? REASON_LABEL[item.reason] || item.reason : "—"}
              </td>
              <td className="p-2 text-text-muted">{item.detail || "—"}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export default function ValuationLog() {
  const [openRunId, setOpenRunId] = useState(null);
  const { data: runs = [], isLoading } = useQuery({
    queryKey: ["valuationRuns", "all"],
    queryFn: () => api.listValuationRuns(30),
  });

  return (
    <div className="p-4">
      <div className="flex items-baseline justify-between mb-4">
        <h1 className="text-xl font-bold">Bewertungs-Protokoll</h1>
        <Link to="/inventar" className="text-lego-yellow text-sm hover:underline">
          zurück zum Inventar
        </Link>
      </div>
      <p className="text-text-muted text-xs mb-4">
        Läufe der letzten 30 Tage. Ältere werden automatisch entfernt.
      </p>

      {isLoading && <p className="text-text-muted text-sm">Wird geladen …</p>}
      {!isLoading && runs.length === 0 && (
        <p className="text-text-muted text-sm">Noch kein Lauf aufgezeichnet.</p>
      )}

      <div className="space-y-2">
        {runs.map((run) => (
          <div key={run.id} className="border border-bg-hover rounded-lg">
            <button
              type="button"
              onClick={() => setOpenRunId(openRunId === run.id ? null : run.id)}
              className="w-full flex flex-wrap items-center justify-between gap-2 p-3 text-left text-sm hover:bg-bg-hover/40"
            >
              <span>
                {formatWhen(run.started_at)}
                <span className="text-text-muted ml-2">
                  {run.trigger === "manual" ? "von Hand" : "geplant"}
                </span>
              </span>
              <span className="text-text-secondary">
                {run.items_valued} bewertet · {run.items_skipped} übersprungen
                {run.items_failed > 0 && ` · ${run.items_failed} fehlgeschlagen`}
                {run.status === "running" && (isRunActive(run) ? " · läuft" : " · abgebrochen")}
              </span>
            </button>
            {openRunId === run.id && <RunDetail runId={run.id} />}
          </div>
        ))}
      </div>
    </div>
  );
}
