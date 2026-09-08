"""
remisiones.py — reader for Gustavo's "Concentrado Remisiones 2026.xlsx".

The workbook is TIM's weekly billing / revenue-projection tracker: one sheet
per week ("4 al 10 ene", "26 abr al 2 may", …), rolled forward each Monday.
Each sheet stacks five blocks, each with a VENTA subtotal:

    REMISIONES PENDIENTES              — shipments to bill this month
    CERTIFICADOS DE MENAJE PENDIENTES  — certificate work pending
    ALMACENAJES MENSUALES              — monthly storage accounts
    Confirmados pendientes             — confirmed future shipments
    Entregas                           — deliveries (different columns)

Shipment blocks share the columns
    # ID | NOMBRE - USUARIO | AGENTE - CLIENTE | FECHA | VOLUMEN | CONCEPTO |
    VENTA | REFERENCIA | TIPO | ORIGEN | DESTINO | STATUS

This is the ONLY place volume, destination, sale value and origin live for
TIM shipments (ClickUp holds process progress only), so the dashboard merges
the latest week's rows into the ClickUp-derived Shipment records, matching on
REFERENCIA first and customer name second.

Two entry points:
    parse_workbook(path)        — openpyxl, the real .xlsx
    parse_text_extract(text)    — the Microsoft-365-connector text dump (tab-
                                  separated rows per "## Sheet:" header), used
                                  for tests and quick validation
Both feed parse_sheet_rows(), which does the actual work.
"""
from __future__ import annotations

import difflib
import re
from dataclasses import dataclass, field
from typing import Iterable, Optional

from .models import (
    CUFT_PER_M3, Hub, Shipment, hub_for_destination, norm_text,
)

BLOCKS = {
    "remisiones pendientes": "pending",
    "certificados de menaje pendientes": "certificates",
    "almacenajes mensuales": "storage",
    "confirmados pendientes": "confirmed",
    "entregas": "deliveries",
}
SHIPMENT_BLOCKS = ("pending", "confirmed")
COLS = ["id", "name", "agent", "date", "volume", "concept", "sale", "reference",
        "type", "origin", "destination", "status"]


@dataclass
class RemisionRow:
    week: str
    block: str                      # pending / confirmed / certificates / storage / deliveries
    name: str = ""
    agent: str = ""
    volume_text: str = ""
    sale: Optional[float] = None
    reference: str = ""
    type: str = ""
    origin: str = ""
    destination: str = ""
    status: str = ""
    concept: str = ""
    date: str = ""
    # normalized
    lift_vans: Optional[int] = None
    u_boxes: Optional[int] = None
    volume_m3: Optional[float] = None
    vehicles: Optional[int] = None
    flags: list[str] = field(default_factory=list)
    row_index: int = 0

    @property
    def hub(self) -> Hub:
        return hub_for_destination(self.destination)

    def to_dict(self) -> dict:
        d = self.__dict__.copy()
        d["hub"] = self.hub.value
        return d


# ── value normalizers ───────────────────────────────────────────────────────────

def parse_sale(v) -> Optional[float]:
    """'$2.478,00' (es-MX thousands '.', decimal ',') / '2189,43' / 2478 → 2478.0"""
    if v in (None, ""):
        return None
    if isinstance(v, (int, float)):
        return float(v)
    s = str(v).strip().replace("$", "").replace(" ", "")
    if not s or s in ("-", "–"):
        return None
    if "," in s and "." in s:
        if s.rfind(",") > s.rfind("."):          # 2.478,00 → European
            s = s.replace(".", "").replace(",", ".")
        else:                                    # 2,478.00 → US
            s = s.replace(",", "")
    elif "," in s:
        head, tail = s.rsplit(",", 1)
        s = head.replace(",", "") + ("." + tail if len(tail) <= 2 else tail)
    elif s.count(".") > 1:
        s = s.replace(".", "")
    try:
        return round(float(s), 2)
    except ValueError:
        return None


