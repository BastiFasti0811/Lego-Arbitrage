"""What a set in a given condition is actually worth on resale.

The market price the pipeline collects is the price for complete, undamaged
goods — that is what eBay-sold and the price aggregators report. Paying 10 EUR
for a kingfisher whose listing says "ohne Anleitung und Karton" and booking it
against 37,89 EUR produced a +57 % ROI for a set that will not fetch anywhere
near that.

The condition already reached the risk score (2 points out of 10), which says
"this could go wrong" but leaves the expected return untouched. Risk and value
are different questions: a used set is not a risky bet on 37,89 EUR, it is a
safe bet on less.
"""

from __future__ import annotations

import re

import structlog

from app.domain.german import AE, OE, UE

logger = structlog.get_logger()

# Zustandsstrings, die anderswo im System entstehen und dasselbe meinen.
# Der risk_scorer kannte diese Schreibweisen laengst (NEU/MISB ohne Risiko,
# OVP/UNGEOEFFNET mit einem Punkt) — die Wertseite fiel dafuer still auf
# UNKNOWN zurueck. Beide lesen jetzt aus derselben Tabelle.
_CONDITION_ALIASES = {
    "NEU": "NEW_SEALED",
    "MISB": "NEW_SEALED",
    "NEW": "NEW_SEALED",
    "SEALED": "NEW_SEALED",
    "OVP": "NEW_OPEN_BOX",
    "UNGEÖFFNET": "NEW_OPEN_BOX",
    "UNGEOEFFNET": "NEW_OPEN_BOX",
    "NEW_OPEN": "NEW_OPEN_BOX",
    "USED": "USED_COMPLETE",
    "GEBRAUCHT": "USED_COMPLETE",
}

# Share of the market price a set in this condition can be expected to fetch.
# Deliberately coarse — these are resale rules of thumb, not measurements, and
# pretending otherwise with two decimals would be false precision.
_VALUE_BY_CONDITION = {
    "NEW_SEALED": 1.0,
    "NEW_OPEN_BOX": 0.9,
    "USED_COMPLETE": 0.7,
    "USED_INCOMPLETE": 0.5,
    # Private sellers who have a sealed set say so. Silence more often means
    # used, and assuming new is the more expensive mistake.
    #
    # Bewusst offen gelassen (2026-08-23): UNKNOWN kostet hier 30 % Wert UND im
    # risk_scorer 2 von 10 Risikopunkten, und opportunity_score multipliziert
    # beides. Seit die Detailseite nur noch fuer die billigsten
    # scraper_detail_max_per_set Angebote gelesen wird, ist UNKNOWN der
    # Normalfall — ein Angebot muss dann rund 35-40 % unter Markt liegen, um
    # den Feed-Filter (min_roi=15) zu passieren. Ob das zu streng ist, laesst
    # sich nicht schaetzen: offers/price_records sind seit 2026-03-25 leer.
    # Erst am ersten Lauf mit gefuellter Watchlist messen (scrape.details_capped
    # sagt, wie oft der Cap ueberhaupt greift), dann entscheiden.
    "UNKNOWN": 0.7,
}

# A damaged box costs on top of whatever the condition already implies.
_BOX_DAMAGE_FACTOR = 0.9


def normalize_condition(condition: str | None) -> str:
    """Map any condition spelling in the system onto the canonical set.

    An unknown string is not silently worth 70 % — it is logged, because a
    spelling nobody mapped is a bug that otherwise only shows up as a slightly
    wrong ROI months later.
    """
    key = (condition or "").strip().upper()
    if not key:
        return "UNKNOWN"
    if key in _VALUE_BY_CONDITION:
        return key
    if key in _CONDITION_ALIASES:
        return _CONDITION_ALIASES[key]
    logger.warning("condition.unknown_value", condition=condition)
    return "UNKNOWN"


def condition_value_factor(condition: str | None, box_damage: bool = False) -> float:
    """Multiplier from market price to expected sale price."""
    factor = _VALUE_BY_CONDITION[normalize_condition(condition)]
    if box_damage:
        factor *= _BOX_DAMAGE_FACTOR
    return round(factor, 4)


# ── Zustand aus der Anzeige lesen ────────────────────────────────────

# Die Auswahlwerte, die kleinanzeigen.de im Feld "Zustand" anbietet.
_LABEL_TO_CONDITION = {
    "NEU": "NEW_OPEN_BOX",
    "SEHR GUT": "USED_COMPLETE",
    "GUT": "USED_COMPLETE",
    "IN ORDNUNG": "USED_INCOMPLETE",
    "DEFEKT": "USED_INCOMPLETE",
}

