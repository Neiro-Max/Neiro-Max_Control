from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
from dataclasses import dataclass


@dataclass(frozen=True)
class SessionResult:
    valid: bool
    username: str | None = None
    reason: str | None = None


def _b64e(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def _b64d(value: str) -> bytes:
    value += "=" * (-len(value) % 4)
    return base64.urlsafe_b64decode(value.encode())


def credentials_match(username: str, password: str, *, expected_user: str, expected_password: str) -> bool:
    return hmac.compare_digest(username, expected_user) and hmac.compare_digest(password, expected_password)


def issue_session(username: str, *, secret: str, ttl_seconds: int = 86400, now_ts: int | None = None) -> str:
    now_ts = int(time.time()) if now_ts is None else int(now_ts)
    payload = {"u": username, "iat": now_ts, "exp": now_ts + int(ttl_seconds)}
    body = _b64e(json.dumps(payload, separators=(",", ":"), sort_keys=True).encode())
    sig = hmac.new(secret.encode(), body.encode(), hashlib.sha256).hexdigest()
    return f"{body}.{sig}"


def verify_session(token: str, *, secret: str, now_ts: int | None = None) -> SessionResult:
    now_ts = int(time.time()) if now_ts is None else int(now_ts)
    try:
        body, sig = token.split(".", 1)
        expected = hmac.new(secret.encode(), body.encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(sig, expected):
            return SessionResult(False, reason="bad_signature")
        payload = json.loads(_b64d(body))
        if now_ts >= int(payload["exp"]):
            return SessionResult(False, reason="expired")
        return SessionResult(True, username=str(payload["u"]))
    except Exception:
        return SessionResult(False, reason="malformed")
