"""Read the condition off a listing instead of guessing it from the title.

The title "Lego Eisvogel 10331" says nothing about condition, so the scraper
assumed UNKNOWN. The detail page says "Zustand: Sehr Gut" and the description
says "Ohne Anleitung und Karton" — that is a set worth roughly half of the
market price, not a +57 % bargain.

Real values from kleinanzeigen.de: Neu, Sehr Gut, Gut, In Ordnung, Defekt.
"""

from app.domain.condition import classify_listing_condition


class TestConditionLabel:
    def test_neu_without_further_hints_is_open_box(self):
        # "Neu" on a private listing does not promise a sealed box.
        condition, damage = classify_listing_condition("Neu", None)
        assert condition == "NEW_OPEN_BOX"
        assert damage is False

    def test_sehr_gut_is_used_but_complete(self):
        assert classify_listing_condition("Sehr Gut", None)[0] == "USED_COMPLETE"

    def test_gut_is_used_but_complete(self):
        assert classify_listing_condition("Gut", None)[0] == "USED_COMPLETE"

    def test_in_ordnung_is_incomplete(self):
        assert classify_listing_condition("In Ordnung", None)[0] == "USED_INCOMPLETE"

    def test_defekt_is_incomplete(self):
        assert classify_listing_condition("Defekt", None)[0] == "USED_INCOMPLETE"

    def test_missing_label_stays_unknown(self):
        assert classify_listing_condition(None, None)[0] == "UNKNOWN"

    def test_unexpected_label_stays_unknown(self):
        assert classify_listing_condition("Bespielt", None)[0] == "UNKNOWN"


class TestDescriptionOverrides:
    def test_the_kingfisher_listing(self):
        # Exactly what the ad says, fetched 2026-08-21.
        condition, damage = classify_listing_condition(
            "Sehr Gut", "Ohne Anleitung und Karton\nKeine Garantie Haftung oder Rücknahme"
        )
        assert condition == "USED_INCOMPLETE"
        assert damage is False

    def test_missing_box_downgrades_a_new_listing(self):
        # Contradiction between label and text — believe the worse of the two.
        assert classify_listing_condition("Neu", "Neuwertig, aber ohne OVP")[0] == "USED_INCOMPLETE"

    def test_missing_pieces_downgrade(self):
        assert classify_listing_condition("Gut", "Es fehlen ein paar Teile")[0] == "USED_INCOMPLETE"

    def test_explicitly_sealed_upgrades_a_new_listing(self):
        assert classify_listing_condition("Neu", "Originalverpackt und noch versiegelt")[0] == "NEW_SEALED"

    def test_sealed_wording_does_not_upgrade_a_used_listing(self):
        # "war mal versiegelt" must not turn a used set into new goods.
        assert classify_listing_condition("Gut", "Karton war mal versiegelt")[0] == "USED_COMPLETE"

    def test_description_alone_can_establish_sealed(self):
        assert classify_listing_condition(None, "Neu und versiegelt, nie geöffnet")[0] == "NEW_SEALED"


class TestBoxDamage:
    def test_damaged_box_is_flagged(self):
        assert classify_listing_condition("Neu", "Karton hat Dellen")[1] is True

    def test_damage_wording_variants(self):
        for text in ("Verpackung beschädigt", "Box hat einen Knick", "leichte Dellen am Karton"):
            assert classify_listing_condition("Neu", text)[1] is True, text

    def test_intact_listing_is_not_flagged(self):
        assert classify_listing_condition("Neu", "Karton in top Zustand")[1] is False


