import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { beforeEach, describe, expect, it, vi } from "vitest";
import SystemStatus from "./SystemStatus";

vi.mock("../api/client", () => ({
  api: {
    health: vi.fn(),
    pipelineStatus: vi.fn(),
  },
}));

const { api } = await import("../api/client");

function renderStatus() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <SystemStatus />
    </QueryClientProvider>,
  );
}

const okTask = {
  task_name: "app.tasks.scrape_daily.scrape_kleinanzeigen_watched",
  status: "ok",
  last_success_at: "2026-11-02T07:11:57Z",
  last_run_at: "2026-11-02T07:11:57Z",
  last_status: "success",
  age_seconds: 3800,
  max_age_seconds: 18000,
  detail: "{'total_sets': 31, 'offers': 0, 'errors': [], 'aborted': False}",
};

// Prod 02.11.2026: der Task meldet Erfolg, die Quelle liefert seit zwei Monaten nichts.
const sourceProblem = {
  task_name: "pipeline.offers_kleinanzeigen",
  status: "stale",
  last_success_at: "2026-09-01T08:10:00Z",
  last_run_at: "2026-11-02T07:11:57Z",
  last_status: null,
  age_seconds: 1488 * 3600,
  max_age_seconds: 86400,
  detail:
    "Kleinanzeigen: In 24 h gescannt (zuletzt 02.11.2026 08:11), aber kein Angebot gesehen; letztes: 01.09.2026 10:10",
};

beforeEach(() => {
  api.health.mockResolvedValue({ status: "healthy", version: "0123456789abcdef0123456789abcdef01234567" });
});

describe("SystemStatus", () => {
  it("zeigt eine Quelle ohne Ergebnis mit ihrem Befund statt einer Laufzeit", async () => {
    api.pipelineStatus.mockResolvedValue({ healthy: false, tasks: [okTask, sourceProblem], sources: [] });
    renderStatus();

    const button = await screen.findByRole("button", { name: /1 Problem/ });
    await userEvent.click(button);

    expect(screen.getByText("Kleinanzeigen-Angebote")).toBeInTheDocument();
    expect(screen.getByText(/ohne Ergebnis/)).toBeInTheDocument();
    expect(screen.getByText(/aber kein Angebot gesehen; letztes: 01\.09\.2026/)).toBeInTheDocument();
    // "letzter erfolgreicher Lauf vor 62 Tage" waere fuer einen Task gedacht, der nicht laeuft.
    expect(screen.queryByText(/letzter erfolgreicher Lauf/)).not.toBeInTheDocument();
  });

  it("bleibt bei einem ueberfaelligen Task bei der Laufzeit", async () => {
    const staleTask = { ...okTask, status: "stale", age_seconds: 7 * 3600 };
    api.pipelineStatus.mockResolvedValue({ healthy: false, tasks: [staleTask], sources: [] });
    renderStatus();

    await userEvent.click(await screen.findByRole("button", { name: /1 Problem/ }));

    expect(screen.getByText("Kleinanzeigen-Scan")).toBeInTheDocument();
    expect(screen.getByText("letzter erfolgreicher Lauf vor 7 h")).toBeInTheDocument();
  });
});
