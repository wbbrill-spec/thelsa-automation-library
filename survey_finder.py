"""Survey finder (Bill, 31 Oct 2026: "scan the TMS coordinator emails and look
for the missing surveys").

For every audited file with no survey date in Moveware, search the TMS
coordinator mailboxes (read-only, app-only Graph, the same access the final-charges
scan uses) for emails about that file - by file number, and by the customer's
surname - and keep the ones that talk about a survey (survey, inspeccion, visita,
levantamiento, video call ...). The result is a worklist: file, coordinator, the
email(s) that look like survey evidence, and the date - so the survey date can be
entered in Moveware. Nothing is sent and nothing is written anywhere.
"""
from __future__ import annotations

import re
import threading
import time
import unicodedata

import requests

import ms_graph

SURVEY_WORDS = (
    "survey", "inspeccion", "levantamiento", "visita", "pre-move", "premove",
    "pre move", "videollamada", "video llamada", "video call", "videocall",
    "virtual", "walkthrough", "walk-through", "walk through", "cubicaje",
    "inventario visual",
)
# a satisfaction / quality survey is about the end of the move, not the pre-move survey
NOT_SURVEY = ("satisfaction", "satisfaccion", "encuesta", "quality survey",
              "customer feedback", "nps")

_STATE = {"running": False, "started": None, "finished": None, "error": None,
          "files": 0, "done": 0, "searches": 0, "forbidden": 0, "results": [],
          "diag": None}
_LOCK = threading.Lock()


def _norm(s: str) -> str:
    s = unicodedata.normalize("NFKD", s or "")
    return "".join(c for c in s if not unicodedata.combining(c)).lower()


def survey_hint(text: str) -> str | None:
    """The survey word found in `text`, or None (a satisfaction survey doesn't count)."""
    t = _norm(text)
    for w in SURVEY_WORDS:
        if w in t:
            # "survey" inside a satisfaction-survey email is not a pre-move survey
            if any(n in t for n in NOT_SURVEY) and w in ("survey",):
                continue
            return w
    return None


_STOP = {"de", "del", "la", "las", "los", "y", "van", "von", "da", "mr", "mrs", "ms",
         "sr", "sra", "the", "and", "embajada", "embassy", "usa", "us", "s.a.", "sa",
         "de c.v.", "cv", "inc", "llc", "ltd", "family", "familia"}


def surname(client: str) -> str | None:
    """A searchable surname from Moveware's client name ('Smith, John' or 'John
    Smith'), or None when it is a company/embassy or too short to be useful."""
    c = (client or "").strip()
    if not c or any(k in _norm(c) for k in ("embass", "embajada", "s.a.", "inc", "llc",
                                              "ltd", "gmbh", "relocation", "moving",
                                              "logistic", "mudanza")):
        return None
    part = c.split(",")[0] if "," in c else c.split()[-1]
    part = re.sub(r"[^A-Za-zÀ-ÿ'\- ]", "", part).strip()
    if len(part) < 4 or _norm(part) in _STOP:
        return None
    return part


# Daniela Martinez coordinates TMS files too (she is on the dashboard)
MAILBOXES = list(dict.fromkeys(ms_graph.TMS_COORDINATORS + ["danielamartinez@thelsa.com"]))


def _mailboxes_for(coord_email: str) -> list[str]:
    e = (coord_email or "").strip().lower()
    if e in MAILBOXES:
        return [e]
    return list(MAILBOXES)     # former staff / unknown: look everywhere


def _search(mbx: str, term: str, h: dict, diag: dict) -> list[dict]:
    params = {"$search": f'"{term}"',
              "$select": "subject,from,receivedDateTime,bodyPreview,webLink,body",
              "$top": "25"}
    try:
        r = requests.get(f"{ms_graph.GRAPH}/users/{mbx}/messages",
                         headers=h, params=params, timeout=25)
    except Exception as e:
        diag["errors"] += 1
        diag["first_error"] = diag["first_error"] or f"{mbx}: {e}"[:160]
        return []
    diag["searches"] += 1
    if r.status_code == 403:
        diag["forbidden"] += 1
        return []
    if r.status_code != 200:
        diag["errors"] += 1
        diag["first_error"] = diag["first_error"] or f"{mbx}: {r.status_code} {ms_graph._short(r)}"
        return []
    return r.json().get("value") or []


def find_for_file(row: dict, client: str, h: dict, diag: dict) -> list[dict]:
    """Survey-looking emails for one file, newest first (at most 3)."""
    number = str(row.get("file") or "")
    base = re.match(r"\d{5,7}", number)
    terms = [base.group(0)] if base else []
    sn = surname(client)
    if sn:
        terms.append(sn)
    seen, hits = set(), []
    for mbx in _mailboxes_for(row.get("coord_email")):
        for term in terms:
            for m in _search(mbx, term, h, diag):
                key = (m.get("subject"), m.get("receivedDateTime"))
                if key in seen:
                    continue
                seen.add(key)
                text = (m.get("subject") or "") + " " + ms_graph._plain(m)
                word = survey_hint(text)
                if not word:
                    continue
                # matched on the surname only: the file number or full client
                # name must also be there, or it may be another customer
                by_number = bool(base) and base.group(0) in text
                if not by_number and term == sn and _norm(client) not in _norm(text):
                    by = "surname"
                else:
                    by = "file number" if by_number else "customer name"
                hits.append({
                    "mailbox": mbx,
                    "date": (m.get("receivedDateTime") or "")[:10],
                    "subject": (m.get("subject") or "")[:140],
                    "from": ((m.get("from") or {}).get("emailAddress") or {}).get("address", ""),
                    "word": word,
                    "matched_by": by,
                    "snippet": (m.get("bodyPreview") or "")[:220],
                    "link": m.get("webLink") or "",
                })
    order = {"file number": 0, "customer name": 1, "surname": 2}
    hits.sort(key=lambda x: (order[x["matched_by"]], x["date"]), reverse=False)
    # best match first: by file number, then the earliest email (a survey comes early)
    return hits[:3]


def run(missing: list[dict], clients: dict[str, str]) -> None:
    """Background pass over `missing` (faim_snapshot()['missing_survey'])."""
    diag = {"searches": 0, "forbidden": 0, "errors": 0, "first_error": None, "token_ok": False}
    with _LOCK:
        _STATE.update(running=True, started=time.time(), finished=None, error=None,
                      files=len(missing), done=0, results=[], diag=diag)
    try:
        tok = ms_graph._get_token()
        diag["token_ok"] = bool(tok)
        if not tok:
            raise RuntimeError("could not sign in to Microsoft 365 (mail reading credential)")
        h = {"Authorization": f"Bearer {tok}", "ConsistencyLevel": "eventual",
             "Prefer": 'outlook.body-content-type="text"'}
        out = []
        for row in missing:
            hits = find_for_file(row, clients.get(row["file"], ""), h, diag)
            out.append(dict(row, hits=hits))
            with _LOCK:
                _STATE["done"] += 1
                _STATE["results"] = list(out)
        with _LOCK:
            _STATE["results"] = out
    except Exception as e:
        with _LOCK:
            _STATE["error"] = str(e)[:200]
    finally:
        with _LOCK:
            _STATE.update(running=False, finished=time.time())


def start(missing, clients) -> bool:
    with _LOCK:
        if _STATE["running"]:
            return False
        _STATE["running"] = True
    threading.Thread(target=run, args=(missing, clients), daemon=True,
                     name="survey-finder").start()
    return True


def state() -> dict:
    with _LOCK:
        return dict(_STATE, results=list(_STATE["results"]))
