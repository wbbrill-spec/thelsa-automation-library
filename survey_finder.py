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

# Words that show an email is about a pack/load or a delivery (Bill, 31 Oct 2026:
# "also scan their emails for the missing pack/load and delivery dates")
LOAD_WORDS = ("carga", "cargar", "cargamos", "cargado", "loading", "loaded", "uplift",
              "pick up", "pickup", "recoleccion")
PACKING_WORDS = ("empaque", "empacar", "empacamos", "embalaje", "packing", "packed", "huacal")
PACK_WORDS = LOAD_WORDS + PACKING_WORDS
DELIVERY_WORDS = ("entrega", "entregamos", "entregado", "descarga", "delivery", "delivered",
                  "desempaque", "unpacking", "proof of delivery")
_NEED_WORDS = {"pack": PACK_WORDS, "delivery": DELIVERY_WORDS}


def _word_re(w: str):
    return re.compile(r"(?<![a-z])" + re.escape(w) + r"(?![a-z])")


def date_hint(text: str, need: str) -> str | None:
    t = _norm(text)
    for w in _NEED_WORDS[need]:
        if _word_re(w).search(t):
            return w
    return None


# ── reading the date out of the email ───────────────────────────────────────────
_MONTHS = {
    "enero": 1, "ene": 1, "january": 1, "jan": 1,
    "febrero": 2, "feb": 2, "february": 2,
    "marzo": 3, "mar": 3, "march": 3,
    "abril": 4, "abr": 4, "april": 4, "apr": 4,
    "mayo": 5, "may": 5,
    "junio": 6, "jun": 6, "june": 6,
    "julio": 7, "jul": 7, "july": 7,
    "agosto": 8, "ago": 8, "august": 8, "aug": 8,
    "septiembre": 9, "setiembre": 9, "sep": 9, "sept": 9, "september": 9,
    "octubre": 10, "oct": 10, "october": 10,
    "noviembre": 11, "nov": 11, "november": 11,
    "diciembre": 12, "dic": 12, "december": 12, "dec": 12,
}
_MON = "|".join(sorted(_MONTHS, key=len, reverse=True))
_DATE_RES = [
    ("iso", re.compile(r"(?<!\d)(20\d\d)-(\d{1,2})-(\d{1,2})(?!\d)")),
    ("dmy", re.compile(r"(?<![\d/.-])(\d{1,2})[/.-](\d{1,2})(?:[/.-](\d{2,4}))?(?![\d/.-])")),
    ("d_mon", re.compile(r"(?<!\d)(\d{1,2})(?:st|nd|rd|th)?\s*(?:de\s+|of\s+)?(" + _MON + r")\.?(?![a-z])(?:,?\s*(?:de(?:l)?\s+)?(20\d\d))?")),
    ("mon_d", re.compile(r"(?<![a-z])(" + _MON + r")\.?\s+(\d{1,2})(?:st|nd|rd|th)?(?!\d)(?:,?\s*(20\d\d))?")),
]


def _mk(y, m, d, received):
    import datetime as _dt
    try:
        m, d = int(m), int(d)
        if y:
            y = int(y)
            y = y + 2000 if y < 100 else y
            return _dt.date(y, m, d)
        # no year: the one that puts the date nearest the email's own date
        best = None
        for yy in (received.year - 1, received.year, received.year + 1):
            try:
                c = _dt.date(yy, m, d)
            except ValueError:
                continue
            if best is None or abs((c - received).days) < abs((best - received).days):
                best = c
        return best
    except (ValueError, TypeError):
        return None


def dates_in(text: str, received) -> list:
    """[(position, date)] of every date written in `text` (Mexican day/month
    order for 12/09/2026)."""
    t = _norm(text)
    out = []
    for kind, rx in _DATE_RES:
        for m in rx.finditer(t):
            if kind == "iso":
                d = _mk(m.group(1), m.group(2), m.group(3), received)
            elif kind == "dmy":
                d = _mk(m.group(3), m.group(2), m.group(1), received)
            elif kind == "d_mon":
                d = _mk(m.group(3), _MONTHS[m.group(2)], m.group(1), received)
            else:
                d = _mk(m.group(3), _MONTHS[m.group(1)], m.group(2), received)
            if d:
                out.append((m.start(), d))
    return out


