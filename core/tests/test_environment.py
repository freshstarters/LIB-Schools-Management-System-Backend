import os
import subprocess
import sys
import tempfile
import textwrap
from pathlib import Path

from django.core.exceptions import ImproperlyConfigured
from django.test import SimpleTestCase

from core.helpers.environment import (
    postgres_database,
    tenancy_db_mode,
    tenancy_routing_mode,
)


class PostgresUrlTests(SimpleTestCase):
    def test_postgresql_scheme_is_accepted(self):
        config = postgres_database(
            "postgresql://sms:secret@localhost:5432/lib_sms",
            label="DATABASE_URL",
            required=True,
        )
        self.assertEqual(config["ENGINE"], "django.db.backends.postgresql")
        self.assertEqual(config["NAME"], "lib_sms")

    def test_postgres_scheme_is_accepted(self):
        config = postgres_database(
            "postgres://sms:secret@localhost:5432/lib_sms",
            label="DATABASE_URL",
            required=True,
        )
        self.assertEqual(config["ENGINE"], "django.db.backends.postgresql")

    def test_non_postgresql_url_fails_closed(self):
        with self.assertRaises(ImproperlyConfigured):
            postgres_database(
                "mysql://sms:secret@localhost:3306/lib_sms",
                label="DATABASE_URL",
                required=True,
            )

    def test_missing_required_url_fails_closed(self):
        with self.assertRaises(ImproperlyConfigured):
            postgres_database(None, label="DATABASE_URL", required=True)

    def test_blank_optional_url_is_empty(self):
        self.assertEqual(
            postgres_database("  ", label="TENANCY_PG_ADMIN_URL", required=False),
            "",
        )


class TenancySwitchTests(SimpleTestCase):
    def test_unset_defaults(self):
        self.assertEqual(tenancy_db_mode(None), "shared")
        self.assertEqual(tenancy_routing_mode(None, debug=True), "header_path")
        self.assertEqual(tenancy_routing_mode(None, debug=False), "subdomain")

    def test_legal_combinations(self):
        for db_mode in ("shared", "per_tenant"):
            for routing_mode in ("subdomain", "header_path"):
                with self.subTest(db_mode=db_mode, routing_mode=routing_mode):
                    self.assertEqual(tenancy_db_mode(db_mode), db_mode)
                    self.assertEqual(tenancy_routing_mode(routing_mode), routing_mode)

    def test_empty_and_illegal_values_fail_closed(self):
        for raw in ("", "  ", "schema", "tenant"):
            with self.subTest(raw=raw):
                with self.assertRaises(ImproperlyConfigured):
                    tenancy_db_mode(raw)
                with self.assertRaises(ImproperlyConfigured):
                    tenancy_routing_mode(raw)