_VOL_RE = re.compile(
    r"(\d+\s*/\s*\d+|\d+(?:[.,]\d+)?)\s*"
    r"(m3|m\u00b3|cbm|cdm|cuft|cuf|cft|cu\.?\s*ft|ft3|uboxes?|u-?box(?:es)?|lvs?|liftvans?|lift\s*vans?|vans?|carros?|autos?)?",
    re.IGNORECASE)


def _num(tok: str) -> float:
    if "/" in tok:                                   # "1/2"
        a, b = tok.split("/")
        return float(a) / float(b)
    return float(tok.replace(",", "."))


def parse_volume(text) -> dict:
    """'1 van' / '3 uboxes' / '1/2 LVS' / '1,5 ubox' / '423cuft' / '626 CUF' /
    '160 m3' / '13,65cdm' / '450 o 520' / '250' / '1 carro'
    -> {lift_vans, u_boxes, volume_m3, vehicles}. Bare numbers are treated as
    cuft when > 60 (the sheet's dominant unit), else m\u00b3."""
    out = {"lift_vans": None, "u_boxes": None, "volume_m3": None, "vehicles": None}
    if text in (None, ""):
        return out
    s = str(text).lower().replace("\u00f3", "o")
    for num, unit in _VOL_RE.findall(s):
        n = _num(num)
        u = (unit or "").replace(" ", "").replace(".", "")
        if u.startswith("u"):
            out["u_boxes"] = (out["u_boxes"] or 0) + n
        elif u.startswith("lv") or u.startswith("lift") or u.startswith("van"):
            out["lift_vans"] = (out["lift_vans"] or 0) + n
        elif u.startswith("carro") or u.startswith("auto"):
            out["vehicles"] = (out["vehicles"] or 0) + int(n)
        elif u in ("m3", "m\u00b3", "cbm", "cdm"):
            out["volume_m3"] = round(n, 2)
        elif u.startswith("cu") or u in ("cft", "ft3"):
            out["volume_m3"] = round(n / CUFT_PER_M3, 2)
        elif not unit and out["volume_m3"] is None:
            # bare number: '450 o 520' keeps the first; assume cuft unless tiny
            out["volume_m3"] = round(n / CUFT_PER_M3, 2) if n > 60 else round(n, 2)
    for k in ("lift_vans", "u_boxes"):
        if out[k] is not None:
            out[k] = int(out[k]) if float(out[k]).is_integer() else round(out[k], 2)
    return out


STATUS_FLAG_RULES = [
    ("on hold", "on_hold"), ("hold", "on_hold"), ("detenido", "on_hold"),
    ("pendiente de pago", "payment_pending"), ("saldo pendiente", "payment_pending"),
    ("por pagar", "payment_pending"), ("no ha pagado", "payment_pending"),
    ("esperando pago", "payment_pending"),
    ("almacenaje", "in_storage"), ("storage", "in_storage"),
    ("certificado", "certificate_pending"), (" cm", "certificate_pending"),
    ("visa", "visa_pending"),
    ("espera de documentos", "docs_pending"), ("llegan documentos", "docs_pending"),
    ("no ha estado contestando", "unresponsive"), ("sin dar respuesta", "unresponsive"),
    ("no ha contestado", "unresponsive"),
    ("pendiente greenlight", "awaiting_green_light"), ("pending booking", "awaiting_booking"),
    ("no bookea", "awaiting_booking"),
]


def status_flags(status: str) -> list[str]:
    t = norm_text(status)
    if not t:
        return []
    flags = []
    for needle, flag in STATUS_FLAG_RULES:
        n = needle.strip()
        if (len(n) <= 3 and f" {n} " in f" {t} ") or (len(n) > 3 and n in t):
            if flag not in flags:
                flags.append(flag)
    return flags


def _dedupe(seq: Iterable[str]) -> list[str]:
    seen, out = set(), []
    for x in seq:
        if x not in seen:
            seen.add(x)
            out.append(x)
    return out


# ── sheet parsing ───────────────────────────────────────────────────────────────

