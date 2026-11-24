"""Create a tenant database, migrate it, and seed the Liberian catalog."""

import copy
import hashlib
import logging
import secrets
from urllib.parse import urlparse

import psycopg
from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.core.management import call_command
from django.db import connections, transaction
from psycopg import sql

from core.tenancy.context import tenant_context
from core.tenancy.crypto import decrypt_password, encrypt_password

logger = logging.getLogger(__name__)

DIVISIONS = (
    ("early-childhood", "Early Childhood"),
    ("elementary", "Elementary"),
    ("junior-high", "Junior High"),
    ("senior-high", "Senior High"),
)

SUBJECTS = (
    ("mathematics", "Mathematics"),
    ("english-language", "English Language"),
    ("general-science", "General Science"),
    ("physics", "Physics"),
    ("chemistry", "Chemistry"),
    ("biology", "Biology"),
    ("social-studies", "Social Studies"),
    ("liberian-history", "Liberian History"),
    ("literature", "Literature"),
    ("economics", "Economics"),
    ("french", "French"),
    ("computer-science", "Computer Science"),
    ("waec-practical-labs", "WAEC Practical Labs"),
)


def connect_provisioning():
    from django.db.models.signals import post_save

    from core.models import TenantSchool

    post_save.connect(
        on_tenant_created,
        sender=TenantSchool,
        dispatch_uid="core.tenancy.provision_tenant",
    )


def on_tenant_created(sender, instance, created, **kwargs):
    if not created:
        return
    tenant_id = instance.pk

    def _run(tenant_id=tenant_id):
        try:
            provision_tenant_database(tenant_id)
        except Exception as exc:
            logger.error(
                "tenant provision failed tenant=%s error=%s",
                tenant_id,
                type(exc).__name__,
            )

    transaction.on_commit(_run)


def provision_tenant_database(tenant_id):
    """Create or reuse the tenant database, then seed. Failures stay PROVISIONING."""
    from core.models import TenantSchool

    tenant = TenantSchool.objects.get(pk=tenant_id)
    if settings.TENANCY_DB_MODE != "per_tenant":
        _seed(tenant)
        _store_schema_version(tenant, "default")
        return

    if not settings.TENANCY_PG_ADMIN_URL:
        raise ImproperlyConfigured(
            "TENANCY_PG_ADMIN_URL is required when TENANCY_DB_MODE=per_tenant."
        )
    _provision_physical(tenant)
    alias = register_tenant_database(tenant)
    call_command("migrate", database=alias, interactive=False, verbosity=0)
    _seed(tenant)
    _store_schema_version(tenant, alias)


def register_tenant_database(tenant):
    """Open the tenant alias with the decrypted role. The password is not logged."""
    if not tenant.db_alias or not tenant.db_password_encrypted:
        raise ImproperlyConfigured("Tenant database credentials are not provisioned.")
    password = decrypt_password(tenant.db_password_encrypted)
    current = connections.databases.get(tenant.db_alias)
    if current and current.get("NAME") == tenant.db_name and current.get("USER") == tenant.db_user:
        return tenant.db_alias
    if tenant.db_alias in connections.databases and hasattr(
        connections._connections, tenant.db_alias
    ):
        connections[tenant.db_alias].close()
        del connections[tenant.db_alias]
    config = copy.deepcopy(connections.databases["default"])
    config.update(
        {
            "NAME": tenant.db_name,
            "USER": tenant.db_user,
            "PASSWORD": password,
            "HOST": tenant.db_host,
            "PORT": str(tenant.db_port or ""),
            "ATOMIC_REQUESTS": False,
            "CONN_MAX_AGE": 0,
        }
    )
    config["TEST"] = {"NAME": tenant.db_name}
    connections.databases[tenant.db_alias] = config
    settings.DATABASES[tenant.db_alias] = config
    _allow_test_alias(tenant.db_alias)
    return tenant.db_alias


def _allow_test_alias(alias):
    """Django's test runner blocks aliases that were not declared up front."""
    from django.test.testcases import SimpleTestCase

    pending = list(SimpleTestCase.__subclasses__())
    seen = set()
    while pending:
        cls = pending.pop()
        if cls in seen:
            continue
        seen.add(cls)
        pending.extend(cls.__subclasses__())
        databases = cls.__dict__.get("databases")
        if isinstance(databases, frozenset) and alias not in databases:
            cls.databases = databases | {alias}


def migration_fingerprint(alias):
    with connections[alias].cursor() as cursor:
        cursor.execute("SELECT app, name FROM django_migrations ORDER BY 1, 2")
        rows = cursor.fetchall()
    payload = "\n".join(f"{app}.{name}" for app, name in rows)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def identifiers_for(slug):
    safe = slug.replace("-", "_")
    testing = str(settings.DATABASES["default"]["NAME"]).startswith("test_")
    if testing:
        return (
            f"sms_tenant_test_{safe}",
            f"sms_test_{safe}",
            f"tenant_test_{safe}",
        )
    return (f"sms_tenant_{safe}", f"sms_{safe}", f"tenant_{safe}")


