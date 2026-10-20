import { api } from "../api/client";

// Hilfen fuer den Inserat-Weg: vorbereiten, kopieren, Fotos holen, filtern.
// ADR 0002: hier wird nie etwas eingestellt, nur zugearbeitet.

export const PLATFORM_LABELS = { KLEINANZEIGEN: "Kleinanzeigen", EBAY: "eBay" };
export const RUNNING_STATUSES = ["ACTIVE", "PAUSED"];

const DAY_MS = 86_400_000;

export function platformCreateUrl(item, platform) {
  if (platform === "EBAY") {
    const keyword = item.set_number ? `LEGO ${item.set_number}` : item.set_name;
    return `https://www.ebay.de/sell/create?${new URLSearchParams({ keyword, LH_BIN: "1" })}`;
  }
  return "https://www.kleinanzeigen.de/p-anzeige-aufgeben.html";
}

export function listingClipboardText(listing) {
  return [listing.title, listing.body].filter(Boolean).join("\n\n");
}

// Nach einem langen KI-Aufruf kann der Browser das Schreiben verweigern
// (Fenster nicht mehr im Fokus); der Aufrufer bietet dann den Knopf an.
export async function copyListingText(listing) {
  try {
    await navigator.clipboard.writeText(listingClipboardText(listing));
    return true;
  } catch {
    return false;
  }
}

function photoExtension(photo) {
  const fromName = /\.([a-z0-9]{2,5})$/i.exec(photo.original_filename || "");
  if (fromName) return fromName[1].toLowerCase();
  const fromType = /^image\/([a-z0-9]+)/i.exec(photo.content_type || "");
  return fromType ? fromType[1].replace("jpeg", "jpg") : "jpg";
}

// Laedt alle Fotos einzeln herunter, Titelbild zuerst. Chrome fragt beim
// ersten Mal, ob die Seite mehrere Dateien laden darf.
export function downloadPhotos(item) {
  const photos = item.photos || [];
  const base = (item.set_number || item.set_name || `posten-${item.id}`).replace(/[^\w-]+/g, "_").slice(0, 40);
  photos.forEach((photo, index) => {
    setTimeout(() => {
      const link = document.createElement("a");
      link.href = api.inventoryPhotoUrl(item.id, photo.id);
      link.download = `${base}-${index + 1}.${photoExtension(photo)}`;
      document.body.appendChild(link);
      link.click();
      link.remove();
    }, index * 300);
  });
  return photos.length;
}

export function openListing(item, platform) {
  return (item.listings || []).find(
    (x) => x.platform === platform && (RUNNING_STATUSES.includes(x.status) || x.status === "DRAFT"),
  );
}

// Tage seit der aeltesten laufenden Anzeige, null ohne laufende Anzeige.
export function daysListed(item, now = Date.now()) {
  const dates = (item.listings || [])
    .filter((x) => RUNNING_STATUSES.includes(x.status) && x.listed_at)
    .map((x) => new Date(x.listed_at).getTime());
  if (dates.length === 0) return null;
  return Math.max(0, Math.floor((now - Math.min(...dates)) / DAY_MS));
}

export function isListed(item) {
  return (item.listings || []).some((x) => RUNNING_STATUSES.includes(x.status));
}

export function hasDraft(item) {
  return (item.listings || []).some((x) => x.status === "DRAFT");
}

export const LISTING_FILTERS = [
  { value: "", label: "Alle" },
  { value: "unlisted", label: "Nicht inseriert" },
  { value: "draft", label: "Entwurf vorbereitet" },
  { value: "listed", label: "Inseriert" },
  { value: "listed14", label: "Inseriert seit über 14 Tagen" },
  { value: "listed30", label: "Inseriert seit über 30 Tagen" },
];

export function matchesListingFilter(item, filter, now = Date.now()) {
  switch (filter) {
    case "unlisted":
      return !isListed(item);
    case "draft":
      return !isListed(item) && hasDraft(item);
    case "listed":
      return isListed(item);
    case "listed14":
      return (daysListed(item, now) ?? -1) > 14;
    case "listed30":
      return (daysListed(item, now) ?? -1) > 30;
    default:
      return true;
  }
}

function normalize(value) {
  return (value || "").toString().toLocaleLowerCase("de-DE");
}

// Jedes Wort muss irgendwo vorkommen: "falcon kiste" findet den Falken in Kiste 3.
export function matchesSearch(item, query) {
  const words = normalize(query).split(/\s+/).filter(Boolean);
  if (words.length === 0) return true;
  const haystack = normalize(
    [item.set_number, item.set_name, item.theme, item.product_group, item.storage_location, item.notes].join(" "),
  );
  return words.every((word) => haystack.includes(word));
}
