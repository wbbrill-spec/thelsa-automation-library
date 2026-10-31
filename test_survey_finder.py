import survey_finder as sf


def test_survey_words_and_satisfaction_surveys():
    assert sf.survey_hint("Confirmo la visita de inspección el lunes") == "inspeccion"
    assert sf.survey_hint("Pre-move survey scheduled for Tuesday") == "survey"
    assert sf.survey_hint("Encuesta de satisfacción - please rate our survey") is None
    assert sf.survey_hint("Invoice attached") is None


def test_surname_skips_companies_and_short_names():
    assert sf.surname("Smith, John") == "Smith"
    assert sf.surname("Maria Lopez") == "Lopez"
    assert sf.surname("Embajada de USA") is None
    assert sf.surname("Global Relocation Inc") is None
    assert sf.surname("Li") is None


def test_former_staff_files_search_every_mailbox():
    assert sf._mailboxes_for("sarareyes@thelsa.com") == ["sarareyes@thelsa.com"]
    assert len(sf._mailboxes_for("someone.gone@thelsa.com")) == len(sf.MAILBOXES)


def test_pack_and_delivery_words():
    assert sf.hint_for("Confirmamos la carga el 12 de septiembre", "pack") == "carga"
    assert sf.hint_for("Entrega programada para el lunes", "delivery") == "entrega"
    assert sf.hint_for("Invoice attached", "delivery") is None
