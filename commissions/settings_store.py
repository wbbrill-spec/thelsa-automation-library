"""
settings_store.py — the commission settings that people can change on the page.

Rate, weights, target margin, the dollar rate for sales, the Sales/Admin split,
the people and their shares, and the booking budget per month. Stored whole
under the key "settings"; every change is kept in "settings_history" with who
and when, so a figure can always be traced to the settings it was made with.
Nothing stored = the values in engine.Settings and engine.BUDGET_2026.

Not on the screen, on purpose: how dollar sales are converted (D30: 16.50) and
which quotes count as bookings (D38: invoiced). Those are decisions, not
settings; they change in code.

Typed values are read strictly: percentages as "3" or "3.0" or "3 %", amounts
as "1,234.56" or "1234.56". Anything else is refused and nothing is saved.
"""
from __future__ import annotations

import dataclasses
import re

from . import engine as E

_PCT = re.compile(r"^-?\d+(\.\d+)?$")
_AMOUNT = re.compile(r"^(\d{1,3}(,\d{3})+|\d+)(\.\d{1,2})?$")
_RATE = re.compile(r"^\d+(\.\d{1,4})?$")
PCT_FIELDS = (("rate", "Commission rate", "Tasa de comisión"),
              ("w_invoicing", "Weight: invoicing", "Peso: facturación"),
              ("w_margin", "Weight: gross margin", "Peso: margen bruto"),
              ("w_discipline", "Weight: discipline", "Peso: disciplina"),
              ("target_margin", "Target gross margin", "Margen bruto objetivo"),
              ("sales_share", "Sales group share (Admin gets the rest)", "Parte del grupo Ventas (Adm recibe el resto)"))
MAX_PEOPLE = 10
MAX_MOVE_TYPES = 20
MOVE_KIND_LABELS = (("tms", "TMS booking: Corporate / Private", "Reserva TMS: Corporativo / Particular"),
                    ("agent", "Agent: never commission", "Agente: nunca comisiona"),
                    ("mixed", "Mixed: Finance's type decides", "Mixto: decide el tipo de Finanzas"),
                    ("unused", "Not in use", "Sin uso"))


class SettingsFormError(ValueError):
    def __init__(self, en: str, es: str):
        super().__init__(en)
        self.es = es


def budget_months(doc: dict | None = None) -> list:
    """2026 and 2027, plus any other month that has a budget stored."""
    months = [f"{y}-{m:02d}" for y in (2026, 2027) for m in range(1, 13)]
    extra = sorted(set((doc or {}).get("budgets", {})) - set(months))
    return months + extra


def defaults() -> E.Settings:
    return E.Settings(budgets=dict(E.BUDGET_2026))


def to_doc(s: E.Settings) -> dict:
    return {"rate": s.rate, "w_invoicing": s.w_invoicing, "w_margin": s.w_margin, "w_discipline": s.w_discipline,
            "target_margin": s.target_margin, "usd_mxn": s.usd_mxn, "sales_share": s.sales_share, "admin_share": s.admin_share,
            "sales_people": [[n, p] for n, p in s.sales_people], "admin_people": [[n, p] for n, p in s.admin_people],
            "budgets": dict(sorted(s.budgets.items())), "move_types": [[c, k] for c, k in s.move_types]}


def from_doc(doc: dict | None) -> E.Settings:
    """Settings from what is stored; defaults when nothing is. Raises
    engine.SettingsError when the stored values do not add up."""
    base = defaults()
    if not doc:
        return base
    share = float(doc.get("sales_share", base.sales_share))
    s = dataclasses.replace(
        base, rate=float(doc.get("rate", base.rate)), w_invoicing=float(doc.get("w_invoicing", base.w_invoicing)),
        w_margin=float(doc.get("w_margin", base.w_margin)), w_discipline=float(doc.get("w_discipline", base.w_discipline)),
        target_margin=float(doc.get("target_margin", base.target_margin)), usd_mxn=float(doc.get("usd_mxn", base.usd_mxn)),
        sales_share=share, admin_share=float(doc.get("admin_share", 1.0 - share)),
        sales_people=tuple((str(n), float(p)) for n, p in doc.get("sales_people", base.sales_people)),
        admin_people=tuple((str(n), float(p)) for n, p in doc.get("admin_people", base.admin_people)),
        budgets={str(k): float(v) for k, v in (doc.get("budgets") or base.budgets).items()},
        move_types=tuple((str(c), str(k)) for c, k in doc.get("move_types", base.move_types)))
    return s.check()


def _pct(raw: str, label: str, label_es: str) -> float:
    t = (raw or "").replace("%", "").replace(" ", "")
    if not _PCT.match(t):
        raise SettingsFormError(f"{label}: type a percentage, for example 3 or 30.5.",
                                f"{label_es}: escriba un porcentaje, por ejemplo 3 o 30.5.")
    return round(float(t) / 100, 6)


def _keep(typed: float, current, tol: float) -> float:
    """The page shows rounded values (2/3 as 66.6667 %); typed back unchanged,
    the exact value in use is kept, so saving without a change changes nothing."""
    return float(current) if current is not None and abs(typed - float(current)) < tol else typed


