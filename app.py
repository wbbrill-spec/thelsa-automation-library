"""
Thelsa Automation Library — Dashboard
Serves the library landing page and authenticates team members via Google OAuth.
Each automation tool runs as its own independent Render service; this app just
links out to them.

Local dev : python3 app.py   →  http://localhost:5000
Production: deployed to Render — always-on, no ngrok required.

Required environment variables (set in Render dashboard):
  FLASK_SECRET_KEY       — random secret, use Render's "generate" button
  OAUTH_REDIRECT_URI     — https://<your-service>.onrender.com/auth/callback
  GOOGLE_CREDENTIALS_B64 — base64-encoded contents of web_credentials.json
  RATE_ENGINE_URL        — URL of the OA-DA Rate Engine Render service
  LEAD_GEN_URL           — URL of the TMS Lead Gen Engine Render service
"""

import base64
import functools
import hashlib
import json
import logging
import os
import re
import secrets
from datetime import timedelta
from pathlib import Path

from flask import Flask, redirect, request, session, url_for
from werkzeug.middleware.proxy_fix import ProxyFix

# ── App setup ──────────────────────────────────────────────────────────────────
app = Flask(__name__, static_folder="static", static_url_path="/static")
app.secret_key = os.environ.get("FLASK_SECRET_KEY", "thelsa-lib-change-me-in-production")
app.permanent_session_lifetime = timedelta(days=7)

# Trust reverse-proxy headers from Render (and ngrok for local testing)
app.wsgi_app = ProxyFix(app.wsgi_app, x_proto=1, x_host=1)

# Allow OAuth over plain HTTP only in local dev (Render always uses HTTPS)
if os.environ.get("FLASK_ENV") == "development":
    os.environ.setdefault("OAUTHLIB_INSECURE_TRANSPORT", "1")

BASE      = Path(__file__).resolve().parent
TOKEN_DIR = BASE / "data" / "tokens"
TOKEN_DIR.mkdir(parents=True, exist_ok=True)

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger(__name__)

# ── Sub-app Render URLs ────────────────────────────────────────────────────────
# Override with RATE_ENGINE_URL / LEAD_GEN_URL env vars in Render dashboard.
RENDER_URLS = {
    "rate-engine": os.environ.get(
        "RATE_ENGINE_URL", "https://thelsa-rate-engine.onrender.com"
    ),
    "lead-gen": os.environ.get(
        "LEAD_GEN_URL", "https://thelsa-lead-gen.onrender.com"
    ),
}

# ── Authorised team members ────────────────────────────────────────────────────
ALLOWED_EMAILS = {
    "wbbrill@gmail.com",
    "vanegasmartha@gmail.com",
    "guillermo.monroyh@gmail.com",
    "mgonzale1371@gmail.com",
    "a.silveyra88@gmail.com",
    "guga.gekko@gmail.com",
}

GMAIL_SCOPES = [
    "openid",
    "https://www.googleapis.com/auth/userinfo.email",
    "https://www.googleapis.com/auth/userinfo.profile",
    "https://www.googleapis.com/auth/gmail.compose",
]


# ── OAuth helpers ──────────────────────────────────────────────────────────────

def _load_client_config() -> dict:
    """Load OAuth client config.

    Priority:
      1. GOOGLE_CREDENTIALS_B64 env var (base64-encoded JSON) — used on Render
         so the secret never touches the repo.
      2. web_credentials.json in the project root — used in local dev.
    """
    creds_b64 = os.environ.get("GOOGLE_CREDENTIALS_B64", "").strip()
    if creds_b64:
        return json.loads(base64.b64decode(creds_b64).decode())
    path = BASE / "web_credentials.json"
    if path.exists():
        return json.loads(path.read_text())
    raise RuntimeError(
        "No Google OAuth credentials found.\n"
        "  Local dev : place web_credentials.json in the project root.\n"
        "  Render    : set GOOGLE_CREDENTIALS_B64 to base64-encoded JSON.\n"
        "  Encode    : python3 -c \"import base64,pathlib; "
        "print(base64.b64encode(pathlib.Path('web_credentials.json').read_bytes()).decode())\""
    )


