import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import SalesCockpit from "./SalesCockpit";

const cockpit = (overrides = {}) => ({
  lego_holding: 49,
  lego_without_photo: 42,
  drafts_ready: 4,
  posting_today: {
    posted: 2,
    started: 2,
    max_per_day: 3,
    waiting_approval: 1,
    queued: 0,
    failed: 0,
    block: null,
  },
  candidates: [
    { item_id: 7, set_number: "42055", set_name: "Schaufelradbagger", suggested_price: 449, photo_count: 3, storage_location: "Kiste 3" },
    { item_id: 8, set_number: "75251", set_name: "Darth Vaders Festung", suggested_price: 239, photo_count: 1, storage_location: null },
  ],
  ...overrides,
});

describe("SalesCockpit", () => {
  it("zeigt die Kopfzahlen: ohne Foto, Entwuerfe bereit, Auftraege heute und wartend", () => {
    render(<SalesCockpit cockpit={cockpit()} onPrepare={() => {}} />);
    const section = screen.getByRole("region", { name: "Verkaufs-Cockpit" });

    expect(within(section).getByText("42/49")).toBeInTheDocument();
    expect(within(section).getByText("4")).toBeInTheDocument();
    expect(within(section).getByText("2/3")).toBeInTheDocument();
    expect(within(section).getByText("wartend 1")).toBeInTheDocument();
  });

  it("springt per Klick in die passenden Filter", async () => {
    const onShowWithoutPhoto = vi.fn();
    const onShowReady = vi.fn();
    render(
      <SalesCockpit cockpit={cockpit()} onShowWithoutPhoto={onShowWithoutPhoto} onShowReady={onShowReady} onPrepare={() => {}} />,
    );

    await userEvent.click(screen.getByRole("button", { name: /Lego ohne Foto zeigen/ }));
    await userEvent.click(screen.getByRole("button", { name: /Entwürfe bereit zum Einstellen zeigen/ }));

    expect(onShowWithoutPhoto).toHaveBeenCalledTimes(1);
    expect(onShowReady).toHaveBeenCalledTimes(1);
  });

  it("listet die Kandidaten und bereitet per Klick das Inserat vor", async () => {
    const onPrepare = vi.fn();
    render(<SalesCockpit cockpit={cockpit()} onPrepare={onPrepare} />);

    const list = screen.getByRole("list", { name: "Nächste 3 zum Inserieren" });
    expect(within(list).getAllByRole("listitem")).toHaveLength(2);
    expect(within(list).getByText("Schaufelradbagger")).toBeInTheDocument();

    await userEvent.click(screen.getByRole("button", { name: "Inserat vorbereiten: 42055 Schaufelradbagger" }));
    expect(onPrepare).toHaveBeenCalledWith(expect.objectContaining({ item_id: 7 }));
  });

  it("nennt Freigegebene, Fehlschlaege und die Tagessperre", () => {
    render(
      <SalesCockpit
        cockpit={cockpit({
          posting_today: { posted: 3, started: 3, max_per_day: 3, waiting_approval: 0, queued: 1, failed: 1, block: "Tageslimit erreicht (3 Inserate) — morgen weiter" },
        })}
        onPrepare={() => {}}
      />,
    );
    expect(screen.getByText("wartend 0 · 1 freigegeben · 1 fehlgeschlagen")).toBeInTheDocument();
    expect(screen.getByText(/Tageslimit erreicht/)).toBeInTheDocument();
  });

  it("verweist ohne Kandidaten aufs Fotografieren", () => {
    render(<SalesCockpit cockpit={cockpit({ candidates: [] })} onPrepare={() => {}} />);
    expect(screen.getByText(/Als Nächstes fotografieren: 42 ohne Foto/)).toBeInTheDocument();
  });
});
