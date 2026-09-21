"""
Token vault — encrypts each user's mailbox credentials before they touch the DB.

The database only ever holds ciphertext. The key lives in the TOKEN_ENC_KEY env
var (Render: web service + worker), never in the repo or the DB.

Key rotation: TOKEN_ENC_KEY may hold several comma-separated keys. The FIRST key
encrypts new data; every key is tried for decryption. To rotate, put the new key
first, keep the old one after it, then run rotate() over all rows.

Generate a key:
    python3 -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
"""

import base64
import hashlib
import os

from cryptography.fernet import Fernet, InvalidToken, MultiFernet


class VaultError(RuntimeError):
    pass


def _keys() -> list:
    raw = os.environ.get("TOKEN_ENC_KEY", "").strip()
    keys = [k.strip() for k in raw.split(",") if k.strip()]
    if not keys:
        raise VaultError(
            "TOKEN_ENC_KEY is not set. Generate one with: python3 -c \"from "
            "cryptography.fernet import Fernet; print(Fernet.generate_key().decode())\""
        )
    return keys


def _as_fernet_key(k: str) -> bytes:
    """Accept a real Fernet key, or ANY long random string (e.g. Render's
    "Generate" value) — the latter is stretched to a Fernet key with SHA-256."""
    try:
        Fernet(k.encode())
        return k.encode()
    except (ValueError, TypeError):
        if len(k) < 24:
            raise VaultError("TOKEN_ENC_KEY is too short (use 32+ random characters)")
        return base64.urlsafe_b64encode(hashlib.sha256(k.encode()).digest())


def _fernet() -> MultiFernet:
    return MultiFernet([Fernet(_as_fernet_key(k)) for k in _keys()])


def is_configured() -> bool:
    try:
        _fernet()
        return True
    except VaultError:
        return False


def encrypt(plaintext: str) -> bytes:
    if plaintext is None:
        raise VaultError("Refusing to encrypt None")
    return _fernet().encrypt(plaintext.encode("utf-8"))


def decrypt(ciphertext: bytes) -> str:
    if not ciphertext:
        raise VaultError("Nothing to decrypt")
    try:
        return _fernet().decrypt(bytes(ciphertext)).decode("utf-8")
    except InvalidToken as exc:
        raise VaultError("Could not decrypt — wrong or rotated-out TOKEN_ENC_KEY") from exc


def rotate(ciphertext: bytes) -> bytes:
    """Re-encrypt under the current primary key (first key in TOKEN_ENC_KEY)."""
    return _fernet().rotate(bytes(ciphertext))