# Die Schreibweisen liegen jetzt in app.domain.german: der Identity-Filter
# braucht dieselben, und getrennt gepflegt kannte er "fuer" nicht.
_AE = AE
_OE = OE
_UE = UE

# ── Verneinungen ─────────────────────────────────────────────────────
# Drei Richtungen, drei Muster. Ein gemeinsames ginge nicht: "ohne" ist in
# _INCOMPLETE_RE selbst das Signalwort ("ohne Anleitung") und dort gerade keine
# Verneinung.
_NEG_BEFORE_RE = re.compile(r"\b(nicht|nie|kein\w*)\s+(?:mehr\s+)?$", re.IGNORECASE)
# Bewusst [ \t] statt \s: ein Zeilenwechsel trennt Aussagen. Sonst kassiert der
# Gewaehrleistungsausschluss, der auf Kleinanzeigen Pflichttext ist, die Zeile
# davor — "Ohne OVP\nKeine Ruecknahme" ist keine Verneinung von "ohne OVP".
_NEG_AFTER_RE = re.compile(r"^[ \t]*(kein\w*|nichts)\b", re.IGNORECASE)
# Nahe am Schadenswort und nicht ueber eine Satzteilgrenze hinweg: in "nicht
# mehr original, hat Dellen" verneint das "nicht" das Original, nicht die
# Dellen.
#
# Gezaehlt werden Woerter, nicht Zeichen. Ein festes Zeichenfenster kippte in
# beide Richtungen: "ohne groessere sichtbare Dellen" war 24 Zeichen lang und
# damit ausserhalb — die Zusage wurde zum Schaden; "nicht mehr perfekt: Dellen"
# lag drinnen, obwohl der Doppelpunkt eine neue Aussage anfaengt — der Schaden
# verschwand. Dazwischen dürfen bis zu drei Woerter stehen (Adjektive,
# Steigerungen), aber kein Satzzeichen, denn \w+ matcht keines.
_NEG_NEAR_RE = re.compile(r"\b(ohne|kein\w*|nicht)\b(?:\s+\w+){0,3}\s*$", re.IGNORECASE)


def _matches_unnegated(pattern: re.Pattern, text: str, where: str) -> bool:
    """Whether the pattern hits at least once without a negation attached.

    Checked per hit, not per text: "ohne Karton, es fehlen keine Teile" has one
    real hit and one negated one, and the real one has to win.

    `where` says which side a denial may sit on, and that differs per pattern:
    German puts the negation on either side of a verb ("es fehlen keine Teile"
    = "keine Teile fehlen"), but a phrase like "ohne Karton" can only ever be
    denied from in front.
    """
    for match in pattern.finditer(text):
        if where in ("before", "both") and _NEG_BEFORE_RE.search(text[: match.start()]):
            continue
        if where in ("after", "both") and _NEG_AFTER_RE.search(text[match.end() :]):
            continue
        return True
    return False


# ── Unvollstaendig ───────────────────────────────────────────────────
# Fehlt Karton oder Anleitung, ist das Set fuer Sammler kein volles Exemplar
# mehr — unabhaengig davon, was im Zustandsfeld steht.

# Aussagen um das Verb "fehlen": nur die duerfen von hinten verneint werden.
# Der Stamm ist "fehl", nicht "fehle" — "fehlt" fiel vorher durch.
_PARTS = r"(anleitung(?:en)?|karton|ovp|verpackung|teile?|steine?|figuren?)"
# "ohne Verpackungsschaeden" sagt das Gegenteil von "ohne Verpackung". Ein
# pauschales \b ginge nicht — "ohne Anleitungsheft" und "ohne Kartonage" sind
# echte Signale.
# Das Fugen-s gehoert dazu ("VerpackungsSCHAEDEN"), der Bindestrich auch
# ("OVP-Schaeden" ist bei Abkuerzungen die natuerlichere Schreibweise) — und
# die Umschrift ueber _AE statt einer handgeschriebenen Zeichenklasse, sonst
# faellt ausgerechnet "schaeden" durch.
_NOT_A_DEFECT = rf"(?![\s-]?s?(?:sch{_AE}d|mangel|m{_AE}ngel|fehler|material))"
_MISSING_VERB_RE = re.compile(
    rf"{_PARTS}\s+fehl(?:t|en)"
    r"|es\s+fehl(?:t|en)\b"
    # Deutsche Verb-Zweitstellung: "Leider fehlt die Anleitung". Der Artikel
    # bleibt optional, damit "fehlen Teile" ebenfalls greift — "keine" ist
    # keiner davon, die Verneinung bleibt also wirksam.
    # \b, sonst matcht das Muster in "empfehlen".
    rf"|\bfehl(?:t|en)\s+(?:die\s+|der\s+|das\s+|ein\w*\s+)?{_PARTS}",
    re.IGNORECASE,
)

