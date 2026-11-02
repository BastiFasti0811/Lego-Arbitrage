import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { ToastProvider } from "../components/Toast";
import { draft, makeItem, photo } from "../test/fixtures";
import Inventar from "./Inventar";

vi.mock("../api/client", () => ({
  api: {
    listPlatforms: vi.fn(),
    listProductGroups: vi.fn(),
    portfolioSummary: vi.fn(),
    goalProgress: vi.fn(),
    salesCockpit: vi.fn(),
    listInventory: vi.fn(),
    prepareListing: vi.fn(),
    inventoryPhotoUrl: (itemId, photoId) => `/api/inventory/${itemId}/photos/${photoId}`,
  },
}));
// Eigene Abfragen, hier nicht Gegenstand des Tests.
vi.mock("../components/ValuationStatus", () => ({ default: () => null }));
vi.mock("../components/ListingManager", () => ({
  default: ({ item }) => <div role="dialog" aria-label="Listings">{item.set_name}</div>,
}));

const { api } = await import("../api/client");

const bagger = makeItem({ id: 1, set_number: "42055", set_name: "Schaufelradbagger", photos: [photo(1)] });
const festung = makeItem({ id: 2, set_number: "75251", set_name: "Festung", photos: [photo(2)] });
const kranOhneFoto = makeItem({ id: 3, set_number: "42009", set_name: "Kran" });
const fertig = makeItem({ id: 4, set_number: "31109", set_name: "Piratenschiff", photos: [photo(4)], listings: [draft(93)] });
const radio = makeItem({ id: 5, item_type: "GENERIC", set_number: null, product_group: "Elektronik", set_name: "Radio" });

const candidate = (item, price) => ({
  item_id: item.id,
  set_number: item.set_number,
  set_name: item.set_name,
  suggested_price: price,
  photo_count: item.photos.length,
  storage_location: null,
});

const cockpit = (candidates) => ({
  lego_holding: 4,
  lego_without_photo: 1,
  drafts_ready: 1,
  posting_today: { posted: 0, started: 0, max_per_day: 3, waiting_approval: 0, queued: 0, failed: 0, block: null },
  candidates,
});

function renderPage() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <ToastProvider>
        <Inventar />
      </ToastProvider>
    </QueryClientProvider>,
  );
}

const visibleNames = () =>
  screen.queryAllByRole("heading", { level: 3 }).map((heading) => heading.textContent);

beforeEach(() => {
  vi.clearAllMocks();
  api.listPlatforms.mockResolvedValue([]);
  api.listProductGroups.mockResolvedValue(["Elektronik", "Lego"]);
  api.portfolioSummary.mockResolvedValue(null);
  api.goalProgress.mockResolvedValue(null);
  api.salesCockpit.mockResolvedValue(cockpit([candidate(bagger, 449), candidate(festung, 239)]));
  api.listInventory.mockImplementation(async ({ item_type } = {}) =>
    [bagger, festung, kranOhneFoto, fertig, radio].filter((item) => !item_type || item.item_type === item_type),
  );
});

describe("Inventar-Seite", () => {
  it("kombiniert Foto-, Inserat- und Typ-Filter", async () => {
    renderPage();
    await screen.findByText("Schaufelradbagger", { selector: "h3" });
    expect(visibleNames()).toHaveLength(5);

    await userEvent.selectOptions(screen.getByLabelText("Foto"), "without");
    expect(visibleNames()).toEqual(["Kran", "Radio"]);

    await userEvent.selectOptions(screen.getByLabelText("Typ"), "LEGO");
    await waitFor(() => expect(visibleNames()).toEqual(["Kran"]));

    await userEvent.selectOptions(screen.getByLabelText("Foto"), "");
    await userEvent.selectOptions(screen.getByLabelText("Inserat"), "ready");
    expect(visibleNames()).toEqual(["Piratenschiff"]);
    expect(screen.getByText("1 von 4")).toBeInTheDocument();
  });

  it("setzt per Klick auf 'ohne Foto' im Kopf die Filter Lego + ohne Foto", async () => {
    renderPage();
    await userEvent.click(await screen.findByRole("button", { name: /Lego ohne Foto zeigen/ }));

    await waitFor(() => expect(visibleNames()).toEqual(["Kran"]));
    expect(screen.getByLabelText("Typ")).toHaveValue("LEGO");
    expect(screen.getByLabelText("Foto")).toHaveValue("without");
  });

  it("'Naechste 3' aendert sich nach 'Inserat vorbereiten'", async () => {
    api.prepareListing.mockResolvedValue({
      listing: { title: "LEGO 42055", body: "Text", current_price: 449 },
      warning: null,
    });
    renderPage();
    const list = await screen.findByRole("list", { name: "Nächste 3 zum Inserieren" });
    expect(within(list).getByText("Schaufelradbagger")).toBeInTheDocument();

    // Nach dem Vorbereiten hat der Bagger einen Entwurf: das Backend nimmt ihn heraus.
    api.salesCockpit.mockResolvedValue(cockpit([candidate(festung, 239)]));
    await userEvent.click(within(list).getByRole("button", { name: "Inserat vorbereiten: 42055 Schaufelradbagger" }));

    expect(api.prepareListing).toHaveBeenCalledWith(1, "KLEINANZEIGEN", null);
    await waitFor(() => expect(within(list).queryByText("Schaufelradbagger")).not.toBeInTheDocument());
    expect(within(list).getByText("Festung")).toBeInTheDocument();
    expect(screen.getByRole("dialog", { name: "Listings" })).toHaveTextContent("Schaufelradbagger");
  });
});