def parse_sheet_rows(week: str, rows: list[list]) -> list[RemisionRow]:
    """rows: the sheet as a list of lists (cells B..M → index 0..11; any leading
    column A is tolerated when the block titles sit in index 1)."""
    out: list[RemisionRow] = []
    block: Optional[str] = None
    for i, raw in enumerate(rows):
        cells = [("" if c is None else str(c).strip()) for c in raw]
        if not any(cells):
            continue
        joined = norm_text(" ".join(cells))
        # block header?
        hit = next((b for k, b in BLOCKS.items() if joined.startswith(k) or joined == k), None)
        if hit is None:
            for k, b in BLOCKS.items():
                if any(norm_text(c) == k for c in cells[:3]):
                    hit = b
                    break
        if hit:
            block = hit
            continue
        # column header row
        if any(norm_text(c) in ("nombre usuario", "id") for c in cells[:2]) and "agente" in joined:
            continue
        if block is None:
            continue
        # align: find the name column — first non-empty among index 0..1 that is not a pure number
        cells = (cells + [""] * 12)[:12]
        if block in SHIPMENT_BLOCKS + ("certificates", "storage"):
            rec = dict(zip(COLS, cells))
            name = rec["name"] or (rec["id"] if not re.fullmatch(r"[\d.,$ ]+", rec["id"] or "") else "")
            sale = parse_sale(rec["sale"])
            # subtotal / TOTAL rows: no name, only a sale value
            if not name or norm_text(name) in ("total", "venta"):
                continue
            if norm_text(name) in ("a4s",):          # sheet artifact row
                continue
            r = RemisionRow(week=week, block=block, name=name, agent=rec["agent"],
                            volume_text=rec["volume"], sale=sale, reference=rec["reference"].strip(),
                            type=rec["type"], origin=rec["origin"], destination=rec["destination"],
                            status=rec["status"], concept=rec["concept"], date=rec["date"],
                            row_index=i)
            r.__dict__.update(parse_volume(r.volume_text))
            r.flags = status_flags(r.status)
            out.append(r)
        elif block == "deliveries":
            # NOMBRE - USUARIO | AGENTE - CLIENTE | Destino | | FECHA ENTREGA | VOLUMEN | COMENTARIOS | | INVOICE ENVIADO
            name = cells[1] or cells[0]
            if not name or norm_text(name) in ("nombre usuario",):
                continue
            r = RemisionRow(week=week, block=block, name=name, agent=cells[2], destination=cells[3],
                            date=cells[5], volume_text=cells[6], status=cells[7], concept=cells[9],
                            row_index=i)
            r.__dict__.update(parse_volume(r.volume_text))
            out.append(r)
    return out


def parse_text_extract(text: str) -> dict[str, list[RemisionRow]]:
    """The Microsoft 365 connector's dump: '## Sheet: <name> — …' headers, then
    tab-separated rows, '[N empty rows]' markers, and a trailing 'Formulas:' list."""
    sheets: dict[str, list[RemisionRow]] = {}
    name, rows = None, []
    for line in text.splitlines():
        m = re.match(r"## Sheet: (.+?) — ", line)
        if m:
            if name:
                sheets[name] = parse_sheet_rows(name, rows)
            name, rows = m.group(1).strip(), []
            continue
        if name is None or line.startswith("Formulas:") or re.match(r"^[A-Z]+\d+: =", line) \
                or line.startswith("[") and line.endswith("]"):
            continue
        rows.append(line.split("\t"))
    if name:
        sheets[name] = parse_sheet_rows(name, rows)
    return sheets


def parse_csv(path: str, week: str, delimiter: str = ";", encoding: str = "cp1252") -> list[RemisionRow]:
    """One weekly sheet exported from Excel as CSV (column A included)."""
    import csv
    with open(path, encoding=encoding, newline="") as fh:
        rows = list(csv.reader(fh, delimiter=delimiter))
    return parse_sheet_rows(week, rows)


def parse_workbook(path: str) -> dict[str, list[RemisionRow]]:
    import openpyxl
    wb = openpyxl.load_workbook(path, data_only=True, read_only=True)
    sheets: dict[str, list[RemisionRow]] = {}
    for ws in wb.worksheets:
        rows = []
        for row in ws.iter_rows(min_col=2, max_col=13, values_only=True):   # B..M
            rows.append(list(row))
        sheets[ws.title] = parse_sheet_rows(ws.title, rows)
    return sheets


