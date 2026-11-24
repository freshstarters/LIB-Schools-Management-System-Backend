"""Startup checks for PostgreSQL URLs and tenancy switches."""

from urllib.parse import urlparse

import environ
from django.core.exceptions import ImproperlyConfigured

POSTGRES_ENGINE = "django.db.backends.postgresql"
POSTGRES_SCHEMES = frozenset({"postgres", "postgresql"})
TENANCY_DB_MODES = frozenset({"shared", "per_tenant"})
TENANCY_ROUTING_MODES = frozenset({"subdomain", "header_path"})

env = environ.Env()


def postgres_database(url, *, label, required):
    """Return a Django database dict, or "" when an optional URL is unset.

    ``postgres`` and ``postgresql`` schemes are accepted. Every other scheme
    fails closed.
    """
    if url is None or not str(url).strip():
        if required:
            raise ImproperlyConfigured(
                f"{label} is required and must be a PostgreSQL URL."
            )
        return ""

    cleaned = str(url).strip()
    scheme = urlparse(cleaned).scheme
    if scheme not in POSTGRES_SCHEMES:
        raise ImproperlyConfigured(
            f"{label} must use a PostgreSQL URL."
        )

    config = env.db_url_config(cleaned)
    if config.get("ENGINE") != POSTGRES_ENGINE:
        raise ImproperlyConfigured(
            f"{label} must use a PostgreSQL URL."
        )
    return config


def tenancy_db_mode(raw):
    """Return ``shared`` when unset. An empty or unknown value fails closed."""
    if raw is None:
        return "shared"
    value = str(raw).strip()
    if value not in TENANCY_DB_MODES:
        raise ImproperlyConfigured(
            "TENANCY_DB_MODE must be one of: shared, per_tenant."
        )
    return value


def tenancy_routing_mode(raw, *, debug=False):
    """Return the routing mode.

    An unset value is ``header_path`` while debugging and ``subdomain``
    otherwise. An empty or unknown value fails closed.
    """
    if raw is None:
        return "header_path" if debug else "subdomain"
    value = str(raw).strip()
    if value not in TENANCY_ROUTING_MODES:
        raise ImproperlyConfigured(
            "TENANCY_ROUTING_MODE must be one of: subdomain, header_path."
        )
    return value
