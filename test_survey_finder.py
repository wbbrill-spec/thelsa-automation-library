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


def test_reads_the_date_next_to_the_word():
    import datetime as dt
    r = dt.date(2026, 9, 20)
    assert sf.date_for("Confirmamos la carga el 12 de septiembre", sf.LOAD_WORDS, r)[0] == dt.date(2026, 9, 12)
    assert sf.date_for("La entrega fue el 15/09/2026", sf.DELIVERY_WORDS, r)[0] == dt.date(2026, 9, 15)
    assert sf.date_for("Delivery scheduled for Sept 18, 2026", sf.DELIVERY_WORDS, r)[0] == dt.date(2026, 9, 18)
    assert sf.date_for("Empaque 10/9 y carga 11/9", sf.PACKING_WORDS, r)[0] == dt.date(2026, 9, 10)
    assert sf.date_for("Empaque 10/9 y carga 11/9", sf.LOAD_WORDS, r)[0] == dt.date(2026, 9, 11)
    assert sf.date_for("descarga el 22 de sep", sf.LOAD_WORDS, r) == (None, None)   # unload is not load


def test_only_file_number_matches_in_the_past_are_written():
    import datetime as dt
    today = dt.date(2026, 10, 31)
    hits = [
        {"matched_by": "file number", "found_date": "2026-09-11", "kind": "load", "date": "2026-09-05"},
        {"matched_by": "file number", "found_date": "2026-09-12", "kind": "load", "date": "2026-09-10"},  # later email wins
        {"matched_by": "surname", "found_date": "2026-09-30", "kind": "delivery", "date": "2026-09-29"},  # not by number
        {"matched_by": "file number", "found_date": "2026-11-20", "kind": "delivery", "date": "2026-10-01"},  # future
    ]
    assert sf.chosen_dates(hits, today=today, key_date="2026-09-12") == {"load": dt.date(2026, 9, 12)}
    # a delivery before the load is unclear
    hits2 = hits[:2] + [{"matched_by": "file number", "found_date": "2026-09-01", "kind": "delivery", "date": "2026-09-02"}]
    assert "delivery" not in sf.chosen_dates(hits2, today=today)