def parse(form, current: E.Settings | None = None) -> E.Settings:
    """Settings from the form on the page. Refuses anything not plainly typed,
    and anything that does not add up (engine.Settings.check). `current` = the
    settings in use (defaults when not given)."""
    cur = current or defaults()
    vals = {k: _keep(_pct(form.get(k), en, es), getattr(cur, k), 1e-6) for k, en, es in PCT_FIELDS}
    fx = (form.get("usd_mxn") or "").replace(" ", "")
    if not _RATE.match(fx) or float(fx) <= 0:
        raise SettingsFormError("Dollar rate for sales: type pesos per dollar, for example 16.50.",
                                "Tipo de cambio para ventas: escriba pesos por dólar, por ejemplo 16.50.")
    people = {}
    for group in ("sales", "admin"):
        rows = []
        for i in range(MAX_PEOPLE):
            name = (form.get(f"{group}_name_{i}") or "").strip()
            share = (form.get(f"{group}_share_{i}") or "").strip()
            if not name and not share:
                continue
            if not name:
                raise SettingsFormError("Every share needs a name.", "Cada parte necesita un nombre.")
            if len(name) > 80:
                raise SettingsFormError("A name is too long (80 characters at most).",
                                        "Un nombre es demasiado largo (80 caracteres como máximo).")
            was = dict(getattr(cur, f"{group}_people")).get(name)
            rows.append((name, _keep(_pct(share, f"Share of {name}", f"Parte de {name}"), was, 1e-6)))
        if not rows:
            raise SettingsFormError("Each group needs at least one person.", "Cada grupo necesita al menos una persona.")
        people[group] = tuple(rows)
    budgets = {}
    for m in budget_months({"budgets": {k[4:]: 0 for k in form.keys() if k.startswith("bud_")}}):
        raw = (form.get(f"bud_{m}") or "").replace("$", "").replace(" ", "")
        if not raw:
            continue
        if not re.match(r"^\d{4}-(0[1-9]|1[0-2])$", m) or not _AMOUNT.match(raw) or float(raw.replace(",", "")) <= 0:
            raise SettingsFormError(f"Budget {m}: type an amount in pesos, for example 3,554,054.80.",
                                    f"Presupuesto {m}: escriba un importe en pesos, por ejemplo 3,554,054.80.")
        budgets[m] = _keep(float(raw.replace(",", "")), cur.budgets.get(m), 0.005)
    for group, rows in people.items():          # equal thirds typed as 33.3333: the last share takes the 0.0001
        total = sum(p for _, p in rows)
        if 0 < abs(total - 1.0) < 1e-4:
            rows = rows[:-1] + ((rows[-1][0], rows[-1][1] + 1.0 - total),)
            people[group] = rows
    move_types, seen = [], set()
    for i in range(MAX_MOVE_TYPES):
        code = (form.get(f"mt_code_{i}") or "").strip().upper()
        kind = (form.get(f"mt_kind_{i}") or "").strip()
        if not code:
            continue
        if not re.fullmatch(r"[A-Z0-9]{2,6}", code):
            raise SettingsFormError(f"Move type {code[:10]}: use the Moveware code, 2 to 6 letters or digits.",
                                    f"Tipo de mudanza {code[:10]}: use el código de Moveware, 2 a 6 letras o dígitos.")
        if code in seen:
            raise SettingsFormError(f"Move type {code} appears twice.", f"El tipo de mudanza {code} aparece dos veces.")
        if kind not in E.MOVE_KINDS:
            raise SettingsFormError(f"Move type {code}: choose what it is.", f"Tipo de mudanza {code}: elija qué es.")
        seen.add(code)
        move_types.append((code, kind))
    s = dataclasses.replace(defaults(), move_types=tuple(move_types) if move_types else cur.move_types, usd_mxn=_keep(float(fx), cur.usd_mxn, 0.00005), sales_people=people["sales"], admin_people=people["admin"],
                            admin_share=cur.admin_share if vals["sales_share"] == cur.sales_share else 1.0 - vals["sales_share"],
                            budgets=budgets, **vals)
    try:
        return s.check()
    except E.SettingsError as e:
        raise SettingsFormError(f"Not saved: {e}.", f"No se guardó: {_ES.get(str(e), str(e))}.") from None


_ES = {
    "the three weights must add up to 100 %": "los tres pesos deben sumar 100 %",
    "the Sales and Admin shares must add up to 100 %": "las partes de Ventas y Adm deben sumar 100 %",
    "the Sales distribution must add up to 100 %": "la distribución de Ventas debe sumar 100 %",
    "the Admin distribution must add up to 100 %": "la distribución de Adm debe sumar 100 %",
    "the commission rate and the target margin are percentages, at most 100 %":
        "la tasa de comisión y el margen objetivo son porcentajes, como máximo 100 %",
    "rate, target margin and exchange rate must be above zero": "la tasa, el margen objetivo y el tipo de cambio deben ser mayores que cero",
    "a weight or a share cannot be negative": "un peso o una parte no puede ser negativo",
    "a person's share cannot be negative": "la parte de una persona no puede ser negativa",
    "the same name appears twice in the distribution": "el mismo nombre aparece dos veces en la distribución",
}


def changes(before: E.Settings, after: E.Settings) -> list:
    """What changed, in words, for the history (no amounts other than settings)."""
    a, b = to_doc(before), to_doc(after)
    out = [f"{k}: {a[k]} -> {b[k]}" for k in ("rate", "w_invoicing", "w_margin", "w_discipline", "target_margin",
                                               "usd_mxn", "sales_share") if abs(a[k] - b[k]) > 1e-9]
    for k in ("sales_people", "admin_people", "move_types"):
        if a[k] != b[k]:
            out.append(f"{k}: {a[k]} -> {b[k]}")
    for m in sorted(set(a["budgets"]) | set(b["budgets"])):
        x, y = a["budgets"].get(m), b["budgets"].get(m)
        if (x is None) != (y is None) or (x is not None and abs(x - y) >= 0.005):
            out.append(f"budget {m}: {a['budgets'].get(m)} -> {b['budgets'].get(m)}")
    return out
