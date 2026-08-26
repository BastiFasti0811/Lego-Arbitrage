# Deutsche Freitext-Erkennung härten: Angebots-Identität und Zustand

## Problem Statement

Der Scout-Pfad wirft gültige Einzelset-Angebote still weg und bewertet andere falsch — beides, weil zwei Domain-Klassifizierer deutschen Anzeigen-Freitext lesen, der reicher ist als die Muster, die sie kennen.

Das kostet in zwei Richtungen:

**Verlorene Angebote.** `mentions_other_set_numbers()` hält eine Preisangabe im Titel für eine zweite Setnummer. Das Angebot gilt dann als Konvolut, fällt aus `is_set_offer()` und wird in `_upsert_offers` gar nicht erst gespeichert. Es erscheint nie im Live-Feed, und niemand sieht, dass es je da war. Verifiziert am Arbeitsstand vom 23.08.2026, 15:32 (alle mit Setnummer 10326):

| Titel | verworfen | korrekt |
|---|---|---|
| `LEGO 10326 Notre Dame 1500 oder VB` | ja | nein |
| `LEGO 10326 Notre Dame 1500 - VB` | ja | nein |
| `LEGO 10326 Notre Dame 1500 / VB` | ja | nein |
| `LEGO 10326 Notre Dame nur 1500` | ja | nein |
| `LEGO 10326 Notre Dame 1500 zu verkaufen` | ja | nein |
| `LEGO 10326 Notre Dame 1500 abzugeben` | ja | nein |
| `LEGO 10326 Notre Dame, 40233 Duesseldorf` | ja | nein |
| `LEGO 10326 Notre Dame in 10999 Berlin` | ja | nein |
| `LEGO 10326 Notre Dame (81667 Muenchen)` | ja | nein |
| `LEGO 10326 für 1500` | ja (als Zubehör) | nein |

Der Ausfall trifft ausgerechnet das vierstellige Preissegment, in dem Arbitrage überhaupt lohnt, und die Postleitzahl-Fälle treffen jedes Angebot mit Abholort im Titel — auf Kleinanzeigen die Normalform.

**Falsch bewertete Angebote.** In der Gegenrichtung maskieren Preis-Stichwörter echte zweite Setnummern: `LEGO 10326 VB 10350` und `LEGO 10326 Festpreis 10350` passieren als Einzelset, obwohl der eine Preis dem billigeren der beiden Sets gehört. Der Zubehör-Filter übersieht Anzeigen, die den Umlaut umschreiben (`Beleuchtung fuer 10326`, `Stickerbogen passend fuer Lego 10326`) — genau die Klasse, die als 9,99-EUR-Wandhalterung gegen 400 EUR Setpreis schon einmal 869 % ROI gemeldet hat.

**Kartonschaden ist nicht zuzuordnen.** `classify_listing_condition()` liefert `box_damage` und damit den 0.9-Faktor auf `expected_sale_price`. Der Detektor verlangt nur, dass irgendwo im selben Satz ein Karton-Wort steht. Weil das zu schwach ist, mussten `kratzer` und `gebrauchsspuren` bewusst aus dem Schadensvokabular herausgelassen werden — sie hätten den Karton für Kratzer an den Steinen belastet. Die schwache Zuordnung blockiert also die Vokabular-Erweiterung. Dazu zwei verifizierte Fehler in der Verneinungslogik:

| Beschreibung | ist | korrekt |
|---|---|---|
| `Karton nicht mehr perfekt: Dellen an den Ecken` | kein Schaden | Schaden |
| `Karton ohne groessere sichtbare Dellen` | Schaden | kein Schaden |

Das Verneinungsfenster ist ein fester 15-Zeichen-Blick zurück. Es reicht nicht über `ohne groessere sichtbare` hinweg (falscher Schaden) und deckt umgekehrt `nicht mehr perfekt:` ab, wo die Verneinung einer anderen Eigenschaft gilt (verschluckter Schaden).

