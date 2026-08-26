"""Anzeigentexte mit dem Urteil, das sie bekommen sollen.

Gewachsen aus drei Runden Regex-Nachbesserung an denselben zwei Funktionen.
Jede Runde hat echte Fehler behoben und neue Luecken offengelassen, weil
niemand sehen konnte, wie gross die Abdeckung ist. Hier steht sie: eine neue
Formulierung kostet eine Zeile, und die Trefferquote ist ablesbar.

Die Faelle laufen ueber die beiden oeffentlichen Seams (`is_set_offer` und
`classify_listing_condition`) — nie gegen die Muster darunter, die genau
deshalb austauschbar bleiben sollen.
"""

# Notre Dame 10326: 271 EUR Marktpreis (Stand 2026-08), 1500 EUR ist die
# Preisklasse, in der die Titel dieser Sammlung spielen.
SET = "10326"
REFERENCE_PRICE = 271.0
# Klar ueber dem 8-%-Preisboden: der Boden soll in diesen Faellen nie
# mitreden, es geht um Worterkennung, nicht um Schwellen.
PLAUSIBLE_PRICE = 200.0

# (Titel, Abholort, Begruendung) — Zubehoer, das nie das Set selbst ist.
ACCESSORIES = [
    (
        "Stickerbogen passend fuer Lego 10326",
        None,
        "ue-Transkription: 'fuer' ist dasselbe Wort wie 'für'",
    ),
    (
        "Beleuchtung fuer 10326",
        None,
        "ue-Transkription im 'fuer <Setnummer>'-Muster",
    ),
]

# (Titel, Abholort, Begruendung) — echte Einzelset-Angebote. Fallen sie durch,
# merkt es niemand: sie werden nicht gespeichert und erscheinen nie im Feed.
SINGLE_SETS = [
    (
        "LEGO 10326 Notre Dame für 1500",
        None,
        "'für 1500' ist der Preis, kein Verweis auf ein Set",
    ),
    (
        "LEGO 10326 Notre Dame 1500 oder VB",
        None,
        "Trennwort zwischen Zahl und Einheit: 'oder VB' ist dieselbe Preisangabe",
    ),
    (
        "LEGO 10326 Notre Dame 1500 - VB",
        None,
        "Gedankenstrich zwischen Zahl und Einheit",
    ),
    (
        "LEGO 10326 Notre Dame 1500 / VB",
        None,
        "Schraegstrich zwischen Zahl und Einheit",
    ),
    (
        "LEGO 10326 Notre Dame 1500 zu verkaufen",
        None,
        "Verkaufsfloskel hinter der Zahl macht sie nicht zur Setnummer",
    ),
    (
        "LEGO 10326 Notre Dame 1500 abzugeben",
        None,
        "Verkaufsfloskel hinter der Zahl, zweite Form",
    ),
    (
        "LEGO 10326 Notre Dame, 40233 Duesseldorf",
        "40233 Duesseldorf",
        "Die Zahl ist die PLZ des Abholorts — nachgesehen, nicht geraten",
    ),
    (
        "LEGO 10326 Notre Dame in 10999 Berlin",
        "10999 Berlin",
        "PLZ ohne ankuendigendes Stichwort, Abholort belegt sie",
    ),
    (
        "LEGO 10326 Notre Dame (81667 Muenchen)",
        "81667 München",
        "Vergleich laeuft ueber die Ziffern, nicht ueber die Schreibweise des Orts",
    ),
]

# (Titel, Abholort, Begruendung) — Konvolute. Ein Preis gehoert dem billigsten
# der genannten Sets; gegen das teuerste gerechnet ergibt er ein Schnaeppchen,
# das nie angeboten wurde.
BUNDLES = [
    (
        "LEGO 10326 VB 10350",
        None,
        "'VB' davor darf die zweite Setnummer nicht verstecken",
    ),
    (
        "LEGO 10326 Festpreis 10350",
        None,
        "'Festpreis' davor darf die zweite Setnummer nicht verstecken",
    ),
    (
        "LEGO 10326, 10350 Eiffelturm",
        "40233 Duesseldorf",
        "Setnummer plus grossgeschriebener Setname sieht aus wie PLZ plus Ort",
    ),
    (
        "LEGO 10326 10350 VB",
        None,
        "'VB' dahinter darf die zweite Setnummer nicht zum Preis machen",
    ),
    (
        "LEGO 10326 10350 Festpreis",
        None,
        "'Festpreis' dahinter darf die zweite Setnummer nicht zum Preis machen",
    ),
    (
        "LEGO 10326 10350 zu verkaufen",
        None,
        "Verkaufsfloskel dahinter darf die zweite Setnummer nicht verstecken",
    ),
    (
        "LEGO 10326 10350 Notre Dame",
        "40233 Duesseldorf",
        "Abholort vorhanden, passt aber nicht zur Zahl — also eine Setnummer",
    ),
]

