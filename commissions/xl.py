"""Reading Excel sheets without blowing up memory (4 Nov 2026).

Finance's monthly invoicing report (FACT UNI OCTUBRE.xlsx, 33 KB) has formatting
on all 1,048,576 rows of its sheet. Opened normally, openpyxl built a cell for
each one: 4.5 GB of memory and 52 seconds for 35 invoices — it took the server
down. Here sheets are opened read-only (streamed) and reading stops after a long
run of empty rows.
"""
import openpyxl

BLANK_STOP = 500


def open_workbook(src):
    return openpyxl.load_workbook(src, data_only=True, read_only=True)


def rows(ws, blank_stop: int = BLANK_STOP) -> list:
    """The sheet's rows as tuples, up to the last row with data (trailing blank
    rows dropped), stopping after `blank_stop` empty rows in a row."""
    out, blank = [], 0
    for r in ws.iter_rows(values_only=True):
        if r is None or all(c is None or (isinstance(c, str) and not c.strip()) for c in r):
            blank += 1
            if blank >= blank_stop:
                break
            out.append(tuple(r or ()))
            continue
        blank = 0
        out.append(tuple(r))
    while out and all(c is None or (isinstance(c, str) and not c.strip()) for c in out[-1]):
        out.pop()
    return out