Hinter allen drei Punkten steht dasselbe strukturelle Problem: das ist die dritte Runde Regex-Nachbesserung an denselben zwei Funktionen. Jede Runde hat echte Bugs behoben und neue Lücken offengelassen, weil niemand sehen kann, wie groß die Abdeckung ist. Ohne ein Korpus, das die Trefferquote sichtbar macht, folgt eine vierte Runde.

## Solution

Zwei Klassifizierer werden gehärtet, und die Härtung wird messbar gemacht.

**Preisangaben zentral, nicht pro Fundstelle.** Die Preis-Notation (`_PRICE_UNIT`/`_PRICE_TAIL`) ist schon zentralisiert und wird um die fehlenden Formen erweitert: Trennzeichen zwischen Zahl und Einheit (`oder`, `-`, `/`), ankündigende Wörter vor der Zahl (`nur`, `ab`, `für nur`) und abschließende Verkaufsfloskeln nach der Zahl (`zu verkaufen`, `abzugeben`, `zu haben`). Damit verschwindet die Klasse „Preis als Setnummer gelesen" nicht Muster für Muster, sondern an einer Stelle.

**Postleitzahlen über den Abholort, nicht über ein Stichwort.** Angebote tragen bereits `seller_location`. Eine fünfstellige Zahl im Titel, die zur PLZ des Abholorts passt, ist die PLZ — unabhängig davon, ob ein Stichwort davor steht. Fehlt der Abholort, bleibt es bei der heutigen Stichwort-Regel. Es wird an keiner Stelle geraten: entweder es gibt eine Tatsache zum Abgleichen, oder das Verhalten bleibt, wie es ist.

**Preis-Stichwörter maskieren keine Setnummern mehr.** Ein Stichwort wie `VB` oder `Festpreis` vor der Zahl unterdrückt sie nur noch, wenn die Zahl als Preis plausibel ist. Eine fünf- oder sechsstellige Zahl nach `VB` ist eine Setnummer, keine 10.000-EUR-Preisvorstellung.

**Umlaut-Transkription geteilt.** Der Zustands-Klassifizierer hat die Transkriptions-Helfer (`ä|ae|a` und Geschwister) schon; das Identity-Modul kennt nur `[üu]` und übersieht damit `fuer`. Beide lesen künftig aus derselben Quelle.

**Schaden wird dem Karton zugeordnet, bevor das Vokabular wächst.** Das Schadenswort muss zum Karton gehören, nicht bloß im selben Satz stehen. Erst wenn die Zuordnung trägt, kommen `kratzer`, `gebrauchsspuren`, `verkratzt` und `wasserfleck` hinzu — sonst würde jeder Kratzer am Modell den Karton belasten. Das Verneinungsfenster wird von „15 Zeichen" auf „gleiche Teilaussage" umgestellt.

**Ein Korpus statt Einzelfälle.** Eine Fixture-Sammlung echter und realistischer deutscher Anzeigentexte mit erwartetem Urteil treibt parametrisierte Tests durch die beiden bestehenden Seams. Die Trefferquote wird damit eine Zahl, die man vor und nach einer Änderung ablesen kann, und eine neue Formulierung aufzunehmen kostet eine Zeile im Korpus statt eines neuen Tests.

## User Stories

