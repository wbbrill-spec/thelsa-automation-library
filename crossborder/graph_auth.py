"""App-only Microsoft Graph token for the cross-border file readers.

The Remisiones and Plan de Viajes readers need `Files.Read.All`. That
permission lives on ONE app — `GRAPH_CLIENT_ID` (d32634d2…, "Thelsa Agent
Outreach Engine") — granted by César on 2026-09-15.

Until 2026-09-21 these readers borrowed `ms_graph._get_token()`, which prefers
the `MS_*` credentials whenever `MS_CLIENT_SECRET` is set. `MS_*` is shared
with the library's Microsoft web sign-in (app.py) and the personal assistant,
so any change there silently switched the file readers onto an app without
`Files.Read.All`, and `ms_graph` swallows the Azure error. The board reported
only "could not obtain Graph token" while every TIM shipment lost its volume
and the trucks panel went empty.

So this module:
  * tries the `GRAPH_*` triplet FIRST — the app that actually holds the
    permission — and only then `MS_*`, never mixing ids and secrets across
    apps;
  * reports which app it used and, on failure, Azure's own error code
    (AADSTS…) — never a secret — so the next failure is diagnosable from the
    dashboard instead of from a guess.
"""
from __future__ import annotations

import logging
import os
import time

log = logging.getLogger(__name__)

_TOKEN_URL = "https://login.microsoftonline.com/{tenant}/oauth2/v2.0/token"
_TIMEOUT = 20
_cache: dict[str, dict] = {}


def _triplets() -> list[tuple[str, str, str, str]]:
    """(label, tenant, client_id, secret) — complete triplets only, GRAPH_* first."""
    out = []
    for label, prefix in (("GRAPH_*", "GRAPH_"), ("MS_*", "MS_")):
        tenant = (os.environ.get(f"{prefix}TENANT_ID") or "").strip()
        client = (os.environ.get(f"{prefix}CLIENT_ID") or "").strip()
        secret = (os.environ.get(f"{prefix}CLIENT_SECRET") or "").strip()
        if tenant and client and secret:
            out.append((label, tenant, client, secret))
    return out


def _azure_error(resp) -> str:
    """AADSTS code + first line of the description. Contains no credential."""
    try:
        j = resp.json()
        desc = str(j.get("error_description") or "").split("\r\n")[0].split("\n")[0]
        return f"{j.get('error', '')}: {desc}"[:220]
    except Exception:  # noqa: BLE001
        return (getattr(resp, "text", "") or "")[:220]


def get_token() -> tuple[str | None, dict]:
    """(access token or None, info). `info` is safe to show on the dashboard."""
    import requests

    triplets = _triplets()
    if not triplets:
        return None, {"error": "no Graph credentials configured (need GRAPH_TENANT_ID, "
                               "GRAPH_CLIENT_ID and GRAPH_CLIENT_SECRET in Render)"}
    attempts = []
    for label, tenant, client, secret in triplets:
        cached = _cache.get(client)
        if cached and time.time() < cached["exp"] - 60:
            return cached["token"], {"creds": label, "app": client}
        try:
            r = requests.post(_TOKEN_URL.format(tenant=tenant), timeout=_TIMEOUT, data={
                "client_id": client, "client_secret": secret,
                "scope": "https://graph.microsoft.com/.default",
                "grant_type": "client_credentials"})
        except requests.RequestException as exc:
            attempts.append({"creds": label, "app": client, "error": f"{type(exc).__name__}: {exc}"[:220]})
            continue
        if r.status_code == 200:
            body = r.json()
            token = body.get("access_token")
            if token:
                _cache[client] = {"token": token, "exp": time.time() + int(body.get("expires_in", 3000))}
                info = {"creds": label, "app": client}
                if attempts:
                    info["fell_back_after"] = attempts
                return token, info
        attempts.append({"creds": label, "app": client, "http": r.status_code, "error": _azure_error(r)})
        log.warning("Graph token via %s (%s) refused: %s", label, client, attempts[-1]["error"])
    return None, {"error": "could not obtain Graph token", "attempts": attempts}
