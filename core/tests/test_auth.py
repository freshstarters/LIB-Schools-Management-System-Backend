"""Email and Liberian phone sign-in, refresh cookies, and Google sign-in."""

import uuid
from unittest.mock import patch

from django.core.cache import cache
from django.core.exceptions import ValidationError
from django.test import Client, SimpleTestCase, TestCase, override_settings
from rest_framework.settings import api_settings

from core.helpers.phone import InvalidLiberianPhone, normalize_liberian_phone
from core.models import TenantSchool, User
from core.tenancy.claims import decode_access_token
from core.tests.support import bearer

PASSWORD = "school-pass-1"
DEMO_LOGIN = "/api/demo/v1/auth/login/"
DEMO_REFRESH = "/api/demo/v1/auth/refresh/"
DEMO_LOGOUT = "/api/demo/v1/auth/logout/"
DEMO_ME = "/api/demo/v1/auth/me/"
DEMO_GOOGLE = "/api/demo/v1/auth/google/"
DEMO_CONFIG = "/api/demo/v1/auth/config/"
DEMO_SESSIONS = "/api/demo/v1/auth/sessions/"
CORE_LOGIN = "/api/v1/auth/login/"
CORE_REFRESH = "/api/v1/auth/refresh/"
CORE_LOGOUT = "/api/v1/auth/logout/"
CORE_ME = "/api/v1/auth/me/"
CORE_GOOGLE = "/api/v1/auth/google/"
CORE_CONFIG = "/api/v1/auth/config/"
CORE_SESSIONS = "/api/v1/auth/sessions/"
PLATFORM_LOGIN = "/api/v1/auth/platform/login/"
PLATFORM_REFRESH = "/api/v1/auth/platform/refresh/"
PLATFORM_GOOGLE = "/api/v1/auth/platform/google/"

LOCATIONS = {
    "default": {
        "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
        "LOCATION": "auth-tests",
    }
}


def post_json(client, url, payload, **extra):
    return client.post(
        url,
        data=payload,
        content_type="application/json",
        **extra,
    )


def make_school(slug, *, status=TenantSchool.Status.ACTIVE):
    return TenantSchool.objects.create(slug=slug, name=slug.title(), status=status)


def make_user(email, *, app_scope, school=None, phone=None, password=PASSWORD, active=True):
    return User.objects.create_user(
        email,
        password,
        app_scope=app_scope,
        school=school,
        phone=phone,
        is_active=active,
    )


class PhoneTests(SimpleTestCase):
    def test_local_and_international_forms_match(self):
        expected = "+231770123456"
        for raw in ("0770123456", "770123456", "231770123456", "+231770123456", "+231 77-012.3456"):
            with self.subTest(raw=raw):
                self.assertEqual(normalize_liberian_phone(raw), expected)

    def test_other_countries_and_lengths_are_rejected(self):
        for raw in ("+14155552671", "077012345", "07701234567", "abc", "", None):
            with self.subTest(raw=raw):
                with self.assertRaises(InvalidLiberianPhone):
                    normalize_liberian_phone(raw)