1. Als Arbitrage-Betreiber möchte ich, dass ein Angebot mit `1500,- VB` im Titel im Live-Feed erscheint, damit mir keine Deals in genau dem Preissegment entgehen, in dem sich Arbitrage rechnet.
2. Als Arbitrage-Betreiber möchte ich, dass `1500 oder VB`, `1500 - VB` und `1500 / VB` als eine Preisangabe gelesen werden, damit ein Trennzeichen zwischen Zahl und Einheit kein Angebot kostet.
3. Als Arbitrage-Betreiber möchte ich, dass `nur 1500` und `ab 1500` als Preis gelesen werden, damit ankündigende Wörter vor der Zahl nichts verwerfen.
4. Als Arbitrage-Betreiber möchte ich, dass `1500 zu verkaufen` und `1500 abzugeben` als Preis gelesen werden, damit Verkaufsfloskeln hinter der Zahl nichts verwerfen.
5. Als Arbitrage-Betreiber möchte ich, dass `LEGO 10326 Notre Dame, 40233 Duesseldorf` gespeichert wird, damit ein Abholort im Titel — auf Kleinanzeigen die Normalform — nicht als zweites Set gilt.
6. Als Arbitrage-Betreiber möchte ich, dass die PLZ auch ohne ankündigendes Stichwort erkannt wird (`in 10999 Berlin`, `(81667 Muenchen)`), sobald der Abholort des Angebots bekannt ist, damit ich mich nicht auf die Wortwahl des Verkäufers verlassen muss.
7. Als Arbitrage-Betreiber möchte ich, dass die PLZ des Abholorts aus den Angebotsdaten zur Erkennung genutzt wird, damit die Entscheidung auf einer Tatsache beruht und nicht auf einer Formulierung.
8. Als Arbitrage-Betreiber möchte ich, dass `LEGO 10326 für 1500` nicht als Zubehör verworfen wird, damit die Preisform „für X" mich nicht denselben Deal zweimal kostet.
9. Als Arbitrage-Betreiber möchte ich, dass ein echtes Konvolut wie `LEGO 10326 VB 10350` weiterhin abgelehnt wird, damit ich keinen Preis gegen das teurere von zwei Sets rechne.
10. Als Arbitrage-Betreiber möchte ich, dass `LEGO 10326 Festpreis 10350` als Konvolut erkannt wird, damit ein Preis-Stichwort keine echte zweite Setnummer verstecken kann.
11. Als Arbitrage-Betreiber möchte ich, dass Zubehör mit umschriebenem Umlaut (`Beleuchtung fuer 10326`) abgelehnt wird, damit die Schreibweise des Verkäufers den Filter nicht aushebelt.
12. Als Arbitrage-Betreiber möchte ich, dass `Stickerbogen passend fuer Lego 10326` abgelehnt wird, damit ein Aufkleberbogen nicht gegen den Setpreis gerechnet wird.
13. Als Arbitrage-Betreiber möchte ich, dass Baujahre (`Baujahr 2023`) und Teilezahlen (`8000 Teile`) weiterhin keine Setnummern sind, damit die Erweiterung keine bestehende Erkennung zerstört.
14. Als Arbitrage-Betreiber möchte ich, dass ein Karton mit Kratzern als beschädigt gilt, damit `expected_sale_price` den Zustand abbildet, den die Anzeige beschreibt.
15. Als Arbitrage-Betreiber möchte ich, dass `Gebrauchsspuren am Karton` als Kartonschaden zählt, damit die häufigste deutsche Umschreibung nicht durchfällt.
16. Als Arbitrage-Betreiber möchte ich, dass Kratzer am Modell den Karton **nicht** belasten, damit ich nicht 10 % Wert für einen Schaden abziehe, den der Karton nicht hat.
17. Als Arbitrage-Betreiber möchte ich, dass `Karton nicht mehr perfekt: Dellen an den Ecken` als Schaden erkannt wird, damit eine Verneinung über eine andere Eigenschaft den Schaden nicht verschluckt.
18. Als Arbitrage-Betreiber möchte ich, dass `Karton ohne groessere sichtbare Dellen` als schadenfrei gilt, damit eine Zusage mit Adjektiven dazwischen nicht in ihr Gegenteil kippt.
19. Als Arbitrage-Betreiber möchte ich, dass `Karton ohne Dellen` und `Karton neuwertig` weiterhin schadenfrei bleiben, damit die Erweiterung keine falschen Abzüge einführt.
20. Als Arbitrage-Betreiber möchte ich, dass `Karton ohne Verpackungsschaeden` weiterhin nicht als „ohne Verpackung" gelesen wird, damit eine Zusage nicht zur Unvollständigkeit wird.
21. Als Arbitrage-Betreiber möchte ich im Live-Feed erkennen können, warum ein Angebot einen Wertabzug hat, damit ich die Zahl auf der Karte einschätzen kann statt ihr zu glauben.
22. Als Arbitrage-Betreiber möchte ich, dass ein verworfenes Angebot eine Spur hinterlässt, damit ich sehen kann, was der Filter mir wegnimmt, statt es nur zu vermuten.
23. Als Betreiber des Scrapers möchte ich die Trefferquote der beiden Klassifizierer als Zahl sehen, damit ich weiß, ob eine Änderung sie verbessert oder nur verschiebt.
24. Als Entwickler möchte ich eine neue Anzeigen-Formulierung mit einer Zeile im Korpus abdecken, damit das Erweitern billiger ist als das Umgehen.
25. Als Entwickler möchte ich die Fälle aus der Produktions-DB im Korpus wiederfinden, damit die Tests von echten Anzeigen handeln und nicht von erfundenen.
26. Als Entwickler möchte ich, dass alle Prüfungen über `is_set_offer()` und `classify_listing_condition()` laufen, damit ich die Regexe darunter austauschen kann, ohne Tests anzufassen.
27. Als Entwickler möchte ich, dass die Umlaut-Transkription an einer Stelle definiert ist, damit `fuer`/`für` nicht in einem Modul funktioniert und im anderen nicht.
28. Als Entwickler möchte ich, dass jede bewusste Auslassung im Vokabular als solche im Code steht, damit die nächste Runde sie nicht als Bug „behebt".
29. Als Arbitrage-Betreiber möchte ich, dass ein Angebot ohne `seller_location` weiterhin bewertet wird, damit fehlende Metadaten kein Angebot kosten.
30. Als Arbitrage-Betreiber möchte ich, dass die 253 bestehenden Tests grün bleiben, damit die Härtung nichts einreißt, was schon funktioniert.
31. Als Arbitrage-Betreiber möchte ich, dass `LEGO 10326, 10350 Eiffelturm` als Konvolut abgelehnt wird, auch wenn der zweiten Setnummer ein großgeschriebener Setname folgt, damit die PLZ-Erkennung kein Schlupfloch für Konvolute öffnet.
32. Als Entwickler möchte ich, dass das Format des Abholorts an echten Scrape-Daten geprüft ist, bevor die PLZ-Erkennung darauf aufbaut, damit die Entscheidung nicht auf einer Annahme über ein Feld steht.

