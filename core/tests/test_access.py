"""Invites, password reset, role checks, and idempotent writes."""

import os
import uuid
from datetime import timedelta
from types import SimpleNamespace

from django.core.cache import cache
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase, override_settings
from django.utils import timezone

from core.helpers.permissions import ACTIONS, ROLE_ORDER, allows, campus_matches
from core.models import (
    Division,
    InviteToken,
    NotificationLog,
    PasswordResetToken,
    RefreshToken,
    RoleMembership,
    TenantSchool,
    User,
)
from core.models.access import NotificationLog as Log
from core.tenancy.context import tenant_context
from core.tests.support import bearer_for
from core.tests.test_auth import PASSWORD, post_json

LOCATIONS = {
    "default": {
        "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
        "LOCATION": "access-tests",
    }
}
INVITE = "/api/demo/v1/auth/invites/"
ACCEPT = "/api/demo/v1/auth/invites/accept/"
RESET = "/api/demo/v1/auth/password-reset/request/"
CONFIRM = "/api/demo/v1/auth/password-reset/confirm/"
LOGIN = "/api/demo/v1/auth/login/"
REFRESH = "/api/demo/v1/auth/refresh/"
SESSIONS = "/api/demo/v1/auth/sessions/"
NEW_PASSWORD = "Correct-horse-battery-9"

# Independent copy of Section 2.2. Plain grants are unscoped. own-* grants need scope.
SPEC = {
    "tenant_provisioning": {"platform_super_admin": "CRUD"},
    "dashboard": {
        "platform_super_admin": "R",
        "principal": "R",
        "registrar": "R",
        "bursar": "R",
        "teacher": "own-read",
        "support_staff": "own-read",
    },
    "students": {
        "principal": "CRUD",
        "registrar": "CRUD",
        "bursar": "R",
        "teacher": "own-write",
        "support_staff": "R",
        "parent": "own-read",
        "student": "own-read",
    },
    "admissions": {"principal": "CRUD", "registrar": "CRUD", "bursar": "R"},
    "fees": {
        "principal": "R+approve",
        "registrar": "R",
        "bursar": "CRUD",
        "parent": "own-pay",
    },
    "classes": {
        "principal": "CRUD",
        "registrar": "CRUD",
        "bursar": "R",
        "teacher": "own-read",
        "parent": "own-read",
        "student": "own-read",
    },
    "attendance": {
        "principal": "CRUD",
        "registrar": "CRUD",
        "teacher": "own-write",
        "parent": "own-read",
        "student": "own-read",
    },
    "gradebook": {
        "principal": "CRUD+lock",
        "registrar": "CRUD+lock",
        "teacher": "own-write",
        "parent": "own-read",
        "student": "own-read",
    },
    "waec": {
        "principal": "CRUD",
        "registrar": "CRUD",
        "bursar": "RW",
        "teacher": "own-read",
        "parent": "own-read",
        "student": "own-read",
    },
    "report_cards": {
        "principal": "CRUD",
        "registrar": "CRUD",
        "teacher": "own-read",
        "parent": "own-read",
        "student": "own-read",
    },
    "staff": {
        "principal": "CRUD",
        "registrar": "R",
        "bursar": "R",
        "teacher": "R",
        "support_staff": "R",
    },
    "guardians": {
        "principal": "CRUD",
        "registrar": "CRUD",
        "bursar": "R",
        "teacher": "own-read",
        "support_staff": "R",
        "parent": "own-write",
    },
    "library": {
        "principal": "CRUD",
        "registrar": "R",
        "teacher": "R",
        "support_staff": "CRUD",
        "parent": "own-read",
        "student": "own-read",
    },
    "transport": {
        "principal": "CRUD",
        "registrar": "R",
        "teacher": "R",
        "support_staff": "CRUD",
        "parent": "own-read",
        "student": "own-read",
    },
    "announcements": {
        "principal": "CRUD+topup",
        "registrar": "CRU",
        "bursar": "R+topup",
        "teacher": "R",
        "support_staff": "R",
        "parent": "R",
        "student": "R",
    },
    "ai": {
        "principal": "R",
        "registrar": "R",
        "bursar": "R",
        "teacher": "own-read",
    },
    "settings": {"principal": "CRUD", "registrar": "R", "bursar": "RW"},
    "audit": {
        "platform_super_admin": "R",
        "principal": "R",
        "registrar": "R",
        "bursar": "R",
    },
}
PLAIN = {
    "CRUD": {"create", "read", "update", "delete"},
    "CRU": {"create", "read", "update"},
    "R": {"read"},
    "RW": {"read", "update"},
    "R+approve": {"read", "approve"},
    "CRUD+lock": {"create", "read", "update", "delete", "lock"},
    "CRUD+topup": {"create", "read", "update", "delete", "topup"},
    "R+topup": {"read", "topup"},
    "own-read": set(),
    "own-write": set(),
    "own-pay": set(),
}
SCOPED = {
    "own-read": {"read"},
    "own-write": {"read", "update"},
    "own-pay": {"read", "pay"},
}


