import { exactMoment, relativeAge } from "../lib/datetime";

// Alter eines gespeicherten Marktwerts. Ob er veraltet ist, entscheidet der
// Server (`market_price_stale`, Regel in app/domain/market_freshness.py), damit
// Oberfläche, Bewertungslauf und Listing-Check nicht auseinanderlaufen.
export default function MarketAge({ updatedAt, stale }) {
  const age = relativeAge(updatedAt);
  const exact = exactMoment(updatedAt);

  if (stale) {
    return (
      <div
        className="text-check text-[11px] mt-0.5"
        title={
          exact
            ? `Letzter belastbarer Preis am ${exact}. Seitdem hat kein Bewertungslauf einen Wert bestätigt.`
            : "Kein Zeitstempel: unbekannt, von wann dieser Wert stammt."
        }
      >
        veraltet{age ? ` · ${age}` : ""}
      </div>
    );
  }
  if (!age) return null;
  return (
    <div className="text-text-muted text-[11px] mt-0.5" title={exact ? `Stand ${exact}` : undefined}>
      Stand {age}
    </div>
  );
}