class TestNegationsDoNotCountAsStatements:
    """Eine Verneinung darf nicht als Aussage gelesen werden.

    Alle drei Muster lagen falsch, jedes in eine andere Richtung: "es fehlen
    keine Teile" wertete ab, "nicht mehr versiegelt" wertete auf, "Karton ohne
    Dellen" erfand einen Schaden.
    """

    def test_missing_parts_denied_stays_complete(self):
        assert classify_listing_condition("Sehr gut", "Es fehlen keine Teile")[0] == "USED_COMPLETE"
        assert classify_listing_condition("Sehr gut", "Es fehlt nichts")[0] == "USED_COMPLETE"

    def test_no_longer_sealed_is_not_an_upgrade(self):
        # Die teuerste Fehllesung: Faktor 1.0 statt 0.9 auf den vollen Marktpreis.
        assert classify_listing_condition("Neu", "Karton geoeffnet, nicht mehr versiegelt")[0] == "NEW_OPEN_BOX"
        assert classify_listing_condition("Neu", "Leider nicht versiegelt")[0] == "NEW_OPEN_BOX"
        assert classify_listing_condition(None, "Nicht versiegelt")[0] == "UNKNOWN"

    def test_an_intact_box_is_not_damage(self):
        for text in ("Karton ohne Dellen oder Knicke", "Karton hat keine Dellen", "Verpackung nicht beschaedigt"):
            assert classify_listing_condition("Neu", text)[1] is False, text

    def test_a_real_statement_still_wins_next_to_a_denied_one(self):
        # Pro Treffer pruefen, nicht pro Text: "ohne Karton" bleibt gueltig.
        condition, _ = classify_listing_condition("Sehr Gut", "Ohne Karton, es fehlen keine Teile")
        assert condition == "USED_INCOMPLETE"


class TestSpellingVariants:
    def test_missing_is_read_in_its_common_form(self):
        # "fehlt" ist haeufiger als "fehlen" und fiel durch: der Stamm ist "fehl".
        for text in ("Es fehlt die Anleitung.", "Die Anleitung fehlt leider.", "Karton fehlt"):
            assert classify_listing_condition("Sehr Gut", text)[0] == "USED_INCOMPLETE", text

    def test_umlauts_may_be_transcribed(self):
        # Auf Kleinanzeigen wird oft ohne Umlaute geschrieben.
        assert classify_listing_condition("Sehr Gut", "Unvollstaendig")[0] == "USED_INCOMPLETE"
        assert classify_listing_condition("Neu", "Verpackung beschaedigt")[1] is True
        assert classify_listing_condition("Neu", "Karton gedrueckt")[1] is True
        assert classify_listing_condition(None, "Nie geoeffnet")[0] == "NEW_SEALED"


class TestNegationOnEitherSideOfTheVerb:
    """Deutsch stellt die Verneinung vor oder hinter das Verb.

    "es fehlen keine Teile" und "keine Teile fehlen" sind dieselbe Aussage;
    der Guard prueft deshalb beide Richtungen.
    """

    def test_denial_in_front_of_the_verb(self):
        for text in (
            "Vollstaendig, keine Teile fehlen",
            "Komplett, keine Steine fehlen",
            "Keine Anleitung fehlt, alles dabei",
        ):
            assert classify_listing_condition("Sehr Gut", text)[0] == "USED_COMPLETE", text

    def test_denial_behind_the_verb(self):
        assert classify_listing_condition("Sehr Gut", "Es fehlen keine Teile")[0] == "USED_COMPLETE"


class TestBoxDamageIsReadPerSentence:
    def test_a_denial_covers_only_what_it_stands_next_to(self):
        # Der Riss zaehlt, obwohl im selben Satz "ohne Dellen" steht.
        assert classify_listing_condition("Neu", "Karton ohne Dellen, aber ein Riss im Deckel")[1] is True

    def test_a_denial_about_something_else_is_not_a_denial_of_damage(self):
        # "nicht mehr original" verneint das Original, nicht die Dellen.
        assert classify_listing_condition("Neu", "Verpackung ist nicht mehr original, hat Dellen")[1] is True

    def test_a_denied_damage_stays_denied(self):
        assert classify_listing_condition("Neu", "Karton ohne Dellen oder Knicke")[1] is False


class TestSealedNeedsTheBoxNotTheBags:
    def test_sealed_inner_bags_are_not_a_sealed_box(self):
        text = "Karton wurde geoeffnet, die Tueten sind aber noch versiegelt"
        assert classify_listing_condition("Neu", text)[0] == "NEW_OPEN_BOX"

    def test_a_genuinely_sealed_box_still_upgrades(self):
        assert classify_listing_condition("Neu", "Originalverpackt und versiegelt")[0] == "NEW_SEALED"


class TestMoreWaysToSayIncomplete:
    def test_original_prefix_and_not_present(self):
        assert classify_listing_condition(None, "Ohne Originalkarton")[0] == "USED_INCOMPLETE"
        assert classify_listing_condition(None, "Originalverpackung nicht vorhanden")[0] == "USED_INCOMPLETE"


