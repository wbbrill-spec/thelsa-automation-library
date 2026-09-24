"""
Counting the files TIM actually opened — including the finished ones.

The board shows open cross-border shipments. A year-to-date count is a
different question and gets a different answer, so the census walks every
shipment space (Completed included) and dates each list by its first task.
"""
import datetime as dt

from crossborder import census

TODAY = dt.date(2026, 9, 24)


class FakeClient:
    """A miniature TIM workspace."""

    def __init__(self, spaces, lists, task_dates):
        self._spaces, self._lists, self._dates = spaces, lists, task_dates
        self.requests_made = 0

    def teams(self):
        return [{"id": "T1"}]

    def spaces(self, team_id):
        return self._spaces

    def folderless_lists(self, space_id):
        return [l for l in self._lists if l["space"] == space_id and not l.get("folder")]

    def folders(self, space_id):
        names = {l["folder"] for l in self._lists if l["space"] == space_id and l.get("folder")}
        return [{"id": f"F-{n}", "name": n,
                 "lists": [l for l in self._lists if l.get("folder") == n]} for n in sorted(names)]

    def lists_in_folder(self, folder_id):
        return []

    def get(self, path, **kw):
        self.requests_made += 1
        lid = path.split("/")[1]
        stamps = self._dates.get(lid)
        if stamps is None:
            return {"tasks": []}
        return {"tasks": [{"date_created": str(int(
            dt.datetime.combine(d, dt.time(), dt.timezone.utc).timestamp() * 1000))}
            for d in stamps]}


def mk(list_id, name, space, folder=None, tasks=59):
    return {"id": list_id, "name": name, "space": space, "folder": folder, "task_count": tasks}


SPACES = [{"id": "S1", "name": "Logistics Coordination"},
          {"id": "S2", "name": "Completed 2026"},
          {"id": "S3", "name": "Templates"},
          {"id": "S4", "name": "Procesos Certificados de Menaje"}]


def build(**over):
    lists = [
        mk("1", "Ana Ruiz - UHaul - 121722", "S1", "U-HAUL"),
        mk("2", "Luis Mora - Logicstics - 130564", "S1", "LOGICSTICS"),
        mk("3", "List", "S1", "U-HAUL"),                      # placeholder
        mk("4", "Old File - 2025", "S2", None),
        mk("5", "Carol Ashworth - SDC - 134456", "S2", None),
        mk("6", "*** Formato Seguimiento DA", "S3", None),     # template space
        mk("7", "Certificado - Someone", "S4", None),          # certificate space
    ]
    dates = {
        "1": [dt.date(2026, 2, 10), dt.date(2026, 2, 10)],
        "2": [dt.date(2026, 5, 3)],
        "4": [dt.date(2025, 11, 2)],
        "5": [dt.date(2026, 2, 27)],
        "6": [dt.date(2026, 1, 5)],
        "7": [dt.date(2026, 3, 3)],
    }
    dates.update(over)
    return FakeClient(SPACES, lists, dates)


# ── what counts as a file ────────────────────────────────────────────────────
def test_finished_files_are_counted_not_just_the_open_ones():
    """The whole point: the board shows open files, the year does not."""
    c = census.census(build(), year=2026, today=TODAY)
    assert c["files"] == 3          # two active, one in Completed
    spaces = {r["space"]: r["files"] for r in c["by_space"]}
    assert spaces["Completed 2026"] == 1


def test_the_placeholder_list_in_every_folder_is_not_a_file():
    c = census.census(build(), year=2026, today=TODAY)
    assert "List" not in [n for n in c["undated_sample"]]
    assert c["files"] == 3


def test_templates_and_certificate_processes_are_not_files():
    c = census.census(build(), year=2026, today=TODAY)
    assert "Templates" in c["spaces_skipped"]
    assert any("Certificados" in s for s in c["spaces_skipped"])


def test_last_years_files_are_reported_but_not_counted_in_the_year():
    c = census.census(build(), year=2026, today=TODAY)
    assert c["files"] == 3
    assert c["before_this_year"] == 1


def test_a_list_with_no_tasks_is_not_a_file():
    client = build()
    client._lists.append(mk("9", "Empty shell", "S1", None, tasks=0))
    c = census.census(client, year=2026, today=TODAY)
    assert c["files"] == 3


# ── the dating rule ──────────────────────────────────────────────────────────
def test_a_file_is_dated_by_its_earliest_task():
    """A template clone stamps every task at once, so the earliest is the
    moment the file was opened."""
    c = census.census(build(**{"1": [dt.date(2026, 4, 9), dt.date(2026, 1, 15),
                                     dt.date(2026, 7, 1)]}), year=2026, today=TODAY)
    months = {r["month"]: r["files"] for r in c["by_month"]}
    assert months["2026-01"] == 1
    assert "2026-04" not in months


def test_an_undated_list_is_reported_rather_than_guessed():
    client = build()
    client._lists.append(mk("8", "Mystery file", "S1", None))
    c = census.census(client, year=2026, today=TODAY)
    assert c["undated"] == 1
    assert "Mystery file" in c["undated_sample"]
    assert c["files"] == 3          # not invented into a month


# ── the average ──────────────────────────────────────────────────────────────
def test_the_part_month_counts_as_a_fraction():
    """24 September is 0.8 of a month. Counting it whole would understate the
    run rate; ignoring it would overstate it."""
    c = census.census(build(), year=2026, today=TODAY)
    assert c["first_file_opened"] == "2026-02-10"
    # Feb 10 → Sep 24 = 7 whole months + 24/30
    assert c["months_elapsed"] == round(7 + 24 / 30, 2)
    assert c["per_month_average"] == round(3 / c["months_elapsed"], 1)


def test_the_year_starts_at_the_first_real_file_not_january():
    """If nothing was booked in January the average must not be diluted by it."""
    c = census.census(build(), year=2026, today=TODAY)
    assert c["first_file_opened"].startswith("2026-02")


def test_an_empty_year_does_not_divide_by_zero():
    c = census.census(build(), year=2019, today=TODAY)
    assert c["files"] == 0
    assert c["per_month_average"] is None


def test_it_says_how_it_counted():
    c = census.census(build(), year=2026, today=TODAY)
    assert "earliest task creation" in c["basis"]
    assert c["requests_made"] > 0
