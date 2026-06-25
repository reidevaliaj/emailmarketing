"""Signed tokens for unsubscribe links (Section 9).

Unsubscribe URLs embed a signed token of the recipient's email so the endpoint
can suppress without auth and without exposing a guessable id. We use a
non-expiring URL-safe signature (unsubscribe links must keep working forever).

NOTE: keep ``UNSUBSCRIBE_SECRET`` stable across deploys — rotating it
invalidates every previously-sent unsubscribe link.
"""

from __future__ import annotations

from itsdangerous import BadSignature, URLSafeSerializer

from app.config import settings
from app.services.normalize import normalize_email

_SALT = "unsubscribe-v1"


def _serializer() -> URLSafeSerializer:
    return URLSafeSerializer(settings.unsubscribe_secret, salt=_SALT)


def make_unsubscribe_token(email: str) -> str:
    return _serializer().dumps(normalize_email(email) or email)


def read_unsubscribe_token(token: str) -> str | None:
    try:
        value = _serializer().loads(token)
    except BadSignature:
        return None
    return normalize_email(value) if isinstance(value, str) else None


def unsubscribe_url(email: str) -> str:
    return f"{settings.app_base_url_clean}/u/{make_unsubscribe_token(email)}"