class StartupTests(SimpleTestCase):
    def test_non_postgresql_database_url_fails_startup(self):
        result = _boot(DATABASE_URL="mysql://sms:secret@localhost:3306/lib_sms")
        self.assertIn("IMPROPERLY_CONFIGURED", result)
        self.assertIn("PostgreSQL", result)

    def test_missing_database_url_fails_startup(self):
        result = _boot(drop=("DATABASE_URL",))
        self.assertIn("IMPROPERLY_CONFIGURED", result)
        self.assertIn("DATABASE_URL", result)

    def test_non_postgresql_admin_url_fails_startup(self):
        result = _boot(
            DATABASE_URL=os.environ["DATABASE_URL"],
            TENANCY_PG_ADMIN_URL="mysql://sms:secret@localhost:3306/postgres",
        )
        self.assertIn("IMPROPERLY_CONFIGURED", result)
        self.assertIn("TENANCY_PG_ADMIN_URL", result)

    def test_unset_admin_url_and_tenancy_switches_start(self):
        result = _boot(
            DATABASE_URL=os.environ["DATABASE_URL"],
            DEBUG="true",
            drop=("TENANCY_PG_ADMIN_URL", "TENANCY_DB_MODE", "TENANCY_ROUTING_MODE"),
        )
        self.assertIn("STARTED", result)
        self.assertIn("DB_MODE:shared", result)
        self.assertIn("ROUTING_MODE:header_path", result)

    def test_production_default_routing_is_subdomain(self):
        result = _boot(
            DATABASE_URL=os.environ["DATABASE_URL"],
            DEBUG="false",
            TENANCY_BASE_DOMAIN="gosms.test",
            drop=("TENANCY_PG_ADMIN_URL", "TENANCY_DB_MODE", "TENANCY_ROUTING_MODE"),
        )
        self.assertIn("STARTED", result)
        self.assertIn("ROUTING_MODE:subdomain", result)

    def test_production_default_without_base_domain_fails(self):
        result = _boot(
            DATABASE_URL=os.environ["DATABASE_URL"],
            DEBUG="false",
            TENANCY_BASE_DOMAIN="",
            drop=("TENANCY_ROUTING_MODE",),
        )
        self.assertIn("IMPROPERLY_CONFIGURED", result)
        self.assertIn("TENANCY_BASE_DOMAIN", result)

    def test_subdomain_without_base_domain_fails_startup(self):
        result = _boot(
            DATABASE_URL=os.environ["DATABASE_URL"],
            TENANCY_ROUTING_MODE="subdomain",
            TENANCY_BASE_DOMAIN="",
        )
        self.assertIn("IMPROPERLY_CONFIGURED", result)
        self.assertIn("TENANCY_BASE_DOMAIN", result)

    def test_invalid_credential_key_fails_startup(self):
        result = _boot(
            DATABASE_URL=os.environ["DATABASE_URL"],
            TENANCY_CREDENTIAL_KEY="not-a-fernet-key",
        )
        self.assertIn("IMPROPERLY_CONFIGURED", result)
        self.assertIn("TENANCY_CREDENTIAL_KEY", result)

    def test_per_tenant_without_credential_key_fails_startup(self):
        result = _boot(
            DATABASE_URL=os.environ["DATABASE_URL"],
            TENANCY_DB_MODE="per_tenant",
            drop=("TENANCY_CREDENTIAL_KEY",),
        )
        self.assertIn("IMPROPERLY_CONFIGURED", result)
        self.assertIn("TENANCY_CREDENTIAL_KEY", result)

    def test_illegal_tenancy_mode_fails_startup(self):
        result = _boot(
            DATABASE_URL=os.environ["DATABASE_URL"],
            TENANCY_DB_MODE="schema",
        )
        self.assertIn("IMPROPERLY_CONFIGURED", result)
        self.assertIn("TENANCY_DB_MODE", result)


def _boot(**overrides):
    drop = overrides.pop("drop", ())
    child_env = os.environ.copy()
    for name in drop:
        child_env.pop(name, None)
    child_env.update(overrides)
    child_env["SECRET_KEY"] = child_env.get("SECRET_KEY") or "startup-test-secret"
    child_env["DJANGO_SETTINGS_MODULE"] = "lib_school_management_system.settings"

    with tempfile.NamedTemporaryFile("w", delete=False) as handle:
        handle.write("")
        empty_env = handle.name
    child_env["ENV_FILE"] = empty_env

    code = textwrap.dedent(
        """
        import django
        from django.core.exceptions import ImproperlyConfigured
        try:
            django.setup()
        except ImproperlyConfigured as exc:
            print(f"IMPROPERLY_CONFIGURED:{exc}")
            raise SystemExit(0)
        from django.conf import settings
        print(f"DB_MODE:{settings.TENANCY_DB_MODE}")
        print(f"ROUTING_MODE:{settings.TENANCY_ROUTING_MODE}")
        print("STARTED")
        """
    )
    try:
        completed = subprocess.run(
            [sys.executable, "-c", code],
            cwd=Path(__file__).resolve().parents[2],
            env=child_env,
            capture_output=True,
            text=True,
            check=False,
        )
    finally:
        Path(empty_env).unlink(missing_ok=True)

    output = f"{completed.stdout}\n{completed.stderr}"
    if completed.returncode != 0 and "IMPROPERLY_CONFIGURED" not in output:
        raise AssertionError(output)
    return output
