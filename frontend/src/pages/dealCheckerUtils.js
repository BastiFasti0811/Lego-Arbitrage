export const verdictBg = {
  GO_STAR: "from-go-star/20 to-go-star/5 border-go-star/30",
  GO: "from-go/20 to-go/5 border-go/30",
  CHECK: "from-check/20 to-check/5 border-check/30",
  NO_GO: "from-no-go/20 to-no-go/5 border-no-go/30",
};

export const EURO = "\u20ac";
export const ICON_UP = "\u25b4";
export const ICON_DOWN = "\u25be";
export const ICON_RIGHT = "\u25b8";
export const ICON_CLOSE = "\u2715";
export const ICON_EXTERNAL = "\u2197";

export const formatMoney = (value, digits = 2) => `${Number(value).toFixed(digits)}${EURO}`;

export const formatAnalyzedAt = (value) =>
  new Date(value).toLocaleString("de-DE", { dateStyle: "short", timeStyle: "short" });

export const referenceLabelMap = {
  LEGO_UVP: "LEGO UVP",
  MARKT_KONSENS: "Markt-Konsens",
  ANGEBOT_PREIS: "Angebotspreis",
};

export function describeLearning(stats) {
  if (!stats?.completed_deals) {
    return "Sobald echte Verkaeufe vorliegen, kalibriert sich die ROI-Logik mit deinen Ergebnissen.";
  }

  if (stats.avg_roi_deviation == null) {
    return `${stats.completed_deals} Verkaeufe erfasst. Fuer die Kalibrierung fehlen noch genug Vergleichswerte.`;
  }

  if (stats.avg_roi_deviation < -2) {
    return `Bisher war das System im Schnitt ${Math.abs(stats.avg_roi_deviation).toFixed(1)} ROI-Punkte zu optimistisch.`;
  }

  if (stats.avg_roi_deviation > 2) {
    return `Bisher war das System im Schnitt ${stats.avg_roi_deviation.toFixed(1)} ROI-Punkte zu konservativ.`;
  }

  return `Bisher liegen Prognose und Realitaet im Schnitt nur ${Math.abs(stats.avg_roi_deviation).toFixed(1)} ROI-Punkte auseinander.`;
}

export const SHIPPING_PRESETS = [
  { label: "Kein Versand", value: 0 },
  { label: "DHL Paeckchen S", value: 3.99 },
  { label: "DHL Paket", value: 5.49 },
  { label: "Hermes S", value: 4.5 },
  { label: "Hermes M", value: 5.5 },
  { label: "DHL Paket L", value: 7.49 },
  { label: "Abholung", value: 0 },
];

export const isUrl = (str) => /^https?:\/\//.test(str.trim());
