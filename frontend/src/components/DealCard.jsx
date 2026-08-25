import VerdictBadge from "./VerdictBadge";
import { exactMoment, money, relativeAge } from "../lib/datetime";

// Sonderzeichen stehen hier direkt als UTF-8 (die Datei ist UTF-8, index.html
// deklariert es). In der Preiszeile stand vorher die Escape-Sequenz fuer das
// Euro-Zeichen als JSX-Text. JSX wertet dort nichts aus, also erschien die
// Sequenz woertlich im Feed. Escapes greifen nur in String-Literalen.
const CONDITION_LABELS = {
  NEW_SEALED: "Neu versiegelt",
  NEW_OPEN_BOX: "Neu, geöffnet",
  USED_COMPLETE: "Gebraucht, komplett",
  USED_INCOMPLETE: "Gebraucht, unvollständig",
  // Ohne Eintrag verschwand das Badge — und mit ihm der einzige Hinweis
  // darauf, warum der erwartete Erlös 30 % unter dem Marktpreis liegt.
  UNKNOWN: "Zustand unbekannt",
};

const percent = (value) =>
  typeof value === "number" && Number.isFinite(value)
    ? `${value > 0 ? "+" : ""}${value.toLocaleString("de-DE", {
        minimumFractionDigits: 1,
        maximumFractionDigits: 1,
      })} %`
    : null;

const score = (value) =>
  typeof value === "number" && Number.isFinite(value)
    ? value.toLocaleString("de-DE", { maximumFractionDigits: 0 })
    : null;

