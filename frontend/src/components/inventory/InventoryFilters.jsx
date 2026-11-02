import { LISTING_FILTERS, PHOTO_FILTERS } from "../../pages/listingPrep";

// Filterleiste des Inventars. Typ und Warengruppe filtert das Backend,
// Inserat, Foto und Suche die Seite (filterInventory); alle sind kombinierbar.
export default function InventoryFilters({
  typeFilter,
  setTypeFilter,
  groupFilter,
  setGroupFilter,
  listingFilter,
  setListingFilter,
  photoFilter,
  setPhotoFilter,
  search,
  setSearch,
  productGroups,
  totalCount,
  visibleCount,
}) {
  return (
    <div className="bg-bg-card border border-border rounded-xl p-4 mb-6 flex flex-wrap items-center gap-4">
      <div>
        <label htmlFor="inventory-type-filter" className="block text-text-muted text-xs mb-1">Typ</label>
        <select
          id="inventory-type-filter"
          value={typeFilter}
          onChange={(e) => setTypeFilter(e.target.value)}
          className="bg-bg-primary border border-border rounded-lg px-3 py-2 text-text-primary text-sm"
        >
          <option value="">Alle</option>
          <option value="LEGO">Lego</option>
          <option value="GENERIC">Sonstiges</option>
        </select>
      </div>
      <div>
        <label htmlFor="inventory-group-filter" className="block text-text-muted text-xs mb-1">Warengruppe</label>
        <select
          id="inventory-group-filter"
          value={groupFilter}
          onChange={(e) => setGroupFilter(e.target.value)}
          className="bg-bg-primary border border-border rounded-lg px-3 py-2 text-text-primary text-sm"
        >
          <option value="">Alle</option>
          {productGroups.map((group) => (
            <option key={group} value={group}>{group}</option>
          ))}
        </select>
      </div>
      <div>
        <label htmlFor="inventory-listing-filter" className="block text-text-muted text-xs mb-1">Inserat</label>
        <select
          id="inventory-listing-filter"
          value={listingFilter}
          onChange={(e) => setListingFilter(e.target.value)}
          className="bg-bg-primary border border-border rounded-lg px-3 py-2 text-text-primary text-sm"
        >
          {LISTING_FILTERS.map((f) => (
            <option key={f.value} value={f.value}>{f.label}</option>
          ))}
        </select>
      </div>
      <div>
        <label htmlFor="inventory-photo-filter" className="block text-text-muted text-xs mb-1">Foto</label>
        <select
          id="inventory-photo-filter"
          value={photoFilter}
          onChange={(e) => setPhotoFilter(e.target.value)}
          className="bg-bg-primary border border-border rounded-lg px-3 py-2 text-text-primary text-sm"
        >
          {PHOTO_FILTERS.map((f) => (
            <option key={f.value} value={f.value}>{f.label}</option>
          ))}
        </select>
      </div>
      <div className="flex-1 min-w-[12rem]">
        <label htmlFor="inventory-search" className="block text-text-muted text-xs mb-1">Suche</label>
        <input
          id="inventory-search"
          type="search"
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          placeholder="Setnummer, Name, Lagerort …"
          className="w-full bg-bg-primary border border-border rounded-lg px-3 py-2 text-text-primary text-sm"
        />
      </div>
      {totalCount > 0 && (
        <p className="text-text-muted text-xs self-end pb-2" aria-live="polite">
          {visibleCount} von {totalCount}
        </p>
      )}
    </div>
  );
}
