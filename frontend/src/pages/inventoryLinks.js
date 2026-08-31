// Beide Links entstehen aus der Setnummer und brauchen deshalb keinen
// Speicher. Idealo ist bewusst nur ein Link: als Quelle antwortet die Seite
// vom Server aus in etwa einem von sechs Faellen und liefert dann Preise
// fremder Produkte von der Suchseite.
export function referenceLinks(item) {
  const links = [
    {
      label: "BrickMerge",
      href: `https://www.brickmerge.de/?find=${encodeURIComponent(item.set_number)}`,
    },
    {
      label: "Idealo",
      href:
        "https://www.idealo.de/preisvergleich/MainSearchProductCategory.html?q=" +
        encodeURIComponent(`LEGO ${item.set_number}`),
    },
  ];
  if (item.reference_url) {
    links.push({ label: "Eigener Link", href: item.reference_url });
  }
  return links;
}
