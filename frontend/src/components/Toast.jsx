import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { ToastContext } from "./toastContext";

// Kurze Rueckmeldungen unten rechts. Fehler bleiben laenger stehen und werden
// als role="alert" sofort vorgelesen; alles andere ist "polite".
const DURATION_MS = { success: 3500, info: 4500, warning: 7000, error: 8000 };
const STYLES = {
  success: "border-go-star/60 text-go-star",
  info: "border-lego-blue/60 text-text-primary",
  warning: "border-lego-yellow/70 text-lego-yellow",
  error: "border-no-go/70 text-no-go",
};

let nextId = 1;

export function ToastProvider({ children }) {
  const [toasts, setToasts] = useState([]);
  const timers = useRef(new Map());

  const dismiss = useCallback((id) => {
    setToasts((prev) => prev.filter((t) => t.id !== id));
    const timer = timers.current.get(id);
    if (timer) clearTimeout(timer);
    timers.current.delete(id);
  }, []);

  const show = useCallback(
    (message, kind = "info") => {
      if (!message) return;
      const id = nextId++;
      setToasts((prev) => [...prev.slice(-3), { id, message, kind }]);
      timers.current.set(id, setTimeout(() => dismiss(id), DURATION_MS[kind] ?? DURATION_MS.info));
    },
    [dismiss],
  );

  useEffect(() => {
    const pending = timers.current;
    return () => pending.forEach((timer) => clearTimeout(timer));
  }, []);

  const api = useMemo(
    () => ({
      success: (m) => show(m, "success"),
      info: (m) => show(m, "info"),
      warning: (m) => show(m, "warning"),
      error: (m) => show(m, "error"),
    }),
    [show],
  );

  return (
    <ToastContext.Provider value={api}>
      {children}
      <div className="fixed bottom-4 right-4 left-4 sm:left-auto z-[60] flex flex-col gap-2 sm:max-w-sm pointer-events-none">
        {toasts.map((t) => (
          <div
            key={t.id}
            role={t.kind === "error" ? "alert" : "status"}
            aria-live={t.kind === "error" ? "assertive" : "polite"}
            className={`pointer-events-auto flex items-start gap-3 bg-bg-card border rounded-lg shadow-xl px-3 py-2 text-sm ${STYLES[t.kind]}`}
          >
            <span className="flex-1 whitespace-pre-line">{t.message}</span>
            <button
              type="button"
              onClick={() => dismiss(t.id)}
              aria-label="Hinweis schließen"
              className="text-text-secondary hover:text-text-primary"
            >
              ✕
            </button>
          </div>
        ))}
      </div>
    </ToastContext.Provider>
  );
}
