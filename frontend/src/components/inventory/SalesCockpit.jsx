import { formatMoney, PHOTO_ICON } from "./inventoryFormat";

// Verkaufs-Cockpit im Inventar-Kopf (Plan KW 45): was als Naechstes
// fotografiert und eingestellt wird und was heute schon lief.
// Quelle: GET /api/inventory/cockpit (app.services.sales_cockpit).

function CockpitNumber({ label, value, hint, onClick, title }) {
  const body = (
    <>
      <span className="text-text-muted text-xs block">{label}</span>
      <span className="font-[family-name:var(--font-mono)] font-bold text-text-primary text-lg">{value}</span>
      {hint && <span className="text-text-secondary text-xs block">{hint}</span>}
    </>
  );
  if (!onClick) return <div className="min-w-[8rem]">{body}</div>;
  return (
    <button
      type="button"
      onClick={onClick}
      title={title}
      className="min-w-[8rem] text-left rounded-lg px-2 py-1 -mx-2 -my-1 hover:bg-bg-hover transition-colors"
    >
      {body}
    </button>
  );
}

function postingHint(today) {
  const parts = [`wartend ${today.waiting_approval}`];
  // Das Tageslimit zaehlt Versuche, nicht nur Erfolge.
  if (today.started > today.posted) parts.push(`${today.started}/${today.max_per_day} Versuche`);
  if (today.queued > 0) parts.push(`${today.queued} freigegeben`);
  if (today.failed > 0) parts.push(`${today.failed} fehlgeschlagen`);
  return parts.join(" · ");
}

function NextCandidates({ candidates, withoutPhoto, onPrepare, preparingId }) {
  return (
    <div className="mt-4 pt-3 border-t border-border/50">
      <h2 id="next-candidates-heading" className="text-text-muted text-xs uppercase tracking-wider mb-2">
        Nächste {candidates.length || ""} zum Inserieren
      </h2>
      {candidates.length === 0 ? (
        <p className="text-text-secondary text-sm">
          Kein Lego-Posten mit Foto ohne Entwurf.
          {withoutPhoto > 0 && ` Als Nächstes fotografieren: ${withoutPhoto} ohne Foto.`}
        </p>
      ) : (
        <ol className="space-y-2" aria-labelledby="next-candidates-heading">
          {candidates.map((candidate) => (
            <li key={candidate.item_id} className="flex flex-wrap items-center gap-x-3 gap-y-1">
              <span className="text-lego-yellow font-[family-name:var(--font-mono)] text-sm font-semibold">
                {candidate.set_number ?? "—"}
              </span>
              <span className="text-text-primary text-sm flex-1 min-w-[10rem] truncate">{candidate.set_name}</span>
              <span className="text-text-muted text-xs">
                {PHOTO_ICON} {candidate.photo_count}
                {candidate.storage_location ? ` · ${candidate.storage_location}` : ""}
              </span>
              <span className="font-[family-name:var(--font-mono)] text-sm text-text-primary">
                {formatMoney(candidate.suggested_price)}
              </span>
              <button
                type="button"
                onClick={() => onPrepare(candidate)}
                disabled={preparingId !== null}
                aria-label={`Inserat vorbereiten: ${candidate.set_number ?? ""} ${candidate.set_name}`.replace(/\s+/g, " ")}
                className="text-xs px-3 py-1.5 rounded-lg font-bold bg-lego-yellow text-black hover:bg-lego-yellow/90 disabled:opacity-60 transition-colors"
              >
                {preparingId === candidate.item_id ? "Bereite vor …" : "Inserat vorbereiten"}
              </button>
            </li>
          ))}
        </ol>
      )}
    </div>
  );
}

export default function SalesCockpit({ cockpit, onShowWithoutPhoto, onShowReady, onPrepare, preparingId = null }) {
  if (!cockpit) return null;
  const today = cockpit.posting_today;
  return (
    <section aria-label="Verkaufs-Cockpit" className="bg-bg-card border border-border rounded-xl px-4 py-3 mb-6">
      <div className="flex flex-wrap items-start gap-x-8 gap-y-3">
        <CockpitNumber
          label="ohne Foto"
          value={`${cockpit.lego_without_photo}/${cockpit.lego_holding}`}
          hint="Lego, als Nächstes fotografieren"
          onClick={onShowWithoutPhoto}
          title="Lego ohne Foto in der Liste zeigen"
        />
        <CockpitNumber
          label="Entwürfe bereit"
          value={cockpit.drafts_ready}
          hint="mit Foto und Preis"
          onClick={onShowReady}
          title="Entwürfe bereit zum Einstellen in der Liste zeigen"
        />
        <CockpitNumber
          label="heute eingestellt"
          value={`${today.posted}/${today.max_per_day}`}
          hint={postingHint(today)}
        />
      </div>
      {today.block && <p className="text-text-muted text-xs mt-2">{today.block}</p>}
      <NextCandidates
        candidates={cockpit.candidates}
        withoutPhoto={cockpit.lego_without_photo}
        onPrepare={onPrepare}
        preparingId={preparingId}
      />
    </section>
  );
}
