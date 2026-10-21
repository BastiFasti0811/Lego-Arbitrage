import { useMutation, useQueryClient } from "@tanstack/react-query";
import { api } from "../api/client";
import { formatEuro } from "../lib/euro";
import { useToast } from "./toastContext";

// Antwort auf einen Preisvorschlag des Tages-Checks. Das System ändert die
// Anzeige nie selbst (ADR 0002): "Übernommen" heißt, Sebastian hat den Preis
// auf der Plattform schon geändert, und die App zieht nach.
const BUTTON = "text-xs px-3 py-1.5 rounded-lg border transition-colors disabled:opacity-50";

export default function SuggestionActions({ itemId, listing, onDone }) {
  const toast = useToast();
  const queryClient = useQueryClient();

  const refresh = () => {
    queryClient.invalidateQueries({ queryKey: ["dueAdjustments"] });
    queryClient.invalidateQueries({ queryKey: ["listings", itemId] });
    queryClient.invalidateQueries({ queryKey: ["inventory"] });
    onDone?.();
  };

  const action = useMutation({
    mutationFn: (kind) => {
      if (kind === "accept") return api.acceptSuggestion(itemId, listing.id);
      if (kind === "snooze") return api.snoozeSuggestion(itemId, listing.id);
      return api.dismissSuggestion(itemId, listing.id);
    },
    onSuccess: (_result, kind) => {
      if (kind === "accept") toast.success(`Neuer Preis ${formatEuro(listing.suggested_price)} gespeichert`);
      else if (kind === "snooze") toast.info("Zurückgestellt für 3 Tage");
      else toast.info("Vorschlag verworfen, nächster Check in einem Intervall");
      refresh();
    },
    onError: (err) => toast.error(err.message),
  });

  const busy = action.isPending;
  return (
    <div className="flex flex-wrap gap-2">
      <button
        type="button"
        disabled={busy}
        onClick={() => action.mutate("accept")}
        className={`${BUTTON} font-bold bg-lego-yellow text-black border-lego-yellow hover:bg-lego-yellow/90`}
        title="Preis auf der Plattform schon geändert? Dann hier übernehmen."
      >
        Übernommen ({formatEuro(listing.suggested_price)})
      </button>
      <button
        type="button"
        disabled={busy}
        onClick={() => action.mutate("snooze")}
        className={`${BUTTON} bg-bg-hover text-text-primary border-border hover:border-lego-yellow`}
      >
        Später (3 Tage)
      </button>
      <button
        type="button"
        disabled={busy}
        onClick={() => action.mutate("dismiss")}
        className={`${BUTTON} bg-bg-hover text-text-secondary border-border hover:border-no-go hover:text-no-go`}
      >
        Verwerfen
      </button>
    </div>
  );
}
