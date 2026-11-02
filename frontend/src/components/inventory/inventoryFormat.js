// Gemeinsame Konstanten und Hilfen der Inventar-Komponenten.

export const EURO = "€";
export const PHOTO_ICON = "\u{1F4F8}";
export const BOX_ICON = "\u{1F4E6}";
export const LINK_ICON = "↗";
export const MAX_PREVIEW_PHOTOS = 4;
export const SELL_PLATFORM_QUICK_PICKS = [
  { value: "KLEINANZEIGEN", label: "Kleinanzeigen" },
  { value: "EBAY", label: "eBay" },
];

export const emptyAddForm = () => ({
  item_type: "LEGO",
  set_number: "",
  set_name: "",
  theme: "",
  product_group: "",
  buy_price: "",
  buy_shipping: "0",
  buy_date: new Date().toISOString().split("T")[0],
  buy_platform: "",
  buy_url: "",
  image_url: "",
  condition: "NEW_SEALED",
  quantity: "1",
  notes: "",
  storage_location: "",
});

export function normalizeLocation(value) {
  return (value || "").trim().toLocaleLowerCase("de-DE");
}

// "10.01.2026, 19.99 €, Lagerort Kiste 3" -- eine Dublette im Hinweis.
export function describeDuplicate(d) {
  const price = d.buy_price != null ? `${d.buy_price} €` : "ohne Kaufpreis";
  const location = d.storage_location ? `, Lagerort ${d.storage_location}` : "";
  return `${new Date(d.buy_date).toLocaleDateString("de-DE")}, ${price}${location}`;
}

export function formatMoney(value, digits = 0) {
  return `${Number(value || 0).toLocaleString("de-DE", {
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  })}${EURO}`;
}

export const profitColor = (value) => (value > 0 ? "text-go-star" : value < 0 ? "text-no-go" : "text-text-primary");

export function createLocalPhotoEntries(fileList) {
  return Array.from(fileList).map((file, index) => ({
    id: `${file.name}-${file.lastModified}-${index}-${Math.random().toString(36).slice(2, 7)}`,
    file,
    previewUrl: URL.createObjectURL(file),
  }));
}

export function revokeLocalPhotoEntries(entries) {
  entries.forEach((entry) => URL.revokeObjectURL(entry.previewUrl));
}
