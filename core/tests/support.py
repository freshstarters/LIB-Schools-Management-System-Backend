"""Shared helpers for tenancy tests. Not collected as a test module."""

from datetime import datetime, timedelta, timezone

from cryptography.fernet import Fernet

from core.tenancy.claims import encode_access_token

FERNET_KEY = Fernet.generate_key().decode()


def access_token(app_scope, tenant_slug="", *, minutes=15):
    payload = {
        "app_scope": app_scope,
        "tenant_slug": tenant_slug,
        "exp": datetime.now(timezone.utc) + timedelta(minutes=minutes),
    }
    return encode_access_token(payload)


def bearer(app_scope, tenant_slug=""):
    return f"Bearer {access_token(app_scope, tenant_slug)}"


def bearer_for(user, role=""):
    slug = user.school.slug if user.school_id else ""
    payload = {
        "sub": str(user.pk),
        "app_scope": user.app_scope,
        "tenant_slug": slug,
        "role": role,
        "exp": datetime.now(timezone.utc) + timedelta(minutes=15),
    }
    return f"Bearer {encode_access_token(payload)}"
