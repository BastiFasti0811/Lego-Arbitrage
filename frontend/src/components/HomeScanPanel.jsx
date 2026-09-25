import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../api/client";

// Der Server darf Catawiki nicht abrufen (IP-Sperre). Der Heimrechner fragt alle
// paar Minuten nach einem Auftrag; dieser Button legt einen an.
const RUNNER_POLL_MINUTES = 10;
const RUNNER_STALE_MINUTES = 30;

function formatStamp(value) {
  if (!value) return null;
  return new Date(value).toLocaleString("de-DE", { dateStyle: "short", timeStyle: "short" });
}

function minutesSince(value) {
  return value ? (Date.now() - new Date(value).getTime()) / 60000 : Infinity;
}

export default function HomeScanPanel() {
  const queryClient = useQueryClient();
  const { data: status, error } = useQuery({
    queryKey: ["remote-scan-status"],
    queryFn: api.remoteScanStatus,
    refetchInterval: 30_000,
  });
  const requestMutation = useMutation({
    mutationFn: api.requestRemoteScan,
    onSuccess: (data) => queryClient.setQueryData(["remote-scan-status"], data),
  });

  if (error) {
    return <p role="alert" className="text-no-go text-sm mb-3">Status des Heimrechner-Scans nicht ladbar.</p>;
  }
  if (!status) return null;

  const runnerStale = minutesSince(status.runner_seen_at) > RUNNER_STALE_MINUTES;
  const requested = Boolean(status.requested_at);
  const running = Boolean(status.job);

  return (
    <div className="rounded-lg border border-border bg-bg-primary/40 p-4 mb-4" aria-live="polite">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h2 className="text-text-primary text-sm font-semibold">Catawiki-Scan über den Heimrechner</h2>
          <p className="text-text-muted text-xs mt-1">
            Catawiki sperrt den Server. Der Scan läuft auf deinem PC und liefert die Lose hierher.
          </p>
        </div>
        <button
          type="button"
          onClick={() => requestMutation.mutate()}
          disabled={!status.token_configured || requested || running || requestMutation.isPending}
          className="bg-lego-yellow text-bg-primary text-sm font-semibold px-4 py-2 rounded-lg disabled:opacity-50"
        >
          {running ? "Scan läuft" : requested ? "Scan angefordert" : "Catawiki jetzt scannen"}
        </button>
      </div>

      <dl className="mt-3 grid gap-1 text-xs sm:grid-cols-[auto_1fr] sm:gap-x-4">
        <dt className="text-text-muted">Heimrechner zuletzt erreichbar</dt>
        <dd className={runnerStale ? "text-no-go" : "text-text-secondary"}>
          {formatStamp(status.runner_seen_at) || "noch nie"}
          {runnerStale && " – läuft der PC und die Aufgabe „LEGO Arbitrage Catawiki-Scan“?"}
        </dd>
        {running && (
          <>
            <dt className="text-text-muted">Scan läuft seit</dt>
            <dd className="text-text-secondary">
              {formatStamp(status.job.issued_at)}
              {status.job.delivered_at ? " – Lose angekommen, Bewertung läuft" : " – Heimrechner liest Catawiki"}
            </dd>
          </>
        )}
        {requested && !running && (
          <>
            <dt className="text-text-muted">Angefordert</dt>
            <dd className="text-text-secondary">
              {formatStamp(status.requested_at)} – wird beim nächsten Abruf geholt (alle {RUNNER_POLL_MINUTES} Minuten)
            </dd>
          </>
        )}
        {status.last_scan && (
          <>
            <dt className="text-text-muted">Letzter Catawiki-Scan</dt>
            <dd className="text-text-secondary">
              {formatStamp(status.last_scan.scanned_at)}, {status.last_scan.lots} Lose
              {status.last_scan.status !== "OK" && `, ${status.last_scan.errors.length} Fehler`}
            </dd>
          </>
        )}
      </dl>

      {!status.token_configured && (
        <p className="text-text-muted text-xs mt-3">
          Noch kein Heimrechner-Token: Einstellungen › Catawiki › Heimrechner-Token setzen und denselben Wert
          auf dem PC in <code>%USERPROFILE%\.lego-arbitrage\home-scan.env</code> eintragen
          (legt <code>scripts\install-catawiki-home-scan.ps1</code> an).
        </p>
      )}
      {requestMutation.isError && <p role="alert" className="text-no-go text-xs mt-2">{requestMutation.error.message}</p>}
    </div>
  );
}