def latest_week(sheets: dict[str, list[RemisionRow]]) -> str:
    """Sheets are kept in chronological order in the workbook; the last one is
    the current week."""
    return list(sheets.keys())[-1]


# ── matching to ClickUp shipments ───────────────────────────────────────────────

def _ref_tokens(ref: str) -> set[str]:
    return {t for t in re.split(r"[\s/|,;]+", (ref or "").upper()) if len(t) >= 4 and re.search(r"\d", t)}


def _name_key(name: str) -> str:
    return " ".join(sorted(norm_text(name).split()))


def match_rows_to_shipments(rows: list[RemisionRow], shipments: list[Shipment]) -> dict:
    """Return {shipment.id: RemisionRow} for the best match of each shipment,
    plus diagnostics. Reference tokens win; otherwise fuzzy customer name.
    Only shipment blocks (pending / confirmed) are matched — storage and
    certificate rows never carry shipment facts. A row is given to at most one
    shipment: when two ClickUp lists share a reference (it happens — e.g. two
    U-Haul lists both tagged 126624) the closer customer name keeps it."""
    cand = [r for r in rows if r.block in SHIPMENT_BLOCKS]
    by_token: dict[str, list[RemisionRow]] = {}
    for r in cand:
        for t in _ref_tokens(r.reference):
            by_token.setdefault(t, []).append(r)

    def name_score(s: Shipment, r: RemisionRow) -> float:
        return difflib.SequenceMatcher(None, _name_key(s.customer_name), _name_key(r.name)).ratio()

    # 1) reference candidates — resolve collisions by name similarity
    claims: dict[int, list[tuple[float, Shipment]]] = {}
    for s in shipments:
        for t in _ref_tokens(s.reference_number):
            for r in by_token.get(t, []):
                claims.setdefault(id(r), []).append((name_score(s, r), s))
                break
            else:
                continue
            break
    matched: dict[str, RemisionRow] = {}
    how: dict[str, str] = {}
    used: set[int] = set()
    row_by_id = {id(r): r for r in cand}
    for rid, lst in claims.items():
        lst.sort(key=lambda x: -x[0])
        score, s = lst[0]
        matched[s.id] = row_by_id[rid]
        how[s.id] = "reference" if len(lst) == 1 else f"reference (collision, name {score:.2f})"
        used.add(rid)
    # 2) fuzzy name for the rest
    for s in shipments:
        if s.id in matched or not s.customer_name:
            continue
        best, score = None, 0.0
        for r in cand:
            if id(r) in used:
                continue
            sc = name_score(s, r)
            if sc > score:
                best, score = r, sc
        if best is not None and score >= 0.82:
            matched[s.id] = best
            how[s.id] = f"name:{score:.2f}"
            used.add(id(best))
    diag = {
        "rows": len(rows), "shipment_rows": len(cand), "shipments": len(shipments),
        "matched": len(matched),
        "by_reference": sum(1 for v in how.values() if v.startswith("reference")),
        "by_name": sum(1 for v in how.values() if v.startswith("name")),
        "collisions": [f"{s}" for s, v in how.items() if "collision" in v],
        "unmatched_shipments": [f"{s.customer_name} ({s.reference_number})" for s in shipments if s.id not in matched],
        "unmatched_rows": [f"{r.name} ({r.reference})" for r in cand if id(r) not in used],
    }
    return {"matches": matched, "how": how, "diag": diag}


def enrich_shipment(s: Shipment, r: RemisionRow) -> Shipment:
    """Copy the spreadsheet facts onto a ClickUp-derived Shipment (in place)."""
    if r.destination:
        s.destination = r.destination
        s.destination_hub = hub_for_destination(r.destination)
    if r.origin:
        s.origin = r.origin
    if r.volume_m3 is not None:
        s.volume_m3 = r.volume_m3
    if r.lift_vans is not None:
        s.lift_vans = r.lift_vans
    if r.u_boxes is not None:
        s.u_boxes = r.u_boxes
    if not s.agent and r.agent:
        s.agent = r.agent
    if not s.reference_number and r.reference:
        s.reference_number = r.reference
    s.status_flags = _dedupe(list(s.status_flags) + list(r.flags))
    s.extra.update({"remisiones_block": r.block, "remisiones_status": r.status,
                    "sale_value": r.sale, "remisiones_week": r.week, "type": r.type,
                    "vehicles": r.vehicles, "volume_text": r.volume_text})
    return s


