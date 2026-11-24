"""Tenancy routing, isolation, provisioning, and fleet migration."""

import json
import base64
from datetime import datetime, timedelta, timezone

from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TransactionTestCase, override_settings

from core.models import Division, RoleMembership, Subject, TenantMigrationRecord, TenantSchool, User
from core.tenancy.context import TenantContextError, tenant_context
from core.tenancy.crypto import decrypt_password
from core.tenancy.provisioning import (
    drop_test_tenant_databases,
    provision_tenant_database,
)
from core.tests.support import FERNET_KEY, bearer, bearer_for

MODES = ("shared", "per_tenant")
ROUTES = ("header_path", "subdomain")


@override_settings(
    TENANCY_CREDENTIAL_KEY=FERNET_KEY,
    TENANCY_BASE_DOMAIN="gosms.test",
    ALLOWED_HOSTS=["localhost", "127.0.0.1", "testserver", ".gosms.test"],
)
class TenancyEngineTests(TransactionTestCase):
    def tearDown(self):
        drop_test_tenant_databases()
        super().tearDown()

    def test_demo_routes_do_not_require_a_tenant(self):
        response = self.client.get("/api/demo/v1/")
        self.assertEqual(response.status_code, 404)
        self.assertNotIn(b"tenant_header_required", response.content)

        rejected = self.client.get(
            "/api/demo/v1/",
            HTTP_AUTHORIZATION=bearer("core", "alpha-sh"),
        )
        self.assertEqual(rejected.status_code, 403)
        self.assertEqual(rejected.json()["code"], "app_scope_rejected")

    def test_auth_prefix_stays_open_until_login_exists(self):
        response = self.client.get("/api/v1/auth/")
        self.assertEqual(response.status_code, 404)
        self.assertNotIn(b"authentication_failed", response.content)

    def test_routing_matrix_and_platform_ping(self):
        for db_mode in MODES:
            with override_settings(TENANCY_DB_MODE=db_mode):
                alpha, beta = self._schools(db_mode)
                for routing in ROUTES:
                    with self.subTest(db_mode=db_mode, routing=routing):
                        with override_settings(TENANCY_ROUTING_MODE=routing):
                            self._assert_active(alpha, routing)
                            self._assert_unknown(routing)
                            self._assert_inactive(beta, routing, TenantSchool.Status.SUSPENDED)
                            self._assert_inactive(beta, routing, TenantSchool.Status.PROVISIONING)
                            beta.status = TenantSchool.Status.ACTIVE
                            beta.save(update_fields=["status"])
                            self._assert_platform(alpha, routing)

    def test_shared_isolation_blocks_cross_tenant_access(self):
        with override_settings(TENANCY_DB_MODE="shared", TENANCY_ROUTING_MODE="header_path"):
            alpha, beta = self._schools("shared-iso")
            with tenant_context(alpha):
                alpha_division = Division.objects.get(code="elementary")
                alpha_subject = Subject.objects.get(code="mathematics")
            with tenant_context(beta):
                beta_division = Division.objects.get(code="elementary")

            headers = self._school_headers(beta, "header_path")
            detail = f"/api/v1/divisions/{alpha_division.pk}/"
            self.assertEqual(self.client.get(detail, **headers).status_code, 404)
            patched = self.client.patch(
                detail,
                {"name": "Taken"},
                content_type="application/json",
                **headers,
            )
            self.assertEqual(patched.status_code, 404)
            self.assertEqual(self.client.delete(detail, **headers).status_code, 404)

            with tenant_context(alpha):
                self.assertTrue(Division.objects.filter(pk=alpha_division.pk).exists())
                self.assertFalse(Division.objects.filter(pk=beta_division.pk).exists())
                Division.objects.filter(pk=beta_division.pk).update(name="Hacked")
                self.assertEqual(Division.objects.filter(pk=beta_division.pk).delete()[0], 0)
            with tenant_context(beta):
                self.assertEqual(Division.objects.get(pk=beta_division.pk).name, "Elementary")

            stolen = self.client.patch(
                f"/api/v1/subjects/{alpha_subject.pk}/",
                data=json.dumps({"division": str(beta_division.pk)}),
                content_type="application/json",
                **self._school_headers(alpha, "header_path"),
            )
            self.assertEqual(stolen.status_code, 400)
            with tenant_context(alpha):
                with self.assertRaises(ValidationError):
                    Subject(
                        school=alpha,
                        code="borrowed",
                        name="Borrowed",
                        division_id=beta_division.pk,
                    ).full_clean()

            with self.assertRaises(TenantContextError):
                list(Division.objects.all())

    def test_per_tenant_databases_are_isolated(self):
        with override_settings(TENANCY_DB_MODE="per_tenant", TENANCY_ROUTING_MODE="header_path"):
            alpha, beta = self._schools("physical")
            self.assertTrue(alpha.db_name.startswith("sms_tenant_test_"))
            self.assertNotEqual(alpha.db_name, beta.db_name)
            self.assertNotIn("token", alpha.db_password_encrypted.lower())
            self.assertNotEqual(
                decrypt_password(alpha.db_password_encrypted),
                alpha.db_password_encrypted,
            )
            before = alpha.db_password_encrypted
            provision_tenant_database(alpha.pk)
            alpha.refresh_from_db()
            self.assertEqual(alpha.db_password_encrypted, before)

            with tenant_context(alpha):
                self.assertEqual(Division.objects.all().db, alpha.db_alias)
                Division.objects.create(school=alpha, code="extra-a", name="Extra A")
            with tenant_context(beta):
                self.assertFalse(Division.objects.filter(code="extra-a").exists())
                from django.db import connections

                with connections[beta.db_alias].cursor() as cursor:
                    cursor.execute(
                        "SELECT COUNT(*) FROM core_division WHERE code = %s",
                        ["extra-a"],
                    )
                    self.assertEqual(cursor.fetchone()[0], 0)

    def test_fleet_migration_isolates_failures(self):
        with override_settings(TENANCY_DB_MODE="per_tenant"):
            alpha, beta = self._schools("fleet")
            call_command("migrate_all_tenants")
            alpha.refresh_from_db()
            beta.refresh_from_db()
            self.assertTrue(alpha.schema_version)
            self.assertEqual(
                TenantMigrationRecord.objects.filter(
                    tenant=alpha,
                    status=TenantMigrationRecord.Status.SUCCEEDED,
                ).count(),
                1,
            )
            alpha.db_name = "sms_tenant_test_missing"
            alpha.save(update_fields=["db_name"])
            with self.assertRaises(CommandError):
                call_command("migrate_all_tenants")
            self.assertTrue(
                TenantMigrationRecord.objects.filter(
                    tenant=alpha,
                    status=TenantMigrationRecord.Status.FAILED,
                ).exists()
            )
            self.assertGreaterEqual(
                TenantMigrationRecord.objects.filter(
                    tenant=beta,
                    status=TenantMigrationRecord.Status.SUCCEEDED,
                ).count(),
                2,
            )

    def test_shared_mode_refuses_fleet_command(self):
        with override_settings(TENANCY_DB_MODE="shared"):
            with self.assertRaises(CommandError):
                call_command("migrate_all_tenants")

    def test_missing_and_invalid_bearer(self):
        with override_settings(TENANCY_DB_MODE="shared", TENANCY_ROUTING_MODE="header_path"):
            alpha, _beta = self._schools("tokens")
            missing = self.client.get(
                "/api/v1/divisions/",
                HTTP_X_TENANT_SLUG=alpha.slug,
            )
            self.assertEqual(missing.status_code, 401)
            invalid = self.client.get(
                "/api/v1/divisions/",
                HTTP_X_TENANT_SLUG=alpha.slug,
                HTTP_AUTHORIZATION=f"Bearer {_unsigned_token()}",
            )
            self.assertEqual(invalid.status_code, 401)
            foreign = self.client.get(
                "/api/v1/divisions/",
                HTTP_X_TENANT_SLUG=alpha.slug,
                HTTP_AUTHORIZATION=bearer("demo", ""),
            )
            self.assertEqual(foreign.status_code, 403)
            self.assertEqual(foreign.json()["code"], "app_scope_rejected")

    def _schools(self, label):
        safe = label.replace("_", "-")
        alpha = TenantSchool.objects.create(slug=f"alpha-{safe}"[:40].rstrip("-"), name="Alpha")
        beta = TenantSchool.objects.create(slug=f"beta-{safe}"[:40].rstrip("-"), name="Beta")
        for school in (alpha, beta):
            school.refresh_from_db()
            if school.status != TenantSchool.Status.ACTIVE:
                provision_tenant_database(school.pk)
                school.refresh_from_db()
            self.assertEqual(school.status, TenantSchool.Status.ACTIVE, label)
            self._ensure_principal(school)
        return alpha, beta

    def _ensure_principal(self, school):
        if not hasattr(self, "_principals"):
            self._principals = {}
        email = f"principal@{school.slug}.test"
        user = User.objects.filter(email=email, school=school).first()
        if user is None:
            user = User.objects.create_user(
                email,
                "principal-pass-1",
                app_scope=User.AppScope.CORE,
                school=school,
                display_name="Principal",
            )
            RoleMembership.objects.create(user=user, role=RoleMembership.Role.PRINCIPAL)
        self._principals[school.pk] = user
        return user

    def _assert_active(self, school, routing):
        response = self.client.get("/api/v1/divisions/", **self._school_headers(school, routing))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["count"], 4)
        mismatched = dict(self._school_headers(school, routing))
        mismatched["HTTP_AUTHORIZATION"] = bearer("core", "other-school")
        rejected = self.client.get("/api/v1/divisions/", **mismatched)
        self.assertEqual(rejected.status_code, 403)
        self.assertEqual(rejected.json()["code"], "tenant_mismatch")

    def _assert_unknown(self, routing):
        headers = {"HTTP_AUTHORIZATION": bearer("core", "missing-school")}
        if routing == "header_path":
            response = self.client.get("/api/v1/divisions/")
            self.assertEqual(response.status_code, 400)
            self.assertEqual(response.json()["code"], "tenant_header_required")
            headers["HTTP_X_TENANT_SLUG"] = "missing-school"
        else:
            headers["HTTP_HOST"] = "missing-school.gosms.test"
        missing = self.client.get("/api/v1/divisions/", **headers)
        self.assertEqual(missing.status_code, 404)
        self.assertEqual(missing.json()["code"], "tenant_not_found")

    def _assert_inactive(self, school, routing, status):
        school.status = status
        school.save(update_fields=["status"])
        response = self.client.get(
            "/api/v1/divisions/",
            **self._school_headers(school, routing),
        )
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json()["code"], "tenant_unavailable")

    def _assert_platform(self, school, routing):
        platform = {"HTTP_AUTHORIZATION": bearer("core", "")}
        if routing == "subdomain":
            platform["HTTP_HOST"] = "platform.gosms.test"
        response = self.client.get("/api/v1/platform/ping/", **platform)
        self.assertEqual(response.status_code, 200, response.content)
        school_token = dict(platform)
        school_token["HTTP_AUTHORIZATION"] = bearer("core", school.slug)
        rejected = self.client.get("/api/v1/platform/ping/", **school_token)
        self.assertEqual(rejected.status_code, 403)
        if routing == "subdomain":
            wrong_host = self.client.get(
                "/api/v1/platform/ping/",
                HTTP_HOST=f"{school.slug}.gosms.test",
                HTTP_AUTHORIZATION=bearer("core", ""),
            )
            self.assertEqual(wrong_host.status_code, 404)
            reserved_school = self.client.get(
                "/api/v1/divisions/",
                HTTP_HOST="api.gosms.test",
                HTTP_AUTHORIZATION=bearer("core", "api"),
            )
            self.assertEqual(reserved_school.status_code, 404)
        else:
            headed = self.client.get(
                "/api/v1/platform/ping/",
                HTTP_X_TENANT_SLUG=school.slug,
                HTTP_AUTHORIZATION=bearer("core", ""),
            )
            self.assertEqual(headed.status_code, 403)

    def _school_headers(self, school, routing):
        headers = {"HTTP_AUTHORIZATION": bearer_for(self._principals[school.pk], "principal")}
        if routing == "header_path":
            headers["HTTP_X_TENANT_SLUG"] = school.slug
        else:
            headers["HTTP_HOST"] = f"{school.slug}.gosms.test"
        return headers


def _unsigned_token():
    header = _b64(b'{"alg":"none","typ":"JWT"}')
    payload = {
        "app_scope": "core",
        "tenant_slug": "alpha-tokens",
        "exp": int((datetime.now(timezone.utc) + timedelta(minutes=5)).timestamp()),
    }
    body = _b64(json.dumps(payload).encode())
    return f"{header}.{body}."


def _b64(raw):
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")