## Implementation Decisions

**Betroffene Module.** Das Identity-Modul (Angebots-Identität: `is_set_offer`, `mentions_other_set_numbers`, `looks_like_accessory`) und der Zustands-Klassifizierer (`classify_listing_condition`, intern `_has_box_damage`). Der Aufrufer-Pfad (Scout-Route, Scrape-Task, Kleinanzeigen-Scraper) wird nur dort berührt, wo `seller_location` durchgereicht werden muss.

**Signatur-Erweiterung an einem Seam.** `is_set_offer()` bekommt einen optionalen Parameter für den Abholort des Angebots. Optional, weil `seller_location` nullable ist und ein Angebot ohne Ort weiterhin bewertet werden muss. `mentions_other_set_numbers()` bekommt denselben Parameter, damit die PLZ-Entscheidung dort fällt, wo die Zahlen gelesen werden. Beide Aufrufer haben das Angebotsobjekt bereits zur Hand.

**PLZ-Erkennung über den Abgleich, nicht über eine Faustregel.** Aus `seller_location` wird die fünfstellige PLZ gelesen; stimmt eine Zahl im Titel damit überein, ist sie die PLZ und keine Setnummer. Fehlt `seller_location` oder enthält es keine PLZ, gilt unverändert die heutige Stichwort-Regel — die betroffenen Angebote werden also nicht schlechter behandelt als bisher, aber auch nicht besser.

