"""Startup validation for tenancy switches and the credential key."""

import binascii

from cryptography.fernet import Fernet
from django.core.exceptions import ImproperlyConfigured

from core.helpers.environment import tenancy_db_mode, tenancy_routing_mode


def tenancy_base_domain(raw, *, routing_mode):
    """Return the base domain. Subdomain mode requires one."""
    value = "" if raw is None else str(raw).strip().lower().rstrip(".")
    if routing_mode == "subdomain" and not value:
        raise ImproperlyConfigured(
            "TENANCY_BASE_DOMAIN is required when TENANCY_ROUTING_MODE=subdomain."
        )
    return value


def tenancy_credential_key(raw, *, db_mode):
    """Return a Fernet key. A blank key is allowed only in shared mode."""
    value = "" if raw is None else str(raw).strip()
    if not value:
        if db_mode == "per_tenant":
            raise ImproperlyConfigured(
                "TENANCY_CREDENTIAL_KEY is required when TENANCY_DB_MODE=per_tenant."
            )
        return ""
    try:
        Fernet(value.encode("ascii"))
    except (TypeError, ValueError, binascii.Error) as exc:
        raise ImproperlyConfigured(
            "TENANCY_CREDENTIAL_KEY must be a valid Fernet key."
        ) from exc
    return value


def load_tenancy(environ, *, debug):
    """Validate the tenancy environment and return the four settings."""
    db_mode = tenancy_db_mode(environ.get("TENANCY_DB_MODE"))
    routing_mode = tenancy_routing_mode(
        environ.get("TENANCY_ROUTING_MODE"),
        debug=debug,
    )
    base_domain = tenancy_base_domain(
        environ.get("TENANCY_BASE_DOMAIN"),
        routing_mode=routing_mode,
    )
    credential_key = tenancy_credential_key(
        environ.get("TENANCY_CREDENTIAL_KEY"),
        db_mode=db_mode,
    )
    return db_mode, routing_mode, base_domain, credential_key
