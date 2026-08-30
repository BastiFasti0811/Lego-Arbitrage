const PLATFORM_LABELS = { KLEINANZEIGEN: "KA", EBAY: "eBay" };

function daysSince(dateString) {
  if (!dateString) return null;
  const days = Math.floor((Date.now() - new Date(dateString).getTime()) / 86400000);
  return days < 0 ? 0 : days;
}

function BadgeContent({ listing }) {
  const label = PLATFORM_LABELS[listing.platform] || listing.platform;
  if (listing.status === "PAUSED") return `${label}: pausiert`;
  const days = daysSince(listing.listed_at);
  return `${label}: aktiv${days !== null ? ` seit ${days} T` : ""}`;
}

export default function ListingBadges({ item }) {
  const open = (item.listings || []).filter((x) => x.status === "ACTIVE" || x.status === "PAUSED");
  if (open.length === 0) {
    return <span className="text-xs text-text-secondary">nicht eingestellt</span>;
  }
  return (
    <div className="flex flex-wrap gap-1">
      {open.map((listing) => {
        const classes = `text-xs px-2 py-0.5 rounded-full border ${
          listing.at_floor
            ? "border-no-go text-no-go"
            : listing.status === "PAUSED"
              ? "border-border text-text-secondary"
              : "border-go-star text-go-star"
        }`;
        const content = (
          <>
            <BadgeContent listing={listing} />
            {listing.current_price != null && ` · ${Math.round(listing.current_price)}€`}
            {listing.at_floor && " · Schmerzgrenze"}
          </>
        );
        return listing.url ? (
          <a key={listing.id} href={listing.url} target="_blank" rel="noreferrer" className={`${classes} hover:underline`}>
            {content} {"↗"}
          </a>
        ) : (
          <span key={listing.id} className={classes}>{content}</span>
        );
      })}
    </div>
  );
}
