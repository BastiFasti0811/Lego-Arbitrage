const BASE = "/api";

async function request(path, options = {}) {
  const res = await fetch(`${BASE}${path}`, {
    headers: { "Content-Type": "application/json", ...options.headers },
    ...options,
  });
  if (!res.ok) {
    if (res.status === 401 && !path.startsWith("/auth/login")) {
      window.location.href = "/login";
      return;
    }
    const err = await res.json().catch(() => ({ detail: res.statusText }));
    throw new Error(err.detail || `API Error ${res.status}`);
  }
  return res.json();
}

export const api = {
  // Auth
  login: (password) => request("/auth/login", { method: "POST", body: JSON.stringify({ password }) }),
  logout: () => request("/auth/logout", { method: "POST" }),
  checkAuth: () => request("/auth/check"),

  // Health
  health: () => fetch("/health").then((r) => r.json()),

  // Analysis
  analyze: (data) => request("/analysis/analyze", { method: "POST", body: JSON.stringify(data) }),
  lookupSet: (setNumber) => request(`/analysis/lookup/${setNumber}`),
  parseUrl: (url) => request("/analysis/parse-url", { method: "POST", body: JSON.stringify({ url }) }),
  sellerCheck: (sellerUrl) => request("/analysis/seller-check", { method: "POST", body: JSON.stringify({ seller_url: sellerUrl }) }),
  analyzeMulti: (data) => request("/analysis/analyze-multi", { method: "POST", body: JSON.stringify(data) }),
  analysisHistory: () => request("/analysis/history"),

  // Scout
  scoutQuick: (setNumber) => request(`/scout/quick/${setNumber}`),
  scoutScan: (data) => request("/scout/scan", { method: "POST", body: JSON.stringify(data) }),
  feedList: (setNumbers) =>
    request("/scout/scan", {
      method: "POST",
      body: JSON.stringify({ set_numbers: setNumbers, min_roi: 0, cached_only: true }),
    }),

  // Sets
  listSets: (params) => request(`/sets/?${new URLSearchParams(params)}`),
  getSet: (setNumber) => request(`/sets/${setNumber}`),

  // Inventory
  listInventory: (params = {}) => request(`/inventory/?${new URLSearchParams(params)}`),
  listPlatforms: () => request("/inventory/platforms"),
  addInventory: (data) => request("/inventory/", { method: "POST", body: JSON.stringify(data) }),
  updateInventory: (id, data) => request(`/inventory/${id}`, { method: "PATCH", body: JSON.stringify(data) }),
  sellInventory: (id, data) => request(`/inventory/${id}/sell`, { method: "POST", body: JSON.stringify(data) }),
  deleteInventory: (id) => request(`/inventory/${id}`, { method: "DELETE" }),
  getSellLinks: (id) => request(`/inventory/${id}/sell-links`),
  portfolioSummary: () => request("/inventory/summary"),
  inventoryHistory: () => request("/inventory/history"),

  // Settings
  listSettings: (category) => request(`/settings/${category ? `?category=${category}` : ""}`),
  updateSettings: (updates) => request("/settings/", { method: "PUT", body: JSON.stringify(updates) }),
  testTelegram: () => request("/settings/test-telegram", { method: "POST" }),

  // Watchlist
  listWatchlist: () => request("/watchlist/"),
  addWatchlist: (data) => request("/watchlist/", { method: "POST", body: JSON.stringify(data) }),
  removeWatchlist: (id) => request(`/watchlist/${id}`, { method: "DELETE" }),
};