@override_settings(
    CACHES=LOCATIONS,
    TENANCY_ROUTING_MODE="header_path",
    GOOGLE_OAUTH_CLIENT_ID="",
    GOOGLE_OAUTH_CLIENT_SECRET="",
)
class AuthTests(TestCase):
    def setUp(self):
        cache.clear()

    def test_same_email_can_exist_in_each_bucket(self):
        school = make_school("alpha")
        other = make_school("beta")
        make_user("ada@gojo.test", app_scope=User.AppScope.DEMO)
        make_user("ada@gojo.test", app_scope=User.AppScope.CORE, school=school)
        make_user("ada@gojo.test", app_scope=User.AppScope.CORE)
        make_user("ada@gojo.test", app_scope=User.AppScope.CORE, school=other)
        with self.assertRaises(ValidationError):
            make_user("ada@gojo.test", app_scope=User.AppScope.DEMO)
        with self.assertRaises(ValidationError):
            make_user("ada@gojo.test", app_scope=User.AppScope.CORE, school=other)

    def test_gojo_account_cannot_carry_a_school(self):
        school = make_school("alpha")
        with self.assertRaises(ValidationError):
            make_user("ada@gojo.test", app_scope=User.AppScope.DEMO, school=school)

    def test_phone_is_stored_in_e164(self):
        user = make_user(
            "ada@gojo.test",
            app_scope=User.AppScope.DEMO,
            phone="0770123456",
        )
        self.assertEqual(user.phone, "+231770123456")
        self.assertEqual(user.username, "demo:ada@gojo.test")

    def test_email_and_phone_login_issue_a_demo_token(self):
        make_user(
            "Ada@Gojo.test",
            app_scope=User.AppScope.DEMO,
            phone="0770123456",
        )
        by_email = post_json(self.client, DEMO_LOGIN, {"login": "ada@gojo.test", "password": PASSWORD})
        self.assertEqual(by_email.status_code, 200, by_email.content)
        claims = decode_access_token(by_email.json()["access"])
        self.assertEqual(claims.app_scope, "demo")
        self.assertEqual(claims.tenant_slug, "")
        self.assertEqual(claims.role, "")
        self.assertEqual(by_email.json()["expires_in"], 900)
        self.assertNotIn(self.client.cookies["refresh_demo"].value, by_email.content.decode())
        cookie = self.client.cookies["refresh_demo"]
        self.assertEqual(cookie["path"], "/api/demo/v1/auth/")
        self.assertTrue(cookie["httponly"])
        self.assertEqual(cookie["samesite"], "Lax")
        self.assertEqual(int(cookie["max-age"]), 604800)
        self.assertEqual(cookie["domain"], "")

        self.client.cookies.clear()
        by_phone = post_json(self.client, DEMO_LOGIN, {"login": "0770 123 456", "password": PASSWORD})
        self.assertEqual(by_phone.status_code, 200, by_phone.content)
        self.assertEqual(by_phone.json()["user"]["phone"], "+231770123456")

    def test_failed_login_uses_one_message(self):
        make_user("ada@gojo.test", app_scope=User.AppScope.DEMO, active=False)
        inactive = post_json(self.client, DEMO_LOGIN, {"login": "ada@gojo.test", "password": PASSWORD})
        wrong = post_json(self.client, DEMO_LOGIN, {"login": "ada@gojo.test", "password": "nope"})
        missing = post_json(self.client, DEMO_LOGIN, {"login": "nobody@gojo.test", "password": PASSWORD})
        school = make_school("alpha")
        foreign = post_json(
            self.client,
            CORE_LOGIN,
            {"login": "ada@gojo.test", "password": PASSWORD},
            HTTP_X_TENANT_SLUG=school.slug,
        )
        for response in (inactive, wrong, missing, foreign):
            self.assertEqual(response.status_code, 401)
            self.assertEqual(response.json()["code"], "authentication_failed")
            self.assertEqual(response.json()["detail"], "Authentication credentials were not valid.")

    def test_school_login_requires_a_header_and_rejects_other_scopes(self):
        school = make_school("alpha")
        other = make_school("beta")
        make_user("ada@gojo.test", app_scope=User.AppScope.CORE, school=school)
        missing = post_json(self.client, CORE_LOGIN, {"login": "ada@gojo.test", "password": PASSWORD})
        self.assertEqual(missing.status_code, 400)
        self.assertEqual(missing.json()["code"], "tenant_header_required")

        unknown = post_json(
            self.client,
            CORE_LOGIN,
            {"login": "ada@gojo.test", "password": PASSWORD},
            HTTP_X_TENANT_SLUG="missing-school",
        )
        self.assertEqual(unknown.status_code, 404)
        self.assertEqual(unknown.json()["code"], "tenant_not_found")

        logged_in = post_json(
            self.client,
            CORE_LOGIN,
            {"login": "ada@gojo.test", "password": PASSWORD},
            HTTP_X_TENANT_SLUG=school.slug,
        )
        self.assertEqual(logged_in.status_code, 200, logged_in.content)
        token = logged_in.json()["access"]
        claims = decode_access_token(token)
        self.assertEqual(claims.app_scope, "core")
        self.assertEqual(claims.tenant_slug, school.slug)
        self.assertEqual(self.client.cookies["refresh_alpha"]["path"], "/api/v1/auth/")

        demo_me = self.client.get(DEMO_ME, HTTP_AUTHORIZATION=f"Bearer {token}")
        self.assertEqual(demo_me.status_code, 403)
        self.assertEqual(demo_me.json()["code"], "app_scope_rejected")

        platform = self.client.get("/api/v1/platform/ping/", HTTP_AUTHORIZATION=f"Bearer {token}")
        self.assertEqual(platform.status_code, 403)

        other_school = self.client.get(
            "/api/v1/divisions/",
            HTTP_X_TENANT_SLUG=other.slug,
            HTTP_AUTHORIZATION=f"Bearer {token}",
        )
        self.assertEqual(other_school.status_code, 403)
        self.assertEqual(other_school.json()["code"], "tenant_mismatch")

        me = self.client.get(
            CORE_ME,
            HTTP_AUTHORIZATION=f"Bearer {token}",
            HTTP_X_TENANT_SLUG=school.slug,
        )
        self.assertEqual(me.status_code, 200)
        self.assertEqual(me.json()["user"]["account"], "school")

    def test_suspended_school_cannot_sign_in(self):
        school = make_school("alpha", status=TenantSchool.Status.SUSPENDED)
        make_user("ada@gojo.test", app_scope=User.AppScope.CORE, school=school)
        response = post_json(
            self.client,
            CORE_LOGIN,
            {"login": "ada@gojo.test", "password": PASSWORD},
            HTTP_X_TENANT_SLUG=school.slug,
        )
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json()["code"], "tenant_unavailable")

    def test_platform_login_rejects_a_school_header(self):
        make_user("root@gojo.test", app_scope=User.AppScope.CORE)
        rejected = post_json(
            self.client,
            PLATFORM_LOGIN,
            {"login": "root@gojo.test", "password": PASSWORD},
            HTTP_X_TENANT_SLUG="alpha",
        )
        self.assertEqual(rejected.status_code, 400)
        self.assertEqual(rejected.json()["code"], "tenant_header_rejected")

        logged_in = post_json(
            self.client,
            PLATFORM_LOGIN,
            {"login": "root@gojo.test", "password": PASSWORD},
        )
        self.assertEqual(logged_in.status_code, 200, logged_in.content)
        token = logged_in.json()["access"]
        self.assertEqual(decode_access_token(token).tenant_slug, "")
        self.assertEqual(logged_in.json()["user"]["account"], "platform")
        self.assertIn("refresh_platform", self.client.cookies)

        demo_me = self.client.get(DEMO_ME, HTTP_AUTHORIZATION=f"Bearer {token}")
        self.assertEqual(demo_me.status_code, 403)
        core_me = self.client.get(CORE_ME, HTTP_AUTHORIZATION=f"Bearer {token}")
        self.assertEqual(core_me.status_code, 200)

        demo_user = make_user("ada@gojo.test", app_scope=User.AppScope.DEMO)
        demo_login = post_json(self.client, DEMO_LOGIN, {"login": demo_user.email, "password": PASSWORD})
        demo_token = demo_login.json()["access"]
        rejected_me = self.client.get(CORE_ME, HTTP_AUTHORIZATION=f"Bearer {demo_token}")
        self.assertEqual(rejected_me.status_code, 403)
        self.assertEqual(rejected_me.json()["code"], "app_scope_rejected")

    @override_settings(
        TENANCY_ROUTING_MODE="subdomain",
        TENANCY_BASE_DOMAIN="gosms.test",
        ALLOWED_HOSTS=["testserver", "localhost", ".gosms.test"],
    )
    def test_subdomain_selects_the_school_or_platform_host(self):
        school = make_school("alpha")
        make_user("ada@gojo.test", app_scope=User.AppScope.CORE, school=school)
        make_user("root@gojo.test", app_scope=User.AppScope.CORE)
        school_login = post_json(
            self.client,
            CORE_LOGIN,
            {"login": "ada@gojo.test", "password": PASSWORD},
            HTTP_HOST="alpha.gosms.test",
        )
        self.assertEqual(school_login.status_code, 200, school_login.content)
        self.assertEqual(decode_access_token(school_login.json()["access"]).tenant_slug, "alpha")
        school_token = school_login.json()["access"]
        wrong_me = self.client.get(
            CORE_ME,
            HTTP_HOST="beta.gosms.test",
            HTTP_AUTHORIZATION=f"Bearer {school_token}",
        )
        self.assertEqual(wrong_me.status_code, 403)
        self.assertEqual(wrong_me.json()["code"], "tenant_mismatch")
        right_me = self.client.get(
            CORE_ME,
            HTTP_HOST="alpha.gosms.test",
            HTTP_AUTHORIZATION=f"Bearer {school_token}",
        )
        self.assertEqual(right_me.status_code, 200)

        platform_login = post_json(
            self.client,
            PLATFORM_LOGIN,
            {"login": "root@gojo.test", "password": PASSWORD},
            HTTP_HOST="platform.gosms.test",
        )
        self.assertEqual(platform_login.status_code, 200, platform_login.content)

        wrong_host = post_json(
            self.client,
            PLATFORM_LOGIN,
            {"login": "root@gojo.test", "password": PASSWORD},
            HTTP_HOST="alpha.gosms.test",
        )
        self.assertEqual(wrong_host.status_code, 404)
        self.assertEqual(wrong_host.json()["code"], "tenant_not_found")
        platform_token = platform_login.json()["access"]
        platform_me = self.client.get(
            CORE_ME,
            HTTP_HOST="platform.gosms.test",
            HTTP_AUTHORIZATION=f"Bearer {platform_token}",
        )
        self.assertEqual(platform_me.status_code, 200)
        platform_on_school = self.client.get(
            CORE_ME,
            HTTP_HOST="alpha.gosms.test",
            HTTP_AUTHORIZATION=f"Bearer {platform_token}",
        )
        self.assertEqual(platform_on_school.status_code, 403)
        self.assertEqual(platform_on_school.json()["code"], "tenant_mismatch")

    def test_stale_bearer_does_not_block_public_gojo_auth(self):
        config = self.client.get(DEMO_CONFIG, HTTP_AUTHORIZATION="Bearer not-a-token")
        self.assertEqual(config.status_code, 200)
        foreign = self.client.get(DEMO_CONFIG, HTTP_AUTHORIZATION=bearer("core", "alpha"))
        self.assertEqual(foreign.status_code, 403)
        self.assertEqual(foreign.json()["code"], "app_scope_rejected")

        client = Client(enforce_csrf_checks=True)
        login = post_json(
            client,
            DEMO_LOGIN,
            {"login": "nobody@gojo.test", "password": PASSWORD},
            HTTP_AUTHORIZATION="Bearer not-a-token",
        )
        self.assertEqual(login.status_code, 403)
        self.assertEqual(login.json()["code"], "csrf_failed")

        me = self.client.get(DEMO_ME, HTTP_AUTHORIZATION="Bearer not-a-token")
        self.assertEqual(me.status_code, 401)
        self.assertEqual(me.json()["code"], "authentication_failed")

    def test_refresh_rotates_only_the_selected_cookie(self):
        alpha = make_school("alpha")
        beta = make_school("beta")
        make_user("ada@gojo.test", app_scope=User.AppScope.CORE, school=alpha)
        make_user("ada@gojo.test", app_scope=User.AppScope.CORE, school=beta)
        post_json(
            self.client,
            CORE_LOGIN,
            {"login": "ada@gojo.test", "password": PASSWORD},
            HTTP_X_TENANT_SLUG="alpha",
        )
        alpha_before = self.client.cookies["refresh_alpha"].value
        post_json(
            self.client,
            CORE_LOGIN,
            {"login": "ada@gojo.test", "password": PASSWORD},
            HTTP_X_TENANT_SLUG="beta",
        )
        beta_before = self.client.cookies["refresh_beta"].value

        refreshed = post_json(self.client, CORE_REFRESH, {}, HTTP_X_TENANT_SLUG="alpha")
        self.assertEqual(refreshed.status_code, 200, refreshed.content)
        self.assertNotEqual(self.client.cookies["refresh_alpha"].value, alpha_before)
        self.assertEqual(self.client.cookies["refresh_beta"].value, beta_before)
        self.assertEqual(decode_access_token(refreshed.json()["access"]).tenant_slug, "alpha")

        sessions = self.client.get(CORE_SESSIONS)
        self.assertEqual(sessions.status_code, 200)
        slugs = {item["tenant_slug"] for item in sessions.json()["sessions"]}
        self.assertEqual(slugs, {"alpha", "beta"})

    def test_reused_refresh_token_revokes_every_device_for_that_account(self):
        make_user("ada@gojo.test", app_scope=User.AppScope.DEMO)
        post_json(self.client, DEMO_LOGIN, {"login": "ada@gojo.test", "password": PASSWORD})
        first = self.client.cookies["refresh_demo"].value
        post_json(self.client, DEMO_LOGIN, {"login": "ada@gojo.test", "password": PASSWORD})
        second = self.client.cookies["refresh_demo"].value
        rotated = post_json(self.client, DEMO_REFRESH, {})
        self.assertEqual(rotated.status_code, 200, rotated.content)
        current = self.client.cookies["refresh_demo"].value

        self.client.cookies["refresh_demo"] = second
        reused = post_json(self.client, DEMO_REFRESH, {})
        self.assertEqual(reused.status_code, 401)

        self.client.cookies["refresh_demo"] = current
        after = post_json(self.client, DEMO_REFRESH, {})
        self.assertEqual(after.status_code, 401)
        self.assertNotEqual(first, second)

    def test_logout_ends_only_this_browser_session(self):
        make_user("ada@gojo.test", app_scope=User.AppScope.DEMO)
        post_json(self.client, DEMO_LOGIN, {"login": "ada@gojo.test", "password": PASSWORD})
        phone = self.client.cookies["refresh_demo"].value
        post_json(self.client, DEMO_LOGIN, {"login": "ada@gojo.test", "password": PASSWORD})
        logged_out = post_json(self.client, DEMO_LOGOUT, {})
        self.assertEqual(logged_out.status_code, 204)
        self.assertEqual(self.client.cookies["refresh_demo"].value, "")

        self.client.cookies["refresh_demo"] = phone
        still = post_json(self.client, DEMO_REFRESH, {})
        self.assertEqual(still.status_code, 200, still.content)

    def test_sessions_list_is_empty_until_a_cookie_is_valid(self):
        empty = self.client.get(DEMO_SESSIONS)
        self.assertEqual(empty.status_code, 200)
        self.assertEqual(empty.json()["sessions"], [])

        make_user("ada@gojo.test", app_scope=User.AppScope.DEMO)
        post_json(self.client, DEMO_LOGIN, {"login": "ada@gojo.test", "password": PASSWORD})
        listed = self.client.get(DEMO_SESSIONS)
        self.assertEqual(listed.json()["sessions"], [
            {
                "account": "demo",
                "id": listed.json()["sessions"][0]["id"],
                "session_id": listed.json()["sessions"][0]["session_id"],
                "email": "ada@gojo.test",
                "tenant_slug": "",
                "issued_at": listed.json()["sessions"][0]["issued_at"],
            }
        ])
        self.assertTrue(listed.json()["sessions"][0]["issued_at"])
        self.assertNotEqual(
            listed.json()["sessions"][0]["session_id"],
            listed.json()["sessions"][0]["id"],
        )
        self.assertTrue(uuid.UUID(listed.json()["sessions"][0]["id"]))
        core = self.client.get(CORE_SESSIONS)
        self.assertEqual(core.json()["sessions"], [])

    def test_config_hides_secrets_and_warns_when_unconfigured(self):
        with override_settings(HOSTING_REGION="", DATA_CONTROLLER_NAME="", SECRET_KEY="test-secret-key"):
            response = self.client.get(CORE_CONFIG)
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertFalse(body["google_enabled"])
        self.assertFalse(body["privacy_notice"]["configured"])
        self.assertIn("not configured", body["privacy_notice"]["warning"])
        self.assertIn("opt in or out of SMS", body["privacy_notice"]["sms_consent"])
        self.assertTrue(body["csrf_token"])
        rendered = response.content.decode()
        self.assertNotIn("test-secret-key", rendered)
        self.assertNotIn("GOOGLE_OAUTH_CLIENT_SECRET", rendered)

        with override_settings(HOSTING_REGION="eu-west-1", DATA_CONTROLLER_NAME="Gojo Institute"):
            configured = self.client.get(DEMO_CONFIG)
        notice = configured.json()["privacy_notice"]
        self.assertTrue(notice["configured"])
        self.assertEqual(notice["warning"], "")
        self.assertEqual(notice["hosting_region"], "eu-west-1")
        self.assertEqual(notice["data_controller_name"], "Gojo Institute")

    def test_csrf_is_required_for_login(self):
        make_user("ada@gojo.test", app_scope=User.AppScope.DEMO)
        client = Client(enforce_csrf_checks=True)
        missing = post_json(client, DEMO_LOGIN, {"login": "ada@gojo.test", "password": PASSWORD})
        self.assertEqual(missing.status_code, 403)
        self.assertEqual(missing.json()["code"], "csrf_failed")

        config = client.get(DEMO_CONFIG)
        signed_in = post_json(
            client,
            DEMO_LOGIN,
            {"login": "ada@gojo.test", "password": PASSWORD},
            HTTP_X_CSRFTOKEN=config.json()["csrf_token"],
        )
        self.assertEqual(signed_in.status_code, 200, signed_in.content)

    def test_login_is_rate_limited(self):
        rates = api_settings.DEFAULT_THROTTLE_RATES
        original = rates["auth_login"]
        rates["auth_login"] = "2/min"
        try:
            cache.clear()
            payload = {"login": "ada@gojo.test", "password": "nope"}
            first = post_json(self.client, DEMO_LOGIN, payload)
            second = post_json(self.client, DEMO_LOGIN, payload)
            third = post_json(self.client, DEMO_LOGIN, payload)
        finally:
            rates["auth_login"] = original
        self.assertEqual(first.status_code, 401)
        self.assertEqual(second.status_code, 401)
        self.assertEqual(third.status_code, 429)
        self.assertEqual(third.json()["code"], "throttled")

    def test_google_fails_closed_until_both_credentials_exist(self):
        with patch("google.oauth2.id_token.verify_oauth2_token") as verify:
            missing_both = post_json(self.client, DEMO_GOOGLE, {"id_token": "token"})
            self.assertEqual(missing_both.status_code, 503)
            self.assertEqual(missing_both.json()["code"], "service_unavailable")
            verify.assert_not_called()

        with override_settings(GOOGLE_OAUTH_CLIENT_ID="client-id", GOOGLE_OAUTH_CLIENT_SECRET=""):
            with patch("google.oauth2.id_token.verify_oauth2_token") as verify:
                missing_secret = post_json(self.client, CORE_GOOGLE, {"id_token": "token"})
                self.assertEqual(missing_secret.status_code, 503)
                verify.assert_not_called()

    @override_settings(
        GOOGLE_OAUTH_CLIENT_ID="client-id",
        GOOGLE_OAUTH_CLIENT_SECRET="client-secret",
    )
    def test_google_matches_an_existing_verified_account(self):
        make_user("ada@gojo.test", app_scope=User.AppScope.DEMO)
        claims = {
            "email": "Ada@Gojo.test",
            "email_verified": True,
            "iss": "accounts.google.com",
        }
        with patch("google.oauth2.id_token.verify_oauth2_token", return_value=claims):
            signed_in = post_json(self.client, DEMO_GOOGLE, {"id_token": "token"})
        self.assertEqual(signed_in.status_code, 200, signed_in.content)
        self.assertEqual(signed_in.json()["user"]["email"], "ada@gojo.test")
        self.assertNotIn("client-secret", signed_in.content.decode())
        config = self.client.get(DEMO_CONFIG)
        self.assertTrue(config.json()["google_enabled"])
        self.assertNotIn("client-secret", config.content.decode())
        self.assertNotIn("client-id", config.content.decode())

    @override_settings(
        GOOGLE_OAUTH_CLIENT_ID="client-id",
        GOOGLE_OAUTH_CLIENT_SECRET="client-secret",
    )
    def test_google_rejects_unverified_or_unknown_email(self):
        before = User.objects.count()
        unverified = {
            "email": "ada@gojo.test",
            "email_verified": False,
            "iss": "accounts.google.com",
        }
        with patch("google.oauth2.id_token.verify_oauth2_token", return_value=unverified):
            rejected = post_json(self.client, DEMO_GOOGLE, {"id_token": "token"})
        self.assertEqual(rejected.status_code, 401)
        self.assertEqual(User.objects.count(), before)

        make_user("ada@gojo.test", app_scope=User.AppScope.DEMO)
        unknown = {
            "email": "new@gojo.test",
            "email_verified": True,
            "iss": "https://accounts.google.com",
        }
        with patch("google.oauth2.id_token.verify_oauth2_token", return_value=unknown):
            missing = post_json(self.client, DEMO_GOOGLE, {"id_token": "token"})
        self.assertEqual(missing.status_code, 401)
        self.assertFalse(User.objects.filter(email="new@gojo.test").exists())

    @override_settings(DEBUG=False)
    def test_refresh_cookie_is_secure_outside_debug(self):
        make_user("ada@gojo.test", app_scope=User.AppScope.DEMO)
        post_json(self.client, DEMO_LOGIN, {"login": "ada@gojo.test", "password": PASSWORD})
        self.assertTrue(self.client.cookies["refresh_demo"]["secure"])

    @override_settings(DEBUG=True)
    def test_refresh_cookie_is_not_secure_in_debug(self):
        make_user("ada@gojo.test", app_scope=User.AppScope.DEMO)
        post_json(self.client, DEMO_LOGIN, {"login": "ada@gojo.test", "password": PASSWORD})
        self.assertFalse(self.client.cookies["refresh_demo"]["secure"])