def drop_test_tenant_databases():
    """Drop databases and roles created by the test suite. Other names are left alone."""
    url = getattr(settings, "TENANCY_PG_ADMIN_URL", "")
    if not url:
        return
    for alias in list(connections.databases):
        if not str(alias).startswith("tenant_test_"):
            continue
        if hasattr(connections._connections, alias):
            connections[alias].close()
            del connections[alias]
        del connections.databases[alias]
        settings.DATABASES.pop(alias, None)
    with psycopg.connect(url, autocommit=True) as conn:
        names = conn.execute(
            "SELECT datname FROM pg_database WHERE datname LIKE 'sms_tenant_test_%'"
        ).fetchall()
        for (name,) in names:
            conn.execute(
                "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname = %s",
                (name,),
            )
            conn.execute(sql.SQL("DROP DATABASE IF EXISTS {}").format(sql.Identifier(name)))
        roles = conn.execute(
            "SELECT rolname FROM pg_roles WHERE rolname LIKE 'sms_test_%'"
        ).fetchall()
        for (role,) in roles:
            conn.execute(sql.SQL("DROP ROLE IF EXISTS {}").format(sql.Identifier(role)))


def _provision_physical(tenant):
    db_name, db_user, db_alias = identifiers_for(tenant.slug)
    parsed = urlparse(settings.TENANCY_PG_ADMIN_URL)
    password = None
    with psycopg.connect(settings.TENANCY_PG_ADMIN_URL, autocommit=True) as conn:
        _require_createrole(conn)
        role_exists = _exists(conn, "SELECT 1 FROM pg_roles WHERE rolname = %s", db_user)
        if role_exists and not tenant.db_password_encrypted:
            raise ImproperlyConfigured(
                "Tenant role exists without a stored password."
            )
        if role_exists:
            password = decrypt_password(tenant.db_password_encrypted)
        else:
            password = secrets.token_urlsafe(32)
            conn.execute(
                sql.SQL(
                    "CREATE ROLE {} LOGIN PASSWORD {} NOSUPERUSER NOCREATEDB NOCREATEROLE"
                ).format(sql.Identifier(db_user), sql.Literal(password))
            )
        if not _exists(conn, "SELECT 1 FROM pg_database WHERE datname = %s", db_name):
            conn.execute(
                sql.SQL("CREATE DATABASE {} OWNER {}").format(
                    sql.Identifier(db_name),
                    sql.Identifier(db_user),
                )
            )
        conn.execute(
            sql.SQL("REVOKE ALL ON DATABASE {} FROM PUBLIC").format(sql.Identifier(db_name))
        )
        conn.execute(
            sql.SQL("GRANT CONNECT, TEMP ON DATABASE {} TO {}").format(
                sql.Identifier(db_name),
                sql.Identifier(db_user),
            )
        )
        _grant_public_schema(db_name, db_user)
    ciphertext = tenant.db_password_encrypted or encrypt_password(password)
    tenant.db_name = db_name
    tenant.db_user = db_user
    tenant.db_alias = db_alias
    tenant.db_host = parsed.hostname or "localhost"
    tenant.db_port = parsed.port or 5432
    tenant.db_password_encrypted = ciphertext
    tenant.save(
        update_fields=[
            "db_name",
            "db_user",
            "db_alias",
            "db_host",
            "db_port",
            "db_password_encrypted",
        ]
    )


def _grant_public_schema(db_name, db_user):
    parsed = urlparse(settings.TENANCY_PG_ADMIN_URL)
    admin_url = parsed._replace(path=f"/{db_name}").geturl()
    with psycopg.connect(admin_url, autocommit=True) as conn:
        conn.execute(
            sql.SQL("GRANT ALL ON SCHEMA public TO {}").format(sql.Identifier(db_user))
        )


def _require_createrole(conn):
    allowed = conn.execute(
        "SELECT rolsuper OR rolcreaterole FROM pg_roles WHERE rolname = current_user"
    ).fetchone()
    if not allowed or not allowed[0]:
        raise ImproperlyConfigured(
            "TENANCY_PG_ADMIN_URL role needs CREATEROLE to provision tenant logins."
        )


def _exists(conn, query, value):
    return conn.execute(query, (value,)).fetchone() is not None


def _seed(tenant):
    from core.models import Division, Subject

    with tenant_context(tenant):
        for code, name in DIVISIONS:
            Division.objects.get_or_create(
                school=tenant,
                code=code,
                defaults={"name": name},
            )
        for code, name in SUBJECTS:
            Subject.objects.get_or_create(
                school=tenant,
                code=code,
                defaults={"name": name},
            )


def _store_schema_version(tenant, alias):
    from core.models import TenantSchool

    tenant.schema_version = migration_fingerprint(alias)
    tenant.status = TenantSchool.Status.ACTIVE
    tenant.save(update_fields=["schema_version", "status"])