# Feststellungen, die eine nachfolgende Verneinung nicht aufheben kann.
_INCOMPLETE_RE = re.compile(
    # Die Komposita einzeln, nicht \w* — sonst faengt "ohne Werkzeugbox" mit.
    # Der Lookahead haelt die Gegenrichtung heraus: "ohne Verpackungsschaeden"
    # und "ohne Kartonschaden" sind Zusagen, keine fehlenden Teile.
    r"ohne\s+(die\s+|den\s+|das\s+)?(original|bau|um|innen)?"
    rf"(anleitung(?:en)?|karton|ovp|verpackung|box){_NOT_A_DEFECT}"
    # "Keine Anleitung" ist mindestens so haeufig wie "ohne Anleitung". Das
    # "kein" steht im Treffer selbst, _matches_unnegated bleibt also wirksam.
    # (?!\w*\s+fehl) trennt die beiden Lesarten: "keine Anleitung dabei" ist die
    # Aussage, "keine Anleitung fehlt" ihre Verneinung.
    rf"|kein\w*\s+(original)?(anleitung(?:en)?|karton|ovp|verpackung)"
    rf"{_NOT_A_DEFECT}(?!\w*\s+fehl)"
    rf"|unvollst{_AE}ndig"
    # "Originalverpackung nicht vorhanden" ist dieselbe Aussage wie "ohne OVP".
    r"|(anleitung|karton|ovp|originalverpackung|verpackung)\s+(ist\s+)?"
    r"nicht\s+(mehr\s+)?(vorhanden|dabei|enthalten)"
    r"|nicht\s+komplett",
    re.IGNORECASE,
)

# ── Gebraucht / geoeffnet ────────────────────────────────────────────
# Ohne diese beiden faellt ein Titel wie "10331 gebraucht" auf UNKNOWN zurueck,
# und wo ein Aufrufer NEW_SEALED als Default fuehrt, wird daraus Faktor 1.0.
# \b wie bei _OPENED_RE: ohne sie matcht "gebraucht" mitten in "ungebraucht"
# und macht aus versiegelter Ware Faktor 0.7.
_USED_RE = re.compile(r"\bgebraucht|\bbespielt|\bused\b", re.IGNORECASE)
# \b vor "ge": sonst matcht das Muster mitten in "ungeoeffnet".
_OPENED_RE = re.compile(rf"\bge{_OE}ffnet|\baufgebaut|\bzusammengebaut", re.IGNORECASE)

# ── Versiegelt ───────────────────────────────────────────────────────
# Nur eine Aussage ueber den jetzigen Zustand zaehlt, nicht ueber den frueheren
# ("war mal versiegelt").
_SEALED_RE = re.compile(
    rf"(?<!war\s)(?<!war\smal\s)(noch\s+)?(versiegelt|unge{_OE}ffnet|sealed|misb)"
    rf"|(nie|nicht)\s+ge{_OE}ffnet"
    r"|originalverpackt",
    re.IGNORECASE,
)

# Versiegelte Innenbeutel sind kein versiegelter Karton.
_INNER_BAGS_RE = re.compile(rf"t{_UE}ten|beutel|innenverpackung|baggies", re.IGNORECASE)

# ── Kartonschaden ────────────────────────────────────────────────────
# Satzweise gelesen: das Objekt muss im selben Satz stehen, damit ein "Riss"
# ohne Bezug nicht den Karton belastet.
_BOX_OBJECT_RE = re.compile(r"karton|box|verpackung|ovp", re.IGNORECASE)
# Wem der Schaden gehoert, entscheidet das Objekt, das davor genannt wird.
# Der Karton irgendwo im Satz reichte nicht: "Karton in gutem Zustand, die
# Figur hat einen Riss" belastete den Karton fuer einen Riss in der Figur.
_OBJECT_RE = re.compile(
    r"(?P<box>karton|schachtel|box|verpackung|ovp)"
    r"|(?P<other>figur\w*|minifig\w*|steine?\b|teile?\b|anleitung\w*|aufkleber"
    r"|sticker|modell|bauwerk|aufbau)",
    re.IGNORECASE,
)