# (Titel, Abholort, Begruendung) — bekannte Grenzen. Diese Faelle stehen mit
# ihrem heutigen Verhalten hier, ausdruecklich als "so entschieden", nicht als
# "so richtig". Wer sie aufloest, soll wissen, dass er eine Entscheidung
# aendert und nicht einen Fehler behebt.
KNOWN_LIMITS = [
    (
        "LEGO 10326 Notre Dame, 40233 Duesseldorf",
        None,
        "Ohne Abholort bleibt die PLZ eine Setnummer: geraten wird nicht",
    ),
    (
        "LEGO 10326 Notre Dame 1500",
        None,
        "Nackte vierstellige Zahl ohne Einheit ist nicht entscheidbar — "
        "vierstellige Setnummern gibt es",
    ),
]

# (Zustandslabel, Beschreibung, erwarteter Kartonschaden, Begruendung)
# Ein Kartonschaden kostet 10 % vom erwarteten Verkaufspreis. Beide
# Fehlrichtungen kosten also Geld: ein verschluckter Schaden zahlt zu viel,
# ein erfundener laesst ein gutes Angebot liegen.
BOX_DAMAGE = [
    (
        None,
        "Karton nicht mehr perfekt: Dellen an den Ecken",
        True,
        "Die Verneinung gilt 'perfekt', nicht den Dellen dahinter",
    ),
    (
        None,
        "Karton ohne groessere sichtbare Dellen",
        False,
        "Zwischen 'ohne' und 'Dellen' duerfen Adjektive stehen",
    ),
    (
        None,
        "Karton ohne Dellen",
        False,
        "Die knappe Zusage muss weiter tragen",
    ),
    (
        None,
        "Verpackung ist nicht mehr original, hat Dellen",
        True,
        "Das 'nicht' verneint das Original, nicht die Dellen im naechsten Satzteil",
    ),
    (
        None,
        "Karton ohne Dellen, aber ein Riss im Deckel",
        True,
        "Die Zusage deckt die Dellen ab, neben denen sie steht — nicht den Riss",
    ),
    (
        None,
        "Karton in gutem Zustand, die Figur hat einen Riss",
        False,
        "Der Riss gehoert der Figur, die direkt davor steht",
    ),
    (
        None,
        "Anleitung hat einen Riss, Karton ist top",
        False,
        "Der Riss gehoert der Anleitung, auch wenn der Karton naeher steht",
    ),
    (
        None,
        "Die Steine haben Dellen, der Karton ist top",
        False,
        "Die Dellen gehoeren den Steinen",
    ),
    (
        None,
        "Dellen im Karton",
        True,
        "Steht kein Objekt davor, entscheidet das dahinter",
    ),
    (
        None,
        "Karton mit Dellen",
        True,
        "Der Normalfall muss weiter tragen",
    ),
    (None, "Karton hat Kratzer", True, "Kratzer am Karton sind Kartonschaden"),
    (None, "Karton mit Kratzern", True, "Gebeugte Form derselben Aussage"),
    (None, "Karton verkratzt", True, "Adjektivform derselben Aussage"),
    (None, "Karton mit Gebrauchsspuren", True, "Die haeufigste deutsche Umschreibung"),
    (None, "Karton hat einen Wasserfleck", True, "Wasserschaden in seiner kleinen Form"),
    (
        None,
        "Die Steine haben Kratzer, Karton ist top",
        False,
        "Kratzer an den Steinen duerfen den Karton nicht belasten — der Grund, "
        "aus dem 'kratzer' vorher gar nicht im Vokabular stand",
    ),
    (
        None,
        "Modell mit Gebrauchsspuren, Karton neuwertig",
        False,
        "Gebrauchsspuren am Modell gehoeren nicht dem Karton",
    ),
    (None, "Karton ohne Kratzer", False, "Die Zusage gilt auch fuer die neuen Woerter"),
    (None, "Karton unverkratzt", False, "Kein Treffer mitten im Wort"),
]
