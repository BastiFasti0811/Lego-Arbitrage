const BASE = "/api";

async function request(path, options = {}) {
  const res = await fetch(`${BASE}${path}`, {
    headers: { "Content-Type": "application/json", ...options.headers },
    ...options,
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: res.statusText }));
    throw new Error(err.detail || `API Error ${res.status}`);
  }
  return res.json();
}

export const api = {
  // Health
  health: () => fetch("/health").then((r) => r.json()),

  // Analysis
  analyze: (data) => request("/analysis/analyze", { method: "POST", body: JSON.stringify(data) }),

  // Scout
  scoutQuick: (setNumber) => request(`/scout/quick/${setNumber}`),
  scoutScan: (data) => request("/scout/scan", { method: "POST", body: JSON.stringify(data) }),

  // Sets
  listSets: (params) => request(`/sets/?${new URLSearchParams(params)}`),
  getSet: (setNumber) => request(`/sets/${setNumber}`),

  // Inventory
  listInventory: (params = {}) => request(`/inventory/?${new URLSearchParams(params)}`),
  addInventory: (data) => request("/inventory/", { method: "POST", body: JSON.stringify(data) }),
  updateInventory: (id, data) => request(`/inventory/${id}`, { method: "PATCH", body: JSON.stringify(data) }),
  sellInventory: (id, data) => request(`/inventory/${id}/sell`, { method: "POST", body: JSON.stringify(data) }),
  deleteInventory: (id) => request(`/inventory/${id}`, { method: "DELETE" }),
  portfolioSummary: () => request("/inventory/summary"),
  inventoryHistory: () => request("/inventory/history"),

  // Watchlist
  listWatchlist: () => request("/watchlist/"),
  addWatchlist: (data) => request("/watchlist/", { method: "POST", body: JSON.stringify(data) }),
  removeWatchlist: (id) => request(`/watchlist/${id}`, { method: "DELETE" }),
};
