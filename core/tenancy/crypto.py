"""Fernet encryption for tenant database passwords."""

from cryptography.fernet import Fernet, InvalidToken
from django.conf import settings
from django.core.exceptions import ImproperlyConfigured


def encrypt_password(password):
    return _fernet().encrypt(password.encode("utf-8")).decode("ascii")


def decrypt_password(ciphertext):
    try:
        return _fernet().decrypt(ciphertext.encode("ascii")).decode("utf-8")
    except InvalidToken as exc:
        raise ImproperlyConfigured(
            "TENANCY_CREDENTIAL_KEY cannot decrypt the stored tenant password."
        ) from exc


def _fernet():
    key = getattr(settings, "TENANCY_CREDENTIAL_KEY", "")
    if not key:
        raise ImproperlyConfigured(
            "TENANCY_CREDENTIAL_KEY is required when TENANCY_DB_MODE=per_tenant."
        )
    return Fernet(key.encode("ascii"))