Ausdrücklich verworfen wurde die Alternative, aus der Textform zu raten („fünf Ziffern gefolgt von einem großgeschriebenen Wort sind eine PLZ"). Sie hätte auch ohne Abholort geholfen, kippt aber genau im gefährlichen Fall: `LEGO 10326, 10350 Eiffelturm` ist ein Konvolut, dessen zweite Setnummer von ihrem großgeschriebenen Setnamen gefolgt wird — die Faustregel hätte das als PLZ gelesen und den Konvolut-Preis gegen das teurere Set gerechnet. Ein erfundenes Schnäppchen ist teurer als ein Angebot, das weiterhin durchfällt.

**Preis-Stichwörter unterdrücken nur plausible Preise.** Steht ein Preis-Stichwort (`VB`, `Festpreis`, `Preis`, `kostet`) vor der Zahl, wird die Zahl nur dann als Preis verworfen, wenn sie höchstens vierstellig ist. Fünf- und sechsstellige Zahlen bleiben Setnummern. Das ist eine Abwägung: ein echtes Angebot über 10.000 EUR für ein Einzelset wird dadurch als Konvolut abgelehnt. Bewusst in Kauf genommen, weil solche Preise auf Kleinanzeigen praktisch immer Konvolute sind und die Gegenrichtung — ein Konvolut als Einzelset zu bewerten — einen erfundenen Schnäppchenpreis meldet.

**Nackte Zahl am Titelende bleibt ambig.** `LEGO 10326 Notre Dame 1500` wird heute als zweites Set gelesen und damit verworfen. Eine nackte vierstellige Zahl ohne Einheit ist ohne Kontext nicht entscheidbar — vierstellige Setnummern existieren. Entscheidung: unverändert lassen und im Korpus als bekannt-ambiger Fall markieren, damit die nächste Runde ihn nicht versehentlich in eine Richtung auflöst. Wenn er sich am ersten Lauf mit gefüllter Watchlist als häufig erweist, wird er separat entschieden.

**Umlaut-Transkription wird geteilt.** Die Helfer für `ä|ae|a`, `ö|oe|o`, `ü|ue|u` leben heute im Zustands-Klassifizierer. Sie wandern an eine gemeinsame Stelle, aus der beide Domain-Module lesen. Kein pauschales Text-Fold — `neue`, `teuer` und `Steuer` enthalten alle ein `ue`, und dieser Grund steht bereits im Code.

**Kartonschaden: Zuordnung vor Vokabular.** Zwei Schritte in dieser Reihenfolge, weil der zweite ohne den ersten schadet:

1. Das Schadenswort muss zum Karton gehören. Ein Karton-Wort irgendwo im Satz reicht nicht — geprüft wird die Nähe zwischen Karton-Wort und Schadenswort innerhalb derselben Teilaussage.
2. Erst danach kommen `kratzer`, `verkratzt`, `gebrauchsspuren`, `wasserfleck` ins Vokabular. Der bestehende Kommentar, der ihre Auslassung begründet, wird durch einen ersetzt, der die neue Zuordnung nennt — nicht gelöscht.

**Verneinung: Teilaussage statt Zeichenfenster.** Das feste 15-Zeichen-Fenster wird durch eine Grenze auf Teilaussagen-Ebene ersetzt. Eine Verneinung wirkt, wenn sie in derselben Teilaussage wie das Schadenswort steht und kein Grenzzeichen dazwischen liegt; `:` gilt dabei als Grenze, weil `Karton nicht mehr perfekt: Dellen` zwei Aussagen sind. Beliebig viele Adjektive zwischen Verneinung und Schadenswort sind erlaubt, damit `ohne groessere sichtbare Dellen` trägt.

**Satztrennung bleibt wie sie ist.** Der Punkt trennt nur mit folgendem Großbuchstaben, sonst zerfällt `Karton hat ca. 3 Dellen`. Diese Entscheidung ist frisch getroffen und wird nicht angetastet.

**Kein neues Datenmodell.** `box_damage` und `condition` bleiben, wie sie sind. Der UNKNOWN-Faktor (0.7) bleibt unverändert — die Kalibrierung ist bis zum ersten Lauf mit gefüllter Watchlist aufgeschoben und im Code begründet.

## Testing Decisions

**Was einen guten Test hier ausmacht.** Er prüft, welches Urteil ein Anzeigentext bekommt, nicht wie das Urteil zustande kommt. Ein Test, der ein Regex-Muster oder eine private Funktion nennt, friert die Implementierung ein — und genau die soll austauschbar bleiben, weil es die dritte Runde Nachbesserung daran ist. Jeder Testfall ist ein Anzeigentext plus erwartetes Urteil.

**Seams.** Genau zwei, beide bestehen schon, beide sind der höchste erreichbare Punkt:

- `is_set_offer(titel, setnummer, preis, referenzpreis, setname, abholort)` → bool. Deckt Preis-Notation, PLZ, Konvolut-Erkennung und Zubehör-Filter in einem Urteil ab.
- `classify_listing_condition(zustandslabel, beschreibung)` → (Zustand, Kartonschaden). Der Kartonschaden wird über das zweite Element geprüft, nicht über die interne Schadensfunktion.

Kein neuer Seam. `_has_box_damage`, `_matches_unnegated`, `_PRICE_TAIL` und die Verneinungsmuster bleiben privat und damit ersetzbar. `mentions_other_set_numbers` und `looks_like_accessory` werden in den bestehenden Tests weiterhin direkt aufgerufen, wo eine Fehlermeldung dadurch die Ursache benennt statt nur „False statt True" — aber jeder Fall, der über `is_set_offer` ausdrückbar ist, gehört dorthin.

**Korpus.** Eine Fixture-Sammlung von Anzeigentexten mit erwartetem Urteil, getrennt nach den beiden Seams, treibt parametrisierte Tests. Der Titel jedes Falls ist der Anzeigentext selbst, damit ein Fehlschlag im Testlauf sofort lesbar ist. Fälle aus der Produktions-DB werden als solche markiert und mit dem Abrufdatum versehen — die bestehenden Tests machen das schon so, das Muster wird übernommen. Bekannt-ambige Fälle stehen mit ihrem heutigen Verhalten im Korpus, ausdrücklich als „so entschieden", nicht als „so korrekt".

**Vorbild im Repo.** Die parametrisierten Zubehör- und Echt-Set-Listen in den bestehenden Identity-Tests sind die Vorlage für die Korpus-Struktur; die Kommentare dort, die den ROI-Schaden eines Fehlurteils benennen (`869 %`, `9,99 EUR gegen 400 EUR`), sind die Vorlage für die Begründungen. Die Zustands-Tests mit ihrer Trennung in Label-, Beschreibungs- und Schadensklassen sind die Vorlage für den Zustands-Teil des Korpus. Die Zustandswert-Tests zeigen, wie der Wertfaktor gegen die Entscheidungs-Engine geprüft wird — dieser Pfad bleibt unverändert und muss grün bleiben.

**Abholort im Korpus.** Die PLZ-Fälle brauchen den Abholort als Teil des Falls, nicht nur den Titel — sonst prüfen sie die Regel, die gerade ersetzt wird. Jeder Korpus-Fall trägt den Abholort mit, und mindestens drei Varianten gehören dazu: Abholort passt zur Zahl im Titel (PLZ), Abholort vorhanden aber abweichend (dann ist die Zahl keine PLZ), Abholort fehlt (heutiges Verhalten). In allen bestehenden Tests steht `seller_location=None` — es gibt für dieses Feld also noch keinerlei Abdeckung.

**Regression.** Die 253 bestehenden Tests bleiben grün. Der Korpus kommt obendrauf, er ersetzt keinen bestehenden Test.

**Was nicht getestet wird.** Kein Test gegen die echten Marktplätze. Kein Test, der eine Trefferquote als Schwelle festschreibt — die Zahl ist zum Ablesen da, nicht als Gate, weil ein Gate den Anreiz erzeugt, das Korpus zu beschneiden.

## Out of Scope

- **Kalibrierung des UNKNOWN-Faktors (0.7).** Bewusst offen bis zum ersten Lauf mit gefüllter Watchlist; die Begründung steht im Code. Angebote und Preis-Datensätze sind seit dem 25.03.2026 leer, eine Messung ist nicht möglich.
- **Die nackte Zahl am Titelende.** Bleibt beim heutigen Verhalten, siehe Implementierungsentscheidung.
- **Bild- oder OCR-Erkennung des Zustands.** Steht als späteres Produkt-Feature in der Wissensdatenbank, hat mit dieser Texthärtung nichts zu tun.
- **Der Zubehör-Preisboden und die Soft-Marker-Schwelle** (8 % und 35 %). Unverändert; hier geht es um Worterkennung, nicht um Schwellen.
- **Andere Plattformen als Kleinanzeigen.** Die Preis- und Ortsformen sind auf deutsche Kleinanzeigen-Titel zugeschnitten.
- **Das Raten der PLZ aus der Textform.** Verworfen, Begründung bei den Implementierungsentscheidungen. Angebote ohne brauchbaren Abholort behalten das heutige Verhalten.
- **Ein ML- oder LLM-Klassifizierer** statt der Muster. Wäre eine eigene Entscheidung mit eigenen Kosten; hier wird das bestehende Verfahren tragfähig gemacht.
- **Nachträgliches Neubewerten bereits verworfener Angebote.** Verworfene Angebote wurden nie gespeichert, es gibt nichts nachzuholen.
- **Die Rate-Limit- und Enrichment-Themen** aus derselben Arbeitsphase (Detailseiten-Cap, `scrape.details_capped`). Eigenes Thema.

## Further Notes

**Der Arbeitsstand bewegt sich.** Diese Spec ist gegen den unversionierten Arbeitsstand vom 23.08.2026, 15:32 verifiziert, und beide Domain-Dateien wurden in derselben Minute noch bearbeitet. Ein Teil der ursprünglich gemeldeten Fehler war zu diesem Zeitpunkt schon behoben — Komma-Strich-Preise, `verhandelbar`, `fixpreis`, die Verneinung nach Standard-Disclaimern (`keine Garantie`, `keine Rücknahme`) und die Zuordnung bei `ca.`-Abkürzungen laufen inzwischen korrekt. Wer das umsetzt, sollte die Fehlerlisten oben zuerst nachprüfen, statt sie zu glauben.

**Zwei Meldungen aus der Vorgeschichte waren falsch.** `Fixpreis 1500` funktionierte bereits (weil `preis` als Teilwort greift), und der `für`-Lookahead griff nie bei der Schreibweise `fuer` — der gemeldete Fehler existierte nur mit echtem Umlaut. Beide Meldungen stammen aus automatisierten Proben, die nicht gegengeprüft wurden. Das ist der Grund für die Korpus-Entscheidung: eine Behauptung über die Trefferquote muss man nachlesen können.

**Der Abholort ist eine Voraussetzung, keine Gewissheit.** Dass `seller_location` tatsächlich eine fünfstellige PLZ enthält, ist im Repo nicht belegt: Der Wert kommt aus einem CSS-Element der Kleinanzeigen-Trefferliste, es gibt keine Fixture dafür, und in allen bestehenden Tests steht `None`. Kleinanzeigen liefert dort üblicherweise `40233 Düsseldorf`, aber das ist eine Erwartung, keine Messung. Erster Schritt der Umsetzung: an echten Scrape-Daten nachsehen, welches Format wirklich ankommt. Steht dort kein verwertbarer Ort, trägt die PLZ-Erkennung nicht und die Entscheidung muss neu getroffen werden — der Rest der Spec bleibt davon unberührt.

**Die bewusste Auslassung ist ein Muster, kein Versehen.** `kratzer` und `gebrauchsspuren` fehlen im Schadensvokabular mit Begründung im Code. Wer die Zuordnung nicht zuerst stärkt und die Wörter trotzdem aufnimmt, macht die Sache messbar schlechter statt besser: jeder Kratzer am Modell zieht dann 10 % vom erwarteten Verkaufspreis ab.

**Warum beides in einer Spec steht.** Identitäts- und Zustandserkennung sind verschiedene Funktionen mit derselben Fehlerursache: deutscher Freitext, der reicher ist als das Muster, und Verneinungen, die auf beiden Seiten des Verbs stehen können. Sie teilen die Transkriptions-Helfer, die Verneinungslogik und das Korpus-Verfahren. Getrennt zu spezifizieren hieße, denselben Kontext zweimal zu schreiben.

**Die eigentliche Währung.** Ein falsch verworfenes Angebot ist unsichtbar — es steht in keinem Log und in keiner Tabelle. Ein falsch bewertetes Angebot meldet sich als Deal, der es nicht ist. Von beidem ist das erste teurer, weil man es nicht bemerkt.
