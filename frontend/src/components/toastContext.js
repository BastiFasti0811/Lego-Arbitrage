import { createContext, useContext } from "react";

export const ToastContext = createContext(null);

export function useToast() {
  const ctx = useContext(ToastContext);
  if (!ctx) throw new Error("useToast braucht einen ToastProvider");
  return ctx;
}