class TestBoilerplateDoesNotCancelTheStatement:
    """Der Gewaehrleistungsausschluss ist auf Kleinanzeigen Pflichttext.

    Er steht direkt hinter der Zustandsangabe und beginnt fast immer mit
    "Keine ...". Als Verneinung gelesen, hob er die Zeile davor auf und hob den
    erwarteten Erloes um 40 % — auf genau den unvollstaendigen Sets, fuer die
    das Modul geschrieben wurde.
    """

    def test_a_new_line_separates_statements(self):
        for text in (
            "Ohne OVP\nKeine Rücknahme",
            "Karton nicht mehr vorhanden\nKeine Gewährleistung",
            "Set ist unvollständig\nKeine Garantie, da Privatverkauf",
            "Anleitung fehlt\nKeine Rücknahme möglich",
        ):
            assert classify_listing_condition(None, text)[0] == "USED_INCOMPLETE", text

    def test_a_phrase_can_only_be_denied_from_the_front(self):
        # "ohne Karton" laesst sich nicht von hinten verneinen — anders als ein Verb.
        assert classify_listing_condition(None, "Verkaufe ohne Karton keine Rücknahme")[0] == "USED_INCOMPLETE"


class TestUsedAndOpenedAreRead:
    """Ohne diese Lesung faellt ein Titel auf UNKNOWN zurueck — und wo ein
    Aufrufer NEW_SEALED als Default fuehrt, wird daraus Faktor 1.0."""

    def test_used_wording(self):
        for text in ("Lego Eisvogel 10331 gebraucht", "Lego 10331 bespielt"):
            assert classify_listing_condition(None, text)[0] == "USED_COMPLETE", text

    def test_opened_wording(self):
        for text in ("10276 Kolosseum aufgebaut", "10276 geöffnet, aber komplett"):
            assert classify_listing_condition(None, text)[0] == "NEW_OPEN_BOX", text

    def test_a_worse_label_is_not_upgraded_by_opened(self):
        assert classify_listing_condition("Sehr Gut", "10276 geöffnet")[0] == "USED_COMPLETE"

    def test_not_opened_is_the_opposite_of_opened(self):
        for text in ("Nicht geöffnet", "Ungeoeffnet", "Noch versiegelt, nie geoeffnet"):
            assert classify_listing_condition(None, text)[0] == "NEW_SEALED", text


class TestDamageVocabulary:
    def test_singular_and_noun_forms(self):
        # "Kratzer" fehlt hier bewusst — siehe TestWearIsNotBoxDamage.
        for text in ("Karton hat eine Delle", "Karton mit Beschädigungen", "Karton ist gedrueckt"):
            assert classify_listing_condition("Neu", text)[1] is True, text

    def test_an_abbreviation_does_not_end_the_sentence(self):
        # "ca." darf den Satz nicht trennen, sonst verliert der Schaden sein Objekt.
        assert classify_listing_condition("Neu", "Karton hat ca. 3 kleine Dellen")[1] is True


class TestUnPrefixIsNotTheWord:
    """"ungebraucht" ist das Gegenteil von "gebraucht" — und ohne Wortgrenze
    machte das Muster aus versiegelter Ware Faktor 0.7 plus 2 Risikopunkte."""

    def test_un_prefixed_words_do_not_mean_used(self):
        text = "Set ist ungebraucht und noch originalverpackt, die Folie ist unbeschaedigt."
        assert classify_listing_condition("Neu", text)[0] == "NEW_SEALED"

    def test_a_denied_use_does_not_block_the_upgrade(self):
        for text in ("Versiegelt, nie bespielt", "MISB, unbespielt", "Set ist versiegelt, nicht gebraucht"):
            assert classify_listing_condition("Neu", text)[0] == "NEW_SEALED", text

    def test_real_used_wording_still_lands(self):
        for text in ("Set ist gebraucht", "Gebrauchte Ware", "Gebrauchtware"):
            assert classify_listing_condition(None, text)[0] == "USED_COMPLETE", text


