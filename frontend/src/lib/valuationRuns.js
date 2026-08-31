// Muss STALE_RUN_MINUTES in backend/app/api/routes/inventory.py spiegeln.
// Dort entscheidet is_run_blocking() serverseitig, ob ein "running"-Lauf noch
// blockiert oder ein abgestuerzter Worker war. Bewusst dupliziert statt ueber
// die API geteilt — ein eigenes Antwortfeld waere fuer diesen einen Wert mehr
// Aufwand als er wert ist. Drift ist ungefaehrlich: der Server greift mit 409
// durch, wenn er anderer Meinung ist. Das Frontend kann hier also hoechstens
// zu freizuegig sein, nie zu restriktiv.
//
// Geteilt zwischen der Statusleiste (ValuationStatus) und dem Verlauf
// (ValuationLog) — beide muessen denselben Lauf gleich beurteilen, sonst
// behauptet die eine Stelle "läuft", waehrend die andere ihn schon als
// abgebrochen anzeigt.
export const STALE_RUN_MINUTES = 30;

export function isRunActive(run) {
  if (!run || run.status !== "running") return false;
  const startedMs = new Date(run.started_at).getTime();
  return Date.now() - startedMs < STALE_RUN_MINUTES * 60_000;
}
