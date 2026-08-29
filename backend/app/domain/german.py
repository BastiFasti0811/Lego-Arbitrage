"""Deutsche Zahlen- und Waehrungsformatierung."""


def format_eur(value: float) -> str:
    """Deutscher Eurobetrag: 29,74 € und 1.234,56 €.

    Erst die Zahl umstellen, dann das Zeichen anhaengen. Ein
    Zwischentausch ueber ein Trennzeichen wuerde auch gehen, braucht
    dafuer aber ein Zeichen, das sonst nicht vorkommt — und ein
    unsichtbares im Quelltext ist eine Falle beim Abtippen.
    """
    formatted = f"{value:,.2f}".replace(",", "_").replace(".", ",").replace("_", ".")
    return f"{formatted} €"