def date_for(text: str, need, received):
    """(date, word) for the pack/load or delivery the email talks about: the date
    written closest to the word (within ~120 characters), or (None, None).
    Two different dates equally close = unclear = None."""
    t = _norm(text)
    found = dates_in(text, received)
    if not found:
        return None, None
    words = _NEED_WORDS[need] if isinstance(need, str) else need
    best = None
    for w in words:
        for m in _word_re(w).finditer(t):
            for pos, d in found:
                gap = pos - m.end() if pos >= m.start() else (m.start() - pos) + 15
                if gap > 120:
                    continue
                if best is None or gap < best[0]:
                    best = (gap, d, w)
                elif gap == best[0] and d != best[1]:
                    best = (gap, None, w)
    if not best or best[1] is None:
        return None, None
    return best[1], best[2]


def hint_for(text: str, need: str) -> str | None:
    return survey_hint(text) if need == "survey" else date_hint(text, need)


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


def email_steps() -> list:
    """sit-shaped day lists for milestone_plan from the last finder run:
    [(file, job_type, {"load"/"packing"/"delivery": {"date", "status": "done"}})]."""
    st = state()
    out = []
    for r in st.get("results") or []:
        d = r.get("email_dates") or {}
        if d:
            out.append((r["file"], r.get("type") or "",
                        {k: {"date": v, "status": "done", "source": "email"} for k, v in d.items()}))
    return out


def delegated_mailboxes() -> dict:
    """{email: user_id} of Thelsa people who connected their own mailbox in the
    app (the read-only Mail.Read César granted works per person, through their own
    sign-in). Empty if the assistant database isn't there."""
    try:
        from assistant import db
        return {str(u["email"]).strip().lower(): u["id"]
                for u in db.users_to_scan("microsoft") if u.get("email")}
    except Exception:
        return {}


def _auth_for(mbx: str, app_h: dict | None, delegated: dict, diag: dict):
    """(headers, url) to search this mailbox: the person's own connection first,
    else the app-wide credential."""
    uid = delegated.get(mbx)
    if uid:
        try:
            from assistant import graph as ag
            tok = ag.access_token(uid)
            return ({"Authorization": f"Bearer {tok}", "ConsistencyLevel": "eventual",
                     "Prefer": 'outlook.body-content-type="text"'},
                    f"{ms_graph.GRAPH}/me/messages")
        except Exception as e:
            diag["reconnect"] = sorted(set(diag.get("reconnect", [])) | {mbx})
            diag["first_error"] = diag["first_error"] or f"{mbx}: {e}"[:160]
    if app_h:
        return app_h, f"{ms_graph.GRAPH}/users/{mbx}/messages"
    return None, None


def _search(mbx: str, term: str, h: dict, diag: dict) -> list[dict]:
    params = {"$search": f'"{term}"',
              "$select": "subject,from,receivedDateTime,bodyPreview,webLink,body",
              "$top": "25"}
    hh, url = _auth_for(mbx, h, diag.get("_delegated") or {}, diag)
    if not hh:
        diag["forbidden"] += 1
        diag["searches"] += 1
        return []
    try:
        r = requests.get(url, headers=hh, params=params, timeout=25)
    except Exception as e:
        diag["errors"] += 1
        diag["first_error"] = diag["first_error"] or f"{mbx}: {e}"[:160]
        return []
    diag["searches"] += 1
    if r.status_code in (401, 403):
        diag["forbidden"] += 1
        diag.setdefault("refused_mailboxes", [])
        if mbx not in diag["refused_mailboxes"]:
            diag["refused_mailboxes"].append(mbx)
        return []
    diag.setdefault("read_mailboxes", [])
    if mbx not in diag["read_mailboxes"]:
        diag["read_mailboxes"].append(mbx)
    if r.status_code != 200:
        diag["errors"] += 1
        diag["first_error"] = diag["first_error"] or f"{mbx}: {r.status_code} {ms_graph._short(r)}"
        return []
    return r.json().get("value") or []


