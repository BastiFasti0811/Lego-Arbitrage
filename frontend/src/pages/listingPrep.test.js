import { describe, expect, it } from "vitest";
import { active, draft, makeItem, photo } from "../test/fixtures";
import { filterInventory, hasPhoto, isReadyToPost, matchesPhotoFilter } from "./listingPrep";

describe("Foto- und Bereit-Regeln", () => {
  it("zaehlt nur hochgeladene Fotos, nicht den externen Foto-Link", () => {
    expect(hasPhoto(makeItem({ photos: [photo()] }))).toBe(true);
    expect(hasPhoto(makeItem({ image_url: "https://photos.example/1" }))).toBe(false);
    expect(matchesPhotoFilter(makeItem(), "without")).toBe(true);
    expect(matchesPhotoFilter(makeItem({ photos: [photo()] }), "without")).toBe(false);
    expect(matchesPhotoFilter(makeItem(), "")).toBe(true);
  });

  it("bereit zum Einstellen heisst Entwurf mit Preis und Foto ohne laufende Anzeige", () => {
    expect(isReadyToPost(makeItem({ photos: [photo()], listings: [draft(120)] }))).toBe(true);
    expect(isReadyToPost(makeItem({ listings: [draft(120)] }))).toBe(false); // ohne Foto
    expect(isReadyToPost(makeItem({ photos: [photo()], listings: [draft(0)] }))).toBe(false); // ohne Preis
    expect(isReadyToPost(makeItem({ photos: [photo()] }))).toBe(false); // ohne Entwurf
    expect(isReadyToPost(makeItem({ photos: [photo()], listings: [draft(120), active("EBAY")] }))).toBe(false);
  });
});

describe("filterInventory", () => {
  const plain = makeItem({ set_name: "Falke ohne alles" });
  const withPhoto = makeItem({ set_name: "Falke mit Foto", photos: [photo()] });
  const ready = makeItem({ set_name: "Bereit", photos: [photo()], listings: [draft(80)] });
  const draftNoPhoto = makeItem({ set_name: "Entwurf ohne Foto", listings: [draft(80)] });
  const listed = makeItem({ set_name: "Laeuft", listings: [active()] });
  const items = [plain, withPhoto, ready, draftNoPhoto, listed];
  const ids = (list) => list.map((item) => item.id);

  it("kombiniert 'nicht inseriert' mit 'ohne Foto'", () => {
    expect(ids(filterInventory(items, { listingFilter: "unlisted" }))).toEqual(
      ids([plain, withPhoto, ready, draftNoPhoto]),
    );
    expect(ids(filterInventory(items, { listingFilter: "unlisted", photoFilter: "without" }))).toEqual(
      ids([plain, draftNoPhoto]),
    );
  });

  it("kombiniert Suche, Foto- und Inserat-Filter", () => {
    expect(ids(filterInventory(items, { listingFilter: "ready" }))).toEqual(ids([ready]));
    expect(ids(filterInventory(items, { photoFilter: "with", search: "falke" }))).toEqual(ids([withPhoto]));
    expect(ids(filterInventory(items, {}))).toEqual(ids(items));
  });
});
