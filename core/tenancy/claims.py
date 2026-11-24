"""HS256 access-token checks.

Login issues these tokens through encode_access_token. ``sub`` and ``role``
are optional so older tokens still verify.
"""

from dataclasses import dataclass

import jwt
from django.conf import settings
from jwt import InvalidTokenError

ALGORITHM = "HS256"
SCOPES = frozenset({"core", "demo"})


class InvalidAccessToken(Exception):
    """The bearer is present and cannot be verified."""


@dataclass(frozen=True)
class AccessClaims:
    app_scope: str
    tenant_slug: str
    sub: str = ""
    role: str = ""


def encode_access_token(payload):
    return jwt.encode(payload, settings.SECRET_KEY, algorithm=ALGORITHM)


def read_bearer(request):
    """Return claims, or None when the Authorization header is absent."""
    header = request.headers.get("Authorization", "")
    if not header:
        return None
    scheme, _, token = header.partition(" ")
    if scheme.lower() != "bearer" or not token.strip():
        raise InvalidAccessToken
    return decode_access_token(token.strip())


def decode_access_token(token):
    try:
        payload = jwt.decode(
            token,
            settings.SECRET_KEY,
            algorithms=[ALGORITHM],
            options={"require": ["exp"]},
        )
    except InvalidTokenError as exc:
        raise InvalidAccessToken from exc
    scope = payload.get("app_scope")
    slug = payload.get("tenant_slug", "")
    subject = payload.get("sub", "")
    role = payload.get("role", "")
    if (
        scope not in SCOPES
        or not isinstance(slug, str)
        or not isinstance(subject, str)
        or not isinstance(role, str)
    ):
        raise InvalidAccessToken
    return AccessClaims(
        app_scope=scope,
        tenant_slug=slug,
        sub=subject,
        role=role,
    )