class TestMoreGermanWaysToSayIncomplete:
    def test_compound_nouns(self):
        for text in ("ohne Bauanleitung", "ohne die Bauanleitung", "ohne Bauanleitungen", "ohne Umkarton"):
            assert classify_listing_condition(None, text)[0] == "USED_INCOMPLETE", text

    def test_an_unrelated_compound_is_not_a_missing_part(self):
        assert classify_listing_condition(None, "ohne Werkzeugbox")[0] == "UNKNOWN"

    def test_verb_second_word_order(self):
        # Deutsche Inversion: das Nomen steht hinter dem Verb.
        for text in ("Leider fehlt die Anleitung", "Anleitungen fehlen", "Es fehlen ein paar Teile"):
            assert classify_listing_condition(None, text)[0] == "USED_INCOMPLETE", text

    def test_the_denial_survives_the_inversion(self):
        for text in ("Es fehlen keine Teile", "Vollstaendig, keine Teile fehlen", "Es fehlt nichts"):
            assert classify_listing_condition(None, text)[0] == "UNKNOWN", text


class TestWearIsNotBoxDamage:
    def test_scratches_and_wear_describe_the_bricks(self):
        # _has_box_damage verlangt nur das Karton-Wort im selben Satz — mit
        # "Gebrauchsspuren" im Wortschatz behauptete die Karte einen
        # Kartonschaden, wo keiner steht.
        for text in (
            "Verkaufe das Set inkl. Karton und Anleitung, es hat leichte Gebrauchsspuren.",
            "Alles komplett mit OVP, Steine mit minimalen Gebrauchsspuren",
        ):
            assert classify_listing_condition(None, text)[1] is False, text

    def test_real_box_damage_still_lands(self):
        for text in ("Karton hat Dellen", "Karton mit Beschädigungen", "Karton hat eine Delle"):
            assert classify_listing_condition(None, text)[1] is True, text


class TestAPromiseIsNotADefect:
    """"ohne Verpackungsschaeden" sagt das Gegenteil von "ohne Verpackung".

    Ein pauschales \\b ginge nicht: "ohne Anleitungsheft" und "ohne Kartonage"
    sind echte Signale. Deshalb ein gezielter Lookahead — inklusive Fugen-s,
    Bindestrich und der ae-Umschrift.
    """

    def test_damage_compounds_are_assurances(self):
        for text in (
            "ohne Verpackungsschäden",
            "ohne Verpackungsschaeden",
            "keine Kartonschaeden",
            "ohne Kartonmaengel",
            "ohne OVP-Schäden",
            "OVP ohne Kartonschaden.",
            "Versand ohne Verpackungsmaterial möglich.",
        ):
            assert classify_listing_condition(None, text)[0] == "UNKNOWN", text

    def test_other_compounds_are_still_signals(self):
        for text in ("ohne Anleitungsheft", "ohne Kartonage", "keine Kartonage"):
            assert classify_listing_condition(None, text)[0] == "USED_INCOMPLETE", text


class TestKeinForm:
    """"Keine Anleitung" ist mindestens so haeufig wie "ohne Anleitung"."""

    def test_kein_is_read_as_missing(self):
        for text in (
            "Keine Anleitung und kein Karton",
            "Keine Anleitung mehr vorhanden",
            "Keine Anleitung, keine OVP, nur die Steine",
            "keine Originalanleitung",
        ):
            assert classify_listing_condition("Sehr Gut", text)[0] == "USED_INCOMPLETE", text

    def test_kein_in_front_of_the_verb_is_the_denial(self):
        # "keine Anleitung dabei" ist die Aussage, "keine Anleitung fehlt" ihre
        # Verneinung — das folgende Verb entscheidet.
        assert classify_listing_condition("Sehr Gut", "Keine Anleitung fehlt, alles dabei")[0] == "USED_COMPLETE"


class TestPickupPostcodeWithAPreposition:
    def test_a_filler_word_may_sit_between(self):
        from app.domain.identity import mentions_other_set_numbers

        for title in (
            "LEGO 10331 Eisvogel Abholung in 40233 Düsseldorf",
            "LEGO 10331 Eisvogel Selbstabholung in 40233",
            "LEGO 10331 Eisvogel Versand aus 40233",
            "LEGO 10331 Eisvogel, Standort ist 40233",
        ):
            assert mentions_other_set_numbers(title, "10331") is False, title

    def test_a_bundle_next_to_a_postcode_is_still_a_bundle(self):
        from app.domain.identity import mentions_other_set_numbers

        title = "10331 Kolibri und 10326 Botanical, Abholung in 40233"
        assert mentions_other_set_numbers(title, "10331") is True

