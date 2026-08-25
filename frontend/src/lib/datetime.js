// Zeitangaben im Feed. Relativ beantwortet "ist das noch frisch?", absolut
// beantwortet "wann genau?" — im Feed stehen beide nebeneinander, weil die
// eine Frage ohne die andere regelmäßig zur Rückfrage führte.

export function relativeAge(value) {
  const seen = toDate(value);
  if (!seen) return null;

  const minutes = Math.round((Date.now() - seen.getTime()) / 60_000);
  if (minutes < 2) return "gerade eben";
  if (minutes < 60) return `vor ${minutes} Min.`;
  const hours = Math.round(minutes / 60);
  if (hours < 24) return `vor ${hours} Std.`;
  const days = Math.round(hours / 24);
  return days === 1 ? "vor 1 Tag" : `vor ${days} Tagen`;
}

// "23.08., 14:20" — mit Jahreszahl nur außerhalb des laufenden Jahres, sonst
// trägt sie in einer ohnehin dichten Zeile nichts bei.
export function exactMoment(value) {
  const seen = toDate(value);
  if (!seen) return null;

  const sameYear = seen.getFullYear() === new Date().getFullYear();
  return seen.toLocaleString("de-DE", {
    day: "2-digit",
    month: "2-digit",
    ...(sameYear ? {} : { year: "numeric" }),
    hour: "2-digit",
    minute: "2-digit",
  });
}

function toDate(value) {
  if (!value) return null;
  const parsed = new Date(value);
  return Number.isNaN(parsed.getTime()) ? null : parsed;
}