def _staff(email, role, *, app_scope=User.AppScope.DEMO, school=None, active=True, phone=None):
    user = User.objects.create_user(
        email,
        PASSWORD,
        app_scope=app_scope,
        school=school,
        phone=phone,
        display_name=email.split("@", 1)[0],
        is_active=active,
    )
    RoleMembership.objects.create(user=user, role=role)
    return user


@override_settings(
    CACHES=LOCATIONS,
    DEBUG=True,
    EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend",
    EMAIL_CONFIGURED=True,
)
class AccessTests(TestCase):
    def setUp(self):
        cache.clear()

    def test_registration_route_does_not_exist(self):
        response = post_json(self.client, "/api/demo/v1/auth/register/", {"email": "a@b.test"})
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json()["code"], "not_found")

    def test_expired_and_reused_invites_are_rejected(self):
        principal = _staff("samuel.kollie@gojo.test", "principal")
        created = self._invite(principal, "new.teacher@gojo.test", "teacher")
        self.assertEqual(created.status_code, 201, created.content)
        token = self._token()
        invited = User.objects.get(email="new.teacher@gojo.test")
        self.assertFalse(invited.is_active)
        denied = post_json(self.client, LOGIN, {"login": invited.email, "password": PASSWORD})
        self.assertEqual(denied.status_code, 401)

        pending = post_json(
            self.client,
            INVITE,
            self._body("new.teacher@gojo.test", "bursar"),
            HTTP_AUTHORIZATION=bearer_for(principal, "principal"),
        )
        self.assertEqual(pending.status_code, 400)
        self.assertEqual(pending.json()["code"], "invite_pending")

        row = InviteToken.objects.get(user=invited)
        row.expires_at = timezone.now() - timedelta(hours=1)
        row.save(update_fields=["expires_at"])
        expired = post_json(self.client, ACCEPT, {"token": token, "password": NEW_PASSWORD})
        self.assertEqual(expired.status_code, 400)

        again = self._invite(principal, "new.teacher@gojo.test", "teacher")
        self.assertEqual(again.status_code, 201, again.content)
        fresh = self._token()
        accepted = post_json(self.client, ACCEPT, {"token": fresh, "password": NEW_PASSWORD})
        self.assertEqual(accepted.status_code, 200, accepted.content)
        self.assertIn("access", accepted.json())
        self.assertTrue(self.client.cookies["refresh_demo"].value)
        reused = post_json(self.client, ACCEPT, {"token": fresh, "password": NEW_PASSWORD})
        self.assertEqual(reused.status_code, 400)

    def test_accept_replay_starts_a_new_session(self):
        principal = _staff("samuel.kollie@gojo.test", "principal")
        created = self._invite(principal, "new.teacher@gojo.test", "teacher")
        self.assertEqual(created.status_code, 201, created.content)
        token = self._token()
        key = str(uuid.uuid4())
        body = {"token": token, "password": NEW_PASSWORD}
        headers = {"HTTP_IDEMPOTENCY_KEY": key}
        first = post_json(self.client, ACCEPT, body, **headers)
        second = post_json(self.client, ACCEPT, body, **headers)
        self.assertEqual(first.status_code, 200, first.content)
        self.assertEqual(second.status_code, 200, second.content)
        self.assertIn("access", second.json())
        self.assertNotEqual(first.json()["access"], second.json()["access"])
        invited = User.objects.get(email="new.teacher@gojo.test")
        self.assertEqual(RefreshToken.objects.filter(user=invited, revoked_at__isnull=True).count(), 2)
        invited.set_password("Different-horse-battery-9")
        invited.save(update_fields=["password"])
        stale = post_json(self.client, ACCEPT, body, **headers)
        self.assertEqual(stale.status_code, 400)
        self.assertEqual(stale.json()["code"], "invite_used")
        self.assertEqual(stale.json()["detail"], "This invite was already used. Sign in.")
        self.assertEqual(RefreshToken.objects.filter(user=invited, revoked_at__isnull=True).count(), 2)
        clash = post_json(
            self.client,
            ACCEPT,
            {"token": token, "password": "Another-horse-battery-9"},
            **headers,
        )
        self.assertEqual(clash.status_code, 409)
        self.assertEqual(clash.json()["code"], "idempotency_conflict")

    def test_phone_reset_emails_the_account_once(self):
        from django.core import mail

        user = _staff("ada@gojo.test", "teacher", phone="+231770123456")
        key = str(uuid.uuid4())
        headers = {"HTTP_IDEMPOTENCY_KEY": key}
        body = {"login": "0770123456"}
        first = post_json(self.client, RESET, body, **headers)
        second = post_json(self.client, RESET, body, **headers)
        self.assertEqual(first.status_code, 200, first.content)
        self.assertEqual(first.json(), second.json())
        self.assertEqual(PasswordResetToken.objects.filter(user=user).count(), 1)
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to, ["ada@gojo.test"])
        self.assertFalse(Log.objects.filter(channel=Log.Channel.SMS).exists())

    def test_registrar_cannot_grant_a_bursar(self):
        registrar = _staff("beatrice.toe@gojo.test", "registrar")
        response = self._invite(registrar, "money@gojo.test", "bursar")
        self.assertEqual(response.status_code, 403)
        teacher = self._invite(registrar, "class@gojo.test", "teacher")
        self.assertEqual(teacher.status_code, 201, teacher.content)

    def test_active_account_gains_a_role_without_a_token(self):
        principal = _staff("samuel.kollie@gojo.test", "principal")
        teacher = _staff("grace.johnson@gojo.test", "teacher")
        added = self._invite(principal, teacher.email, "bursar")
        self.assertEqual(added.status_code, 200, added.content)
        self.assertIsNone(added.json()["invite_id"])
        self.assertEqual(added.json()["delivery"], "none")
        self.assertTrue(teacher.memberships.filter(role="bursar").exists())
        duplicate = self._invite(principal, teacher.email, "bursar")
        self.assertEqual(duplicate.status_code, 400)
        self.assertEqual(duplicate.json()["code"], "role_exists")

    def test_revoke_deletes_an_inactive_account(self):
        principal = _staff("samuel.kollie@gojo.test", "principal")
        created = self._invite(principal, "temp@gojo.test", "teacher")
        invite_id = created.json()["invite_id"]
        revoked = self.client.post(
            f"{INVITE}{invite_id}/revoke/",
            HTTP_AUTHORIZATION=bearer_for(principal, "principal"),
        )
        self.assertEqual(revoked.status_code, 204, revoked.content)
        self.assertFalse(User.objects.filter(email="temp@gojo.test").exists())
        replacement = self._invite(principal, "temp@gojo.test", "teacher")
        self.assertEqual(replacement.status_code, 201, replacement.content)

    def test_reset_revokes_every_session_and_hides_unknown_accounts(self):
        user = _staff("ada@gojo.test", "teacher")
        post_json(self.client, LOGIN, {"login": user.email, "password": PASSWORD})
        other = self.client_class()
        post_json(other, LOGIN, {"login": user.email, "password": PASSWORD})
        self.assertEqual(RefreshToken.objects.filter(user=user, revoked_at__isnull=True).count(), 2)

        missing = post_json(self.client, RESET, {"login": "nobody@gojo.test"})
        inactive = _staff("pending@gojo.test", "parent", active=False)
        quiet = post_json(self.client, RESET, {"login": inactive.email})
        phone = post_json(self.client, RESET, {"login": "0770123456"})
        self.assertEqual(missing.json()["detail"], quiet.json()["detail"])
        self.assertEqual(phone.json()["detail"], missing.json()["detail"])
        self.assertFalse(
            Log.objects.filter(channel=Log.Channel.EMAIL, recipient=inactive.email).exists()
        )
        self.assertFalse(Log.objects.filter(channel=Log.Channel.SMS).exists())
        self.assertEqual(PasswordResetToken.objects.count(), 0)

        asked = post_json(self.client, RESET, {"login": user.email})
        self.assertEqual(asked.status_code, 200)
        token = self._token("password reset token is ")
        confirmed = post_json(
            self.client,
            CONFIRM,
            {"token": token, "password": NEW_PASSWORD},
        )
        self.assertEqual(confirmed.status_code, 200, confirmed.content)
        self.assertNotIn("access", confirmed.json())
        self.assertEqual(RefreshToken.objects.filter(user=user, revoked_at__isnull=True).count(), 0)
        stale = post_json(other, REFRESH, {})
        self.assertEqual(stale.status_code, 401)
        cache.clear()
        reused = post_json(self.client, CONFIRM, {"token": token, "password": NEW_PASSWORD})
        self.assertEqual(reused.status_code, 400)

    def test_one_browser_session_can_be_revoked(self):
        user = _staff("ada@gojo.test", "teacher")
        post_json(self.client, LOGIN, {"login": user.email, "password": PASSWORD})
        other = self.client_class()
        post_json(other, LOGIN, {"login": user.email, "password": PASSWORD})
        listed = self.client.get(SESSIONS)
        session_id = listed.json()["sessions"][0]["session_id"]
        missing = other.delete(f"{SESSIONS}{session_id}/")
        self.assertEqual(missing.status_code, 404)
        revoked = self.client.delete(f"{SESSIONS}{session_id}/")
        self.assertEqual(revoked.status_code, 204)
        self.assertEqual(post_json(self.client, REFRESH, {}).status_code, 401)
        self.assertEqual(post_json(other, REFRESH, {}).status_code, 200)

    def test_invite_replay_does_not_create_a_second_account(self):
        principal = _staff("samuel.kollie@gojo.test", "principal")
        key = str(uuid.uuid4())
        headers = {
            "HTTP_AUTHORIZATION": bearer_for(principal, "principal"),
            "HTTP_IDEMPOTENCY_KEY": key,
        }
        first = post_json(self.client, INVITE, self._body("once@gojo.test", "teacher"), **headers)
        second = post_json(self.client, INVITE, self._body("once@gojo.test", "teacher"), **headers)
        self.assertEqual(first.status_code, 201)
        self.assertEqual(second.status_code, 201)
        self.assertEqual(first.json(), second.json())
        self.assertEqual(User.objects.filter(email="once@gojo.test").count(), 1)
        clash = post_json(
            self.client,
            INVITE,
            self._body("other@gojo.test", "teacher"),
            **headers,
        )
        self.assertEqual(clash.status_code, 409)
        self.assertEqual(clash.json()["code"], "idempotency_conflict")
        self.assertFalse(User.objects.filter(email="other@gojo.test").exists())

    def test_catalog_roles_and_conflict(self):
        school = TenantSchool.objects.create(slug="alpha", name="Alpha", status=TenantSchool.Status.ACTIVE)
        with tenant_context(school):
            Division.objects.create(school=school, code="elementary", name="Elementary")
        principal = _staff(
            "samuel.kollie@gojo.test",
            "principal",
            app_scope=User.AppScope.CORE,
            school=school,
        )
        teacher = _staff(
            "grace.johnson@gojo.test",
            "teacher",
            app_scope=User.AppScope.CORE,
            school=school,
        )
        parent = _staff(
            "parent@gojo.test",
            "parent",
            app_scope=User.AppScope.CORE,
            school=school,
        )
        empty = User.objects.create_user(
            "empty@gojo.test",
            PASSWORD,
            app_scope=User.AppScope.CORE,
            school=school,
            display_name="Empty",
        )
        headers = {"HTTP_X_TENANT_SLUG": school.slug}

        def auth(user, role):
            return {**headers, "HTTP_AUTHORIZATION": bearer_for(user, role)}

        listed = self.client.get("/api/v1/divisions/", **auth(principal, "principal"))
        self.assertEqual(listed.status_code, 200, listed.content)
        division_id = listed.json()["results"][0]["id"]
        detail = f"/api/v1/divisions/{division_id}/"
        teacher_read = self.client.get(detail, **auth(teacher, "teacher"))
        self.assertEqual(teacher_read.status_code, 200)
        teacher_write = self.client.patch(
            detail,
            {"name": "Taken"},
            content_type="application/json",
            **auth(teacher, "teacher"),
        )
        self.assertEqual(teacher_write.status_code, 403)
        self.assertEqual(self.client.get(detail, **auth(parent, "parent")).status_code, 403)
        self.assertEqual(self.client.get(detail, **auth(empty, "")).status_code, 403)

        current = teacher_read.json()["updated_at"]
        saved = self.client.patch(
            detail,
            {"name": "Lower School"},
            content_type="application/json",
            HTTP_IF_UNMATCHED_SINCE=current,
            **auth(principal, "principal"),
        )
        self.assertEqual(saved.status_code, 200, saved.content)
        conflict = self.client.patch(
            detail,
            {"name": "Old Name"},
            content_type="application/json",
            HTTP_IF_UNMATCHED_SINCE=current,
            **auth(principal, "principal"),
        )
        self.assertEqual(conflict.status_code, 409)
        self.assertEqual(conflict.json()["code"], "SYNC_CONFLICT")
        self.assertEqual(conflict.json()["server_record"]["name"], "Lower School")

        key = str(uuid.uuid4())
        first = self.client.patch(
            detail,
            {"name": "Once"},
            content_type="application/json",
            HTTP_IDEMPOTENCY_KEY=key,
            **auth(principal, "principal"),
        )
        second = self.client.patch(
            detail,
            {"name": "Once"},
            content_type="application/json",
            HTTP_IDEMPOTENCY_KEY=key,
            **auth(principal, "principal"),
        )
        self.assertEqual(first.status_code, 200, first.content)
        self.assertEqual(second.json(), first.json())
        with tenant_context(school):
            self.assertEqual(Division.objects.get(pk=division_id).name, "Once")

    def test_matrix_covers_every_role_and_module(self):
        self.assertEqual(set(SPEC), {
            "tenant_provisioning",
            "dashboard",
            "students",
            "admissions",
            "fees",
            "classes",
            "attendance",
            "gradebook",
            "waec",
            "report_cards",
            "staff",
            "guardians",
            "library",
            "transport",
            "announcements",
            "ai",
            "settings",
            "audit",
        })
        for module, grants in SPEC.items():
            for role in ROLE_ORDER:
                code = grants.get(role)
                plain = PLAIN.get(code, set())
                scoped = SCOPED.get(code, set())
                for action in ACTIONS:
                    with self.subTest(module=module, role=role, action=action):
                        self.assertEqual(allows(role, module, action), action in plain)
                        self.assertEqual(
                            allows(role, module, action, scope_proven=True),
                            action in plain or action in scoped,
                        )
        campus = uuid.uuid4()
        other = uuid.uuid4()
        teacher = SimpleNamespace(role="teacher", campus_id=campus)
        school_wide = SimpleNamespace(role="teacher", campus_id=None)
        self.assertTrue(campus_matches([teacher], {"teacher"}, campus))
        self.assertFalse(campus_matches([teacher], {"teacher"}, other))
        self.assertTrue(campus_matches([school_wide], {"teacher"}, other))
        self.assertFalse(campus_matches([teacher], {"parent"}, campus))

    def test_seed_is_local_and_repeatable(self):
        with override_settings(DEBUG=False):
            with self.assertRaises(CommandError):
                call_command("seed_gojo_demo")
        with override_settings(DEBUG=True):
            old = os.environ.get("SEED_GOJO_PASSWORD")
            os.environ["SEED_GOJO_PASSWORD"] = ""
            try:
                with self.assertRaises(CommandError):
                    call_command("seed_gojo_demo")
                os.environ["SEED_GOJO_PASSWORD"] = NEW_PASSWORD
                call_command("seed_gojo_demo")
                call_command("seed_gojo_demo")
            finally:
                if old is None:
                    os.environ.pop("SEED_GOJO_PASSWORD", None)
                else:
                    os.environ["SEED_GOJO_PASSWORD"] = old
        self.assertEqual(User.objects.filter(app_scope=User.AppScope.DEMO).count(), 4756)
        user = User.objects.get(email="samuel.kollie@gojo.test")
        self.assertEqual(user.display_name, "Dr. Samuel Kollie")
        self.assertTrue(user.check_password(NEW_PASSWORD))
        user.set_password(PASSWORD)
        user.display_name = "Changed"
        user.save()
        os.environ["SEED_GOJO_PASSWORD"] = NEW_PASSWORD
        try:
            call_command("seed_gojo_demo")
        finally:
            os.environ.pop("SEED_GOJO_PASSWORD", None)
        user.refresh_from_db()
        self.assertTrue(user.check_password(PASSWORD))
        self.assertEqual(user.display_name, "Changed")
        self.assertEqual(user.memberships.get().role, "principal")

    def _invite(self, actor, email, role):
        return post_json(
            self.client,
            INVITE,
            self._body(email, role),
            HTTP_AUTHORIZATION=bearer_for(actor, "principal"),
        )

    def _body(self, email, role):
        return {"email": email, "role": role, "display_name": "Invited Person"}

    def _token(self, label="invite token is "):
        from django.core import mail

        body = mail.outbox[-1].body
        return body.split(label, 1)[1].split(".", 1)[0].strip()


@override_settings(DEBUG=False, EMAIL_CONFIGURED=False, EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
class UnconfiguredMailTests(TestCase):
    def test_failed_delivery_keeps_the_invite(self):
        principal = _staff("samuel.kollie@gojo.test", "principal")
        response = post_json(
            self.client,
            INVITE,
            {"email": "later@gojo.test", "role": "teacher", "display_name": "Later"},
            HTTP_AUTHORIZATION=bearer_for(principal, "principal"),
        )
        self.assertEqual(response.status_code, 201, response.content)
        self.assertEqual(response.json()["delivery"], NotificationLog.Status.UNCONFIGURED)
        self.assertNotIn("token", response.content.decode())
        self.assertTrue(User.objects.filter(email="later@gojo.test", is_active=False).exists())
