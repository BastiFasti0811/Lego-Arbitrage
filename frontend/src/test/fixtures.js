// Testdaten fuer Inventar-Posten, so wie GET /api/inventory sie liefert.
let nextId = 1;

export function makeItem(overrides = {}) {
  const id = overrides.id ?? nextId++;
  return {
    id,
    item_type: "LEGO",
    set_number: String(10000 + id),
    set_name: `Set ${id}`,
    product_group: "Lego",
    theme: null,
    buy_date: "2026-01-10",
    buy_price: 50,
    buy_shipping: 0,
    total_invested: 50,
    holding_days: 100,
    quantity: 1,
    condition: "NEW_SEALED",
    status: "HOLDING",
    storage_location: null,
    notes: null,
    photos: [],
    listings: [],
    ...overrides,
  };
}

export const photo = (id = 1) => ({ id, original_filename: `p${id}.jpg` });

export const draft = (price = 99) => ({ id: 1, platform: "KLEINANZEIGEN", status: "DRAFT", current_price: price });

export const active = (platform = "KLEINANZEIGEN") => ({
  id: 2,
  platform,
  status: "ACTIVE",
  current_price: 99,
  listed_at: "2026-10-30",
});