export default function DealCard({ deal, onClick, onDismiss }) {
  const roi = deal.estimated_roi ?? deal.roi_percent;
  const roiColor = roi >= 30 ? "text-go-star" : roi >= 15 ? "text-go" : roi >= 0 ? "text-check" : "text-no-go";

  const price = money(deal.price ?? deal.offer_price);
  const marketPrice = money(deal.market_price);
  // Gegen die Basis vergleichen, auf der der Erlös gerechnet wurde, nicht
  // gegen market_price: im Live-Pfad ist das der Konsens, die Erlösrechnung
  // läuft aber gegen die Referenz. Der alte Vergleich blendete die Zeile
  // ausgerechnet dann aus, wenn die beiden auseinanderliefen.
  const priceBasis = deal.reference_price ?? deal.market_price;
  // Nur zeigen, wenn der Zustand den Erlös tatsächlich drückt — bei
  // versiegelter Ware wäre die zweite Zahl identisch und damit Rauschen.
  const expectedPrice =
    typeof deal.expected_sale_price === "number" &&
    typeof priceBasis === "number" &&
    deal.expected_sale_price < priceBasis
      ? money(deal.expected_sale_price)
      : null;
  const shipping = deal.shipping;
  const shippingLabel =
    typeof shipping === "number" ? (shipping > 0 ? `zzgl. ${money(shipping)} Versand` : "Versand frei") : null;

  // Der Titel des Inserats beantwortet die Frage, die der Setname offenlaesst:
  // ob hier das Set verkauft wird oder eine Anleitung mit derselben Nummer.
  const listingTitle = deal.offer_title && deal.offer_title !== deal.set_name ? deal.offer_title : null;

  // last_seen_at ist der letzte Lauf, in dem GENAU DIESES Inserat noch da war
  // — nicht der letzte Lauf ueberhaupt. Steht hier "vor 4 Tagen", waehrend der
  // Header einen Scan von heute frueh meldet, ist das Angebot weg. Darum
  // "zuletzt gesehen" statt einer nackten Zeitangabe.
  const seenAge = relativeAge(deal.last_seen_at);
  const meta = [
    deal.platform,
    CONDITION_LABELS[deal.condition],
    shippingLabel,
    seenAge && `zuletzt gesehen ${seenAge}`,
    exactMoment(deal.last_seen_at),
  ].filter(Boolean);

  const body = (
    <>
      <div className="flex items-start justify-between gap-3">
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-2 mb-1">
            <span className="text-lego-yellow font-[family-name:var(--font-mono)] text-sm font-semibold">
              {deal.set_number}
            </span>
            <VerdictBadge verdict={deal.recommendation} size="sm" />
          </div>
          <h3 className="text-text-primary text-sm font-medium truncate group-hover:text-lego-blue transition-colors">
            {deal.set_name || deal.offer_title}
          </h3>
        </div>
        <div className="text-right shrink-0">
          <div className="text-text-primary font-[family-name:var(--font-mono)] font-semibold">{price}</div>
          {marketPrice && (
            <div className="text-text-muted text-xs">
              {"Markt "}
              {marketPrice}
            </div>
          )}
          {expectedPrice && (
            <div className="text-check text-xs" title="Erwarteter Erlös in diesem Zustand">
              {"Erlös "}
              {expectedPrice}
            </div>
          )}
          <div className={`font-[family-name:var(--font-mono)] text-sm font-bold ${roiColor}`}>
            {percent(roi)}
          </div>
        </div>
      </div>

      {listingTitle && <p className="text-text-secondary text-xs mt-2 line-clamp-2">{listingTitle}</p>}

      {meta.length > 0 && (
        <div className="flex flex-wrap items-center gap-x-2 gap-y-1 mt-2">
          {meta.map((entry, index) => (
            <span key={`${index}-${entry}`} className="text-text-muted text-xs">
              {index > 0 && <span className="mr-2 text-border">{"·"}</span>}
              {entry}
            </span>
          ))}
        </div>
      )}

      {deal.reason && <p className="text-text-secondary text-xs mt-3 line-clamp-2">{deal.reason}</p>}

      <div className="flex items-center justify-between mt-3 pt-3 border-t border-border/50">
        <span className="text-text-muted text-xs">Risk {deal.risk_score}/10</span>
        <div className="flex items-center gap-3">
          <span className="text-text-muted text-xs">Score {score(deal.opportunity_score)}</span>
          {deal.offer_url && (
            <span className="text-lego-blue text-xs opacity-0 group-hover:opacity-100 transition-opacity">
              {"Angebot ↗"}
            </span>
          )}
        </div>
      </div>
    </>
  );

  const className =
    "block h-full bg-bg-card border border-border rounded-xl p-4 hover:border-lego-blue/50 transition-all group";

  const card = !deal.offer_url ? (
    <div onClick={onClick} className={className}>
      {body}
    </div>
  ) : (
    <a
      href={deal.offer_url}
      target="_blank"
      rel="noopener noreferrer"
      onClick={onClick}
      className={`${className} cursor-pointer`}
    >
      {body}
    </a>
  );

  // Ohne offer_url weist die Route die Abwahl mit 422 ab — dann gehoert da
  // auch kein Knopf hin, der nichts tun kann.
  if (!onDismiss || !deal.offer_url) return card;

  // Der Knopf liegt NEBEN dem Anker, nicht darin: ein <button> im <a> ist
  // ungueltiges HTML, und ein Klick darauf oeffnete trotzdem das Angebot. Die
  // negativen Offsets setzen ihn auf die Kartenecke, wo er weder den Preis
  // noch die Fusszeile verdeckt. Sichtbar bleibt er auch ohne Hover, sonst
  // waere er auf dem Handy nicht erreichbar.
  // text-text-secondary statt text-text-muted und opacity-90 statt 60: bei
  // 60 % kam der Knopf auf 1,6:1 gegen den Kartengrund — unlesbar, und auf
  // dem Handy hebt ihn kein Hover an.
  const label = deal.set_name || deal.offer_title || `Set ${deal.set_number}`;
  return (
    <div className="relative h-full">
      {card}
      <button
        type="button"
        onClick={() => onDismiss(deal)}
        title="Nicht mehr im Feed zeigen"
        aria-label={`${label} nicht mehr im Feed zeigen`}
        className="absolute -top-2 -right-2 z-10 flex h-6 w-6 items-center justify-center rounded-full border border-border bg-bg-hover text-text-secondary text-xs opacity-90 transition-opacity hover:opacity-100 hover:text-no-go focus:opacity-100 focus:outline-none focus:ring-2 focus:ring-lego-blue"
      >
        {"✕"}
      </button>
    </div>
  );
}
