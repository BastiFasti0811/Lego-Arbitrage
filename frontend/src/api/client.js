const APP_BASENAME = (import.meta.env.VITE_APP_BASENAME || "").replace(/\/$/, "");
const BASE = import.meta.env.VITE_API_BASE || (APP_BASENAME ? `${APP_BASENAME}/api` : "/api");
const HEALTH_URL = import.meta.env.VITE_HEALTH_URL || (APP_BASENAME ? `${APP_BASENAME}/health` : "/health");
const LOGIN_PATH = APP_BASENAME ? `${APP_BASENAME}/login` : "/login";
const MAX_UPLOAD_DIMENSION = 1600;
const JPEG_UPLOAD_QUALITY = 0.82;

async function request(path, options = {}) {
  const res = await fetch(`${BASE}${path}`, {
    headers: { "Content-Type": "application/json", ...options.headers },
    ...options,
  });
  if (!res.ok) {
    if (res.status === 401 && !path.startsWith("/auth/login")) {
      window.location.href = LOGIN_PATH;
      return;
    }
    const err = await res.json().catch(() => ({ detail: res.statusText }));
    throw new Error(err.detail || `API Error ${res.status}`);
  }
  return res.json();
}

function fileToDataUrl(file) {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(reader.result);
    reader.onerror = () => reject(new Error(`Datei konnte nicht gelesen werden: ${file.name}`));
    reader.readAsDataURL(file);
  });
}

async function optimizeImageFile(file) {
  if (file.type === "image/gif") {
    return file;
  }

  const needsResize = typeof createImageBitmap === "function" && file.type.startsWith("image/");
  if (!needsResize) {
    return file;
  }

  try {
    const bitmap = await createImageBitmap(file);
    const longestSide = Math.max(bitmap.width, bitmap.height);
    const scale = longestSide > MAX_UPLOAD_DIMENSION ? MAX_UPLOAD_DIMENSION / longestSide : 1;
    const targetWidth = Math.max(1, Math.round(bitmap.width * scale));
    const targetHeight = Math.max(1, Math.round(bitmap.height * scale));

    if (scale === 1 && file.size <= 2 * 1024 * 1024) {
      return file;
    }

    const canvas = document.createElement("canvas");
    canvas.width = targetWidth;
    canvas.height = targetHeight;
    const ctx = canvas.getContext("2d");
    if (!ctx) {
      return file;
    }
    ctx.drawImage(bitmap, 0, 0, targetWidth, targetHeight);

    const convertedBlob = await new Promise((resolve) => {
      canvas.toBlob(resolve, "image/jpeg", JPEG_UPLOAD_QUALITY);
    });
    if (typeof bitmap.close === "function") {
      bitmap.close();
    }

    if (!convertedBlob) {
      return file;
    }

    const optimizedName = file.name.replace(/\.[^.]+$/, "") || "foto";
    return new File([convertedBlob], `${optimizedName}.jpg`, {
      type: "image/jpeg",
      lastModified: file.lastModified,
    });
  } catch {
    return file;
  }
}

export const api = {
  // Auth
  login: (password) => request("/auth/login", { method: "POST", body: JSON.stringify({ password }) }),
  logout: () => request("/auth/logout", { method: "POST" }),
  checkAuth: () => request("/auth/check"),

  // Health
  health: () => fetch(HEALTH_URL).then((r) => r.json()),

  // Analysis
  analyze: (data) => request("/analysis/analyze", { method: "POST", body: JSON.stringify(data) }),
  lookupSet: (setNumber) => request(`/analysis/lookup/${setNumber}`),
  lookupCode: (code) => request("/analysis/lookup-code", { method: "POST", body: JSON.stringify({ code }) }),
  parseUrl: (url) => request("/analysis/parse-url", { method: "POST", body: JSON.stringify({ url }) }),
  auctionMaxBid: (data) => request("/analysis/auction-max-bid", { method: "POST", body: JSON.stringify(data) }),
  sellerCheck: (sellerUrl) => request("/analysis/seller-check", { method: "POST", body: JSON.stringify({ seller_url: sellerUrl }) }),
  analyzeMulti: (data) => request("/analysis/analyze-multi", { method: "POST", body: JSON.stringify(data) }),
  analysisHistory: () => request("/analysis/history"),

  // Auctions
  listAuctionWatch: () => request("/auctions/"),
  addAuctionWatch: (data) => request("/auctions/", { method: "POST", body: JSON.stringify(data) }),
  updateAuctionWatch: (id, data) => request(`/auctions/${id}`, { method: "PATCH", body: JSON.stringify(data) }),
  refreshAuctionWatch: (id) => request(`/auctions/${id}/refresh`, { method: "POST" }),
  removeAuctionWatch: (id) => request(`/auctions/${id}`, { method: "DELETE" }),
  discoverAuctions: (data) => request("/auctions/discover", { method: "POST", body: JSON.stringify(data) }),

  // Feedback
  feedbackPerformance: () => request("/feedback/performance"),

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
  uploadInventoryPhotos: async (id, files) => {
    const optimizedFiles = await Promise.all(files.map((file) => optimizeImageFile(file)));
    const photos = await Promise.all(
      optimizedFiles.map(async (file) => ({
        filename: file.name,
        content_type: file.type,
        data_url: await fileToDataUrl(file),
      })),
    );
    return request(`/inventory/${id}/photos`, { method: "POST", body: JSON.stringify({ photos }) });
  },
  deleteInventoryPhoto: (itemId, photoId) => request(`/inventory/${itemId}/photos/${photoId}`, { method: "DELETE" }),
  makePrimaryInventoryPhoto: (itemId, photoId) => request(`/inventory/${itemId}/photos/${photoId}/make-primary`, { method: "POST" }),
  inventoryPhotoUrl: (itemId, photoId) => `${BASE}/inventory/${itemId}/photos/${photoId}`,
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
