// Zeitangaben im Feed. Relativ beantwortet "ist das noch frisch?", absolut
// beantwortet "wann genau?" — im Feed stehen beide nebeneinander, weil die
// eine Frage ohne die andere regelmäßig zur Rückfrage führte.

// Ab diesem Alter trägt die Jahreszahl etwas bei. Bewusst eine Altersgrenze
// und nicht das Kalenderjahr: mit dem Kalenderjahr bekäme am 2. Januar ein
// zwei Wochen alter Eintrag eine Jahreszahl, während am 31. Dezember ein fast
// zwölf Monate alter keine bekäme. Das Jahr erschiene dann danach, wo Silvester
// liegt, statt danach, wie alt der Eintrag ist.
const YEAR_RELEVANT_AFTER_DAYS = 90;

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

// "23.08., 14:20" — mit Jahreszahl erst, wenn der Eintrag alt genug ist, dass
// sie etwas beiträgt.
export function exactMoment(value) {
  const seen = toDate(value);
  if (!seen) return null;

  const ageInDays = Math.abs(Date.now() - seen.getTime()) / 86_400_000;
  const withYear = ageInDays > YEAR_RELEVANT_AFTER_DAYS;
  return seen.toLocaleString("de-DE", {
    day: "2-digit",
    month: "2-digit",
    ...(withYear ? { year: "numeric" } : {}),
    hour: "2-digit",
    minute: "2-digit",
  });
}

// Eine Definition für den ganzen Feed: Karten und die Liste der Ausgeblendeten
// stehen auf demselben Bildschirm, zwei Formatierer liefen dort auseinander.
export function money(value) {
  return typeof value === "number" && Number.isFinite(value)
    ? value.toLocaleString("de-DE", { style: "currency", currency: "EUR" })
    : null;
}

function toDate(value) {
  if (!value) return null;
  const parsed = new Date(value);
  return Number.isNaN(parsed.getTime()) ? null : parsed;
}