# ── Fetching the workbook ───────────────────────────────────────────────────────
# Production path: Microsoft Graph, app-only (the same Entra app the library
# already uses for mailboxes). Needs the Files.Read.All (or Sites.Read.All)
# APPLICATION permission on that app — an IT (Cesar) action. Until then the
# fetch reports the Graph error in the diagnostics and the dashboard runs on
# ClickUp data alone.
#
# Env vars:
#   REMISIONES_DRIVE_ID / REMISIONES_ITEM_ID   the OneDrive drive + item ids of
#       "Concentrado Remisiones 2026.xlsx" (defaults = the ids discovered 2026-09-08)
#   REMISIONES_XLSX_PATH   optional local path (dev / manual drop-in)

DEFAULT_DRIVE_ID = "b!74-aE5c-P0CDf0XLvH1MU-RaA0MdKdRCjcGRCinnayjBUKROx47gSJkA4ttRpkEe"
DEFAULT_ITEM_ID = "01WXKQBWNCSL2ALZIECVGJ7FB2EJM7GZDE"


def fetch_workbook_bytes() -> tuple[Optional[bytes], dict]:
    """Return (xlsx bytes, info). Tries a local path first, then Graph."""
    import os
    path = os.environ.get("REMISIONES_XLSX_PATH", "").strip()
    if path and os.path.exists(path):
        with open(path, "rb") as fh:
            return fh.read(), {"source": "file", "path": path}
    try:
        import ms_graph  # library module: app-only Graph token
    except Exception as exc:  # noqa: BLE001
        return None, {"source": "graph", "error": f"ms_graph unavailable: {exc}"}
    if not ms_graph.have_ms_creds():
        return None, {"source": "graph", "error": "no Graph credentials configured"}
    token = ms_graph._get_token()
    if not token:
        return None, {"source": "graph", "error": "could not obtain Graph token"}
    import requests
    drive = os.environ.get("REMISIONES_DRIVE_ID", DEFAULT_DRIVE_ID)
    item = os.environ.get("REMISIONES_ITEM_ID", DEFAULT_ITEM_ID)
    url = f"https://graph.microsoft.com/v1.0/drives/{drive}/items/{item}/content"
    try:
        r = requests.get(url, headers={"Authorization": f"Bearer {token}"}, timeout=60,
                         allow_redirects=True)
    except requests.RequestException as exc:
        return None, {"source": "graph", "error": f"request failed: {exc}"}
    if r.status_code != 200:
        return None, {"source": "graph", "status": r.status_code,
                      "error": r.text[:300] or "download refused (app needs Files.Read.All)"}
    return r.content, {"source": "graph", "bytes": len(r.content)}


def load_latest_rows() -> tuple[list[RemisionRow], dict]:
    """Latest week's rows from wherever the workbook can be fetched."""
    import io
    data, info = fetch_workbook_bytes()
    if not data:
        return [], info
    import openpyxl
    try:
        wb = openpyxl.load_workbook(io.BytesIO(data), data_only=True, read_only=True)
    except Exception as exc:  # noqa: BLE001
        info["error"] = f"workbook unreadable: {exc}"
        return [], info
    sheets: dict[str, list[RemisionRow]] = {}
    for ws in wb.worksheets:
        rows = [list(r) for r in ws.iter_rows(min_col=2, max_col=13, values_only=True)]
        sheets[ws.title] = parse_sheet_rows(ws.title, rows)
    if not sheets:
        info["error"] = "no worksheets"
        return [], info
    week = latest_week(sheets)
    info.update(week=week, weeks=len(sheets), rows=len(sheets[week]))
    return sheets[week], info