def find_for_file(row: dict, client: str, h: dict, diag: dict, keep_all: list | None = None) -> list[dict]:
    """Emails about what the file is missing (row["needs"]: survey / pack /
    delivery), best first (at most 2 per need)."""
    needs = row.get("needs") or ["survey"]
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
                by_number = bool(base) and base.group(0) in text
                if by_number:
                    by = "file number"
                elif _norm(client) and _norm(client) in _norm(text):
                    by = "customer name"
                else:
                    by = "surname"
                for need in needs:
                    word = hint_for(text, need)
                    if not word:
                        continue
                    found = []          # (kind, date, word)
                    if need in ("pack", "delivery") and by_number:
                        import datetime as _dt
                        try:
                            rec_day = _dt.date.fromisoformat((m.get("receivedDateTime") or "")[:10])
                        except ValueError:
                            rec_day = _dt.date.today()
                        groups = ([("load", LOAD_WORDS), ("packing", PACKING_WORDS)]
                                  if need == "pack" else [("delivery", DELIVERY_WORDS)])
                        for kind, words in groups:
                            when, w2 = date_for(text, words, rec_day)
                            if when:
                                found.append((kind, when, w2))
                    for kind, when, w2 in (found or [(need, None, word)]):
                      hits.append({
                        "found_date": when.isoformat() if when else "",
                        "kind": kind,
                        "need": need,
                        "mailbox": mbx,
                        "date": (m.get("receivedDateTime") or "")[:10],
                        "subject": (m.get("subject") or "")[:140],
                        "from": ((m.get("from") or {}).get("emailAddress") or {}).get("address", ""),
                        "word": w2,
                        "matched_by": by,
                        "snippet": (m.get("bodyPreview") or "")[:220],
                        "link": m.get("webLink") or "",
                      })
    if keep_all is not None:
        keep_all.extend(hits)
    order = {"file number": 0, "customer name": 1, "surname": 2}
    out = []
    for need in needs:
        mine = [x for x in hits if x["need"] == need]
        # best match first; a survey comes early (oldest first), a pack/delivery
        # date is best confirmed by the latest email
        mine.sort(key=lambda x: x["date"], reverse=(need != "survey"))
        mine.sort(key=lambda x: order[x["matched_by"]])
        out += mine[:2]
    return out


def chosen_dates(hits: list, today=None, key_date: str = "") -> dict:
    """{"load"|"packing"|"delivery": date} worth writing to Moveware: from emails
    matched by FILE NUMBER only, the latest such email's date, in the past, from
    2025 on, and within 6 months of the file's move date."""
    import datetime as _dt
    today = today or _dt.date.today()
    try:
        anchor = _dt.date.fromisoformat(key_date[:10]) if key_date else None
    except ValueError:
        anchor = None
    best = {}
    for x in hits:
        if x.get("matched_by") != "file number" or not x.get("found_date"):
            continue
        d = _dt.date.fromisoformat(x["found_date"])
        if d > today or d < _dt.date(2025, 1, 1):
            continue
        if anchor and abs((d - anchor).days) > 183:
            continue
        k = x.get("kind")
        if k not in ("load", "packing", "delivery"):
            continue
        if k not in best or x["date"] > best[k][0]:       # latest email wins
            best[k] = (x["date"], d)
    out = {k: v[1] for k, v in best.items()}
    first_move = min([d for k, d in out.items() if k != "delivery"], default=None)
    if "delivery" in out and first_move and out["delivery"] < first_move:
        out.pop("delivery")                               # delivered before it left: unclear
    return out


def run(missing: list[dict], clients: dict[str, str]) -> None:
    """Background pass over `missing` (faim_snapshot()['missing_survey'])."""
    diag = {"searches": 0, "forbidden": 0, "errors": 0, "first_error": None, "token_ok": False}
    with _LOCK:
        _STATE.update(running=True, started=time.time(), finished=None, error=None,
                      files=len(missing), done=0, results=[], diag=diag)
    try:
        tok = ms_graph._get_token()
        diag["token_ok"] = bool(tok)
        diag["_delegated"] = delegated_mailboxes()
        diag["connected"] = sorted(diag["_delegated"])
        if not tok and not diag["_delegated"]:
            raise RuntimeError("could not sign in to Microsoft 365 (mail reading credential)")
        h = ({"Authorization": f"Bearer {tok}", "ConsistencyLevel": "eventual",
              "Prefer": 'outlook.body-content-type="text"'} if tok else None)
        out = []
        for row in missing:
            every = []
            hits = find_for_file(row, clients.get(row["file"], ""), h, diag, keep_all=every)
            dates = chosen_dates(every, key_date=row.get("key_date", ""))
            out.append(dict(row, hits=hits,
                            email_dates={k: v.isoformat() for k, v in dates.items()}))
            with _LOCK:
                _STATE["done"] += 1
                _STATE["results"] = list(out)
        with _LOCK:
            _STATE["results"] = out
    except Exception as e:
        with _LOCK:
            _STATE["error"] = str(e)[:200]
    finally:
        diag.pop("_delegated", None)
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