def _callback_uri() -> str:
    """Return the OAuth callback URI.

    On Render, set OAUTH_REDIRECT_URI to
    https://<your-service>.onrender.com/auth/callback so the value is
    stable across deploys and matches what's registered in GCP.
    """
    override = os.environ.get("OAUTH_REDIRECT_URI", "").strip()
    if override:
        return override
    proto = request.headers.get("X-Forwarded-Proto", "http")
    host  = request.headers.get("X-Forwarded-Host", request.host)
    return f"{proto}://{host}/auth/callback"


def _make_flow(redirect_uri: str):
    from google_auth_oauthlib.flow import Flow
    return Flow.from_client_config(
        _load_client_config(), scopes=GMAIL_SCOPES, redirect_uri=redirect_uri
    )


def _token_path(email: str) -> Path:
    safe = re.sub(r"[^a-z0-9]", "_", email.lower())
    return TOKEN_DIR / f"{safe}.json"


def _save_token(email: str, credentials) -> None:
    data = {
        "token":         credentials.token,
        "refresh_token": credentials.refresh_token,
        "token_uri":     credentials.token_uri,
        "client_id":     credentials.client_id,
        "client_secret": credentials.client_secret,
        "scopes":        list(credentials.scopes or []),
    }
    _token_path(email).write_text(json.dumps(data))


def login_required(f):
    @functools.wraps(f)
    def decorated(*args, **kwargs):
        if not session.get("user_email"):
            return redirect(url_for("login", next=request.url))
        return f(*args, **kwargs)
    return decorated


# ── Auth routes ────────────────────────────────────────────────────────────────

@app.route("/login")
def login():
    session["oauth_next"] = request.args.get("next", url_for("index"))
    cb   = _callback_uri()
    flow = _make_flow(cb)
    code_verifier  = secrets.token_urlsafe(64)
    code_challenge = base64.urlsafe_b64encode(
        hashlib.sha256(code_verifier.encode()).digest()
    ).rstrip(b"=").decode()
    auth_url, state = flow.authorization_url(
        access_type="offline",
        prompt="consent",
        code_challenge=code_challenge,
        code_challenge_method="S256",
    )
    session["oauth_state"]         = state
    session["oauth_code_verifier"] = code_verifier
    return redirect(auth_url)


@app.route("/auth/callback")
def auth_callback():
    flow = _make_flow(_callback_uri())
    try:
        flow.fetch_token(
            authorization_response=request.url,
            code_verifier=session.get("oauth_code_verifier", ""),
        )
    except Exception as exc:
        return f"OAuth token exchange failed: {exc}", 400

    creds = flow.credentials
    try:
        from googleapiclient.discovery import build
        svc  = build("oauth2", "v2", credentials=creds, cache_discovery=False)
        info = svc.userinfo().get().execute()
    except Exception as exc:
        return f"Failed to fetch user info: {exc}", 400

    email = (info.get("email") or "").lower()
    if email not in ALLOWED_EMAILS:
        return (
            f"<h2>Access Denied</h2><p>{email} is not authorised to use this tool.</p>"
            "<p>Contact Bill to request access.</p>",
            403,
        )

    session["user_email"] = email
    session["user_name"]  = info.get("name", email)
    session.permanent     = True
    _save_token(email, creds)
    logger.info(f"Login: {email}")
    return redirect(session.pop("oauth_next", url_for("index")))


@app.route("/logout")
def logout():
    logger.info(f"Logout: {session.get('user_email')}")
    session.clear()
    return redirect(url_for("login"))


# ── Main routes ────────────────────────────────────────────────────────────────

@app.route("/")
@login_required
def index():
    html  = (BASE / "index.html").read_text()
    name  = session.get("user_name", "")
    email = session.get("user_email", "")
    html  = html.replace("{{USER_NAME}}", name).replace("{{USER_EMAIL}}", email)
    return html


@app.route("/launch/<key>")
@login_required
def launch(key):
    """Redirect to the sub-app's Render URL. The sub-app handles its own auth."""
    url = RENDER_URLS.get(key)
    if not url:
        return f"Unknown automation: {key}", 404
    return redirect(url)


@app.route("/run/<key>")
@login_required
def run_now(key):
    """'Run Now' button — sends user to the sub-app dashboard (same as launch)."""
    return launch(key)


# ── Entry point ────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    # Allow plain HTTP only for local dev
    os.environ.setdefault("OAUTHLIB_INSECURE_TRANSPORT", "1")
    print(f"\n  Thelsa Automation Library — http://localhost:{port}\n")
    app.run(host="0.0.0.0", port=port, debug=False)
