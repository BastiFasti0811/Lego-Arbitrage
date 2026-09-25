// Hinterlegt einen Marktpreis nach seiner Herkunft (Backend: market_consensus.PriceBasis).
// Gruen: Konsens aus mindestens zwei Quellen (oder belastbare eBay-Verkaeufe).
// Gelb: nur BrickMerge-Bestpreis, weil es keinen belastbaren Konsens gibt.
// Ohne Basis bleibt der Preis unmarkiert.
const STYLES = {
  CONSENSUS: {
    className: "bg-go-star/15 ring-1 ring-go-star/40",
    title: "Marktpreis aus mindestens zwei übereinstimmenden Quellen",
  },
  EBAY_SOLD: {
    className: "bg-go-star/15 ring-1 ring-go-star/40",
    title: "Marktpreis aus belastbaren eBay-Verkäufen",
  },
  BRICKMERGE_ONLY: {
    className: "bg-lego-yellow/15 ring-1 ring-lego-yellow/60",
    title: "Nur BrickMerge-Bestpreis: kein Konsens aus mehreren Quellen",
    label: "nur BrickMerge",
  },
};

export default function PriceBasis({ basis, children, className = "" }) {
  const style = STYLES[basis];
  if (!style) return <span className={className}>{children}</span>;
  return (
    <span className={`inline-flex items-baseline gap-1.5 rounded px-1.5 py-0.5 ${style.className} ${className}`} title={style.title}>
      {children}
      {style.label && <span className="text-[10px] font-normal text-text-muted whitespace-nowrap">{style.label}</span>}
    </span>
  );
}
