// Anzeigenpreise sind meist glatt (Tages-Check rundet auf 1 bzw. 5 €):
// "45 €" statt "45,00 €", Cent nur wenn es welche gibt.
export function formatEuro(value) {
  if (value == null) return "—";
  return Number.isInteger(value) ? `${value} €` : `${value.toFixed(2).replace(".", ",")} €`;
}
