"""
Two filters between the WhatsApp chat list and the dashboard.

A. Watch list (users.wa_watch) — free, deterministic, runs first.
   Empty means "every unread chat", which is how it behaved before this existed.
   Otherwise a chat is kept only if one of the entries appears in its name
   (case- and accent-insensitive), so "thelsa" matches "Thelsa Operaciones".

B. Work/personal classification — only for chats that survived A, and only when
   ANTHROPIC_API_KEY is set. Claude sees the chat name and the one-line preview
   WhatsApp already renders in the list; it never sees message history, because
   the extension never reads any. It answers work/personal and, for work chats,
   writes one short line saying what is being asked for.

Two rules keep this safe to run every minute:
  * Conservative — a chat is dropped only when the model says personal outright.
    No key, an API error, malformed JSON, a chat it didn't answer for: all keep
    the chat. Hiding real work is a worse failure than showing a personal chat.
  * Cached — the extension syncs once a minute and the chat list rarely changes,
    so a verdict is reused for CACHE_TTL_S (default 6h) keyed by the exact name
    and preview. Only genuinely new or changed chats cost an API call.
"""

import hashlib
import json
import logging
import os
import threading
import time
import unicodedata

import requests

log = logging.getLogger("assistant.wa_triage")

MODEL = os.environ.get("ASSISTANT_DRAFT_MODEL", "claude-sonnet-4-5")
CACHE_TTL_S = int(os.environ.get("ASSISTANT_WA_CACHE_S", str(6 * 3600)))
MAX_PER_CALL = 60

_CACHE = {}          # key -> (verdict dict, stored_at)
_LOCK = threading.Lock()


def enabled() -> bool:
    return bool(os.environ.get("ANTHROPIC_API_KEY"))


def _fold(s) -> str:
    """Lowercase, strip accents — so 'Logística' matches 'logistica'."""
    s = unicodedata.normalize("NFKD", str(s or "")).encode("ascii", "ignore").decode()
    return " ".join(s.lower().split())


def parse_watch(value) -> list:
    """'Thelsa, TIM ; Coordinación' -> ['thelsa', 'tim', 'coordinacion']."""
    if not value:
        return []
    parts = value.replace(";", ",").replace("\n", ",").split(",") \
        if isinstance(value, str) else list(value)
    out = []
    for p in parts:
        t = _fold(p)
        if t and t not in out:
            out.append(t)
    return out


def passes_watch(name, watch) -> bool:
    """No watch list = keep everything. Otherwise keep only matching chats."""
    terms = parse_watch(watch)
    if not terms:
        return True
    folded = _fold(name)
    return any(t in folded for t in terms)


def key(name, preview) -> str:
    return hashlib.sha256((str(name) + "\u0000" + str(preview or "")).encode()).hexdigest()[:32]


def _cached(k):
    with _LOCK:
        hit = _CACHE.get(k)
        if hit and time.time() - hit[1] < CACHE_TTL_S:
            return hit[0]
    return None


def _store(k, verdict):
    with _LOCK:
        if len(_CACHE) > 4000:
            _CACHE.clear()
        _CACHE[k] = (verdict, time.time())


PROMPT = (
    "You are triaging the WhatsApp chat list of someone who works at Thelsa, an "
    "international and domestic moving company in Mexico. For each chat you get only "
    "the chat name and the single-line preview of the latest message — never the "
    "conversation.\n\n"
    "For each numbered chat decide:\n"
    '  "work": true if it plausibly relates to their job — customers, agents, suppliers, '
    "colleagues, move files, shipments, documents, quotes, invoices, schedules. "
    "false ONLY when it is clearly personal (family, friends, social plans) or pure "
    "spam/marketing. When you are unsure, answer true.\n"
    '  "action": for work chats, one short line (max 90 characters, same language as the '
    "preview) saying what is being asked of them. If the preview is too thin to tell, "
    'use "". Never invent specifics that are not in the preview.\n\n'
    'Reply with ONLY a JSON array, one object per chat, in order: '
    '[{"n":1,"work":true,"action":"..."}]'
)


def _ask(batch) -> dict:
    lines = []
    for i, c in enumerate(batch, 1):
        lines.append(f'{i}. name: {c["name"]!r} | preview: {(c.get("preview") or "")[:300]!r}')
    r = requests.post(
        "https://api.anthropic.com/v1/messages",
        headers={"x-api-key": os.environ["ANTHROPIC_API_KEY"],
                 "anthropic-version": "2023-06-01", "content-type": "application/json"},
        json={"model": MODEL, "max_tokens": 2000,
              "messages": [{"role": "user", "content": PROMPT + "\n\n" + "\n".join(lines)}]},
        timeout=60)
    r.raise_for_status()
    parts = r.json().get("content") or []
    text = "".join(p.get("text", "") for p in parts if p.get("type") == "text").strip()
    start, end = text.find("["), text.rfind("]")
    if start < 0 or end <= start:
        raise ValueError("no JSON array in response")
    out = {}
    for row in json.loads(text[start:end + 1]):
        n = int(row.get("n") or 0)
        if 1 <= n <= len(batch):
            c = batch[n - 1]
            out[key(c["name"], c.get("preview"))] = {
                "work": row.get("work") is not False,          # anything but explicit false = work
                "action": str(row.get("action") or "")[:200].strip()}
    return out


def classify(chats) -> dict:
    """{cache key: {"work": bool, "action": str}} for the chats it could judge.
    A chat missing from the result was not judged and must be kept."""
    if not chats or not enabled():
        return {}
    verdicts, todo = {}, []
    for c in chats:
        k = key(c.get("name"), c.get("preview"))
        hit = _cached(k)
        if hit is not None:
            verdicts[k] = hit
        elif not any(t.get("name") == c.get("name") and t.get("preview") == c.get("preview")
                     for t in todo):
            todo.append(c)
    for i in range(0, len(todo), MAX_PER_CALL):
        batch = todo[i:i + MAX_PER_CALL]
        try:
            fresh = _ask(batch)
        except Exception as exc:                  # keep every chat if triage fails
            log.warning("WhatsApp triage failed (%s chats kept unfiltered): %s", len(batch), exc)
            continue
        for k, v in fresh.items():
            _store(k, v)
            verdicts[k] = v
    return verdicts


def reset_cache_for_tests():
    with _LOCK:
        _CACHE.clear()