def _damage_belongs_to_the_box(sentence: str, damage_start: int, damage_end: int) -> bool:
    """Whether the object the damage word attaches to is the box.

    German names the object first: "Anleitung hat einen Riss" is the manual's,
    "Karton hat einen Riss" the box's. So the nearest object *before* the
    damage word wins, however far away — that is what carries a topic across a
    comma in "Karton ohne Dellen, aber ein Riss im Deckel". Only when nothing
    precedes it does the object behind decide ("Dellen im Karton").
    """
    nearest_before = None
    nearest_after = None
    for match in _OBJECT_RE.finditer(sentence):
        if match.end() <= damage_start:
            nearest_before = match
        elif match.start() >= damage_end and nearest_after is None:
            nearest_after = match
    chosen = nearest_before or nearest_after
    return chosen is not None and chosen.lastgroup == "box"
# "kratzer" und "gebrauchsspuren" standen hier bewusst nicht drin, solange ein
# Karton-Wort irgendwo im Satz genuegte: beide beschreiben meistens die Steine
# oder das Modell, und der Karton haette deren Schaden mitbezahlt. Seit
# _damage_belongs_to_the_box das Objekt bestimmt, tragen sie — "Die Steine
# haben Kratzer, Karton ist top" bleibt schadenfrei.
_DAMAGE_WORD_RE = re.compile(
    rf"besch{_AE}dig|\bdelle|\bknick|\briss|eingerissen|einriss|gedr{_UE}ckt"
    r"|wasserschaden|wasserfleck|\bkratzer|\bverkratzt|\bgebrauchsspur",
    re.IGNORECASE,
)
# Der Punkt trennt nur mit folgendem Grossbuchstaben — sonst zerfaellt
# "Karton hat ca. 3 Dellen" und der Schaden verliert sein Objekt.
_SENTENCE_SPLIT_RE = re.compile(r"[.;!?]\s+(?=[A-ZÄÖÜ])|\n")


def _has_box_damage(text: str) -> bool:
    """Whether the listing says the box itself is damaged.

    Read sentence by sentence so "Karton ohne Dellen, aber ein Riss im Deckel"
    still reports the crack: the denial covers the dents it stands next to, not
    everything up to the full stop.
    """
    for sentence in _SENTENCE_SPLIT_RE.split(text):
        if not _BOX_OBJECT_RE.search(sentence):
            continue
        for damage in _DAMAGE_WORD_RE.finditer(sentence):
            if _NEG_NEAR_RE.search(sentence[: damage.start()]):
                continue
            if not _damage_belongs_to_the_box(sentence, damage.start(), damage.end()):
                continue
            return True
    return False


def classify_listing_condition(
    condition_label: str | None, description: str | None
) -> tuple[str, bool]:
    """Derive (condition, box_damage) from a listing's own statements.

    The label is the seller's dropdown choice, the description is free text.
    Where they disagree the worse reading wins: a listing marked "Neu" whose
    text says "ohne OVP" is not new goods. The one upgrade allowed is an
    explicit statement that the box is still sealed.
    """
    text = description or ""
    base = _LABEL_TO_CONDITION.get((condition_label or "").strip().upper(), "UNKNOWN")
    box_damage = _has_box_damage(text)

    if _matches_unnegated(_MISSING_VERB_RE, text, "both") or _matches_unnegated(
        _INCOMPLETE_RE, text, "before"
    ):
        return "USED_INCOMPLETE", box_damage

    # Nur abwaerts: ein Label "Sehr Gut" bleibt gebraucht, auch wenn im Text
    # "geoeffnet" steht.
    # Ueber _matches_unnegated wie _OPENED_RE: "nie bespielt" und "nicht
    # gebraucht" sind Aussagen ueber Neuware, nicht ueber Gebrauch.
    if base in ("NEW_OPEN_BOX", "UNKNOWN") and _matches_unnegated(_USED_RE, text, "before"):
        return "USED_COMPLETE", box_damage

    # "nie geoeffnet" ist das Gegenteil von "geoeffnet".
    if base == "UNKNOWN" and _matches_unnegated(_OPENED_RE, text, "before"):
        return "NEW_OPEN_BOX", box_damage

    # Die einzige Stelle, die aufwerten kann — und damit die, die eine
    # Verneinung am teuersten falsch liest: "nicht mehr versiegelt" auf 1.0
    # hochzustufen kostet den vollen Marktpreis.
    if (
        base in ("NEW_OPEN_BOX", "UNKNOWN")
        and _matches_unnegated(_SEALED_RE, text, "before")
        # "Karton geoeffnet, die Tueten sind aber noch versiegelt" ist gerade
        # keine ungeoeffnete Packung — das Objekt entscheidet.
        and not _INNER_BAGS_RE.search(text)
    ):
        return "NEW_SEALED", box_damage

    return base, box_damage
