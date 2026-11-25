"""School profile, calendar, and integration health."""

from django.test import TestCase, override_settings

from core.models import RoleMembership, User
from core.tests.support import bearer_for
from demo.models import AcademicYear, Campus, MarkingPeriod, SchoolConfig
from demo.service.school_service import ensure_school_profile

SETTINGS = "/api/demo/v1/settings/"
CAMPUSES = "/api/demo/v1/campuses/"
YEARS = "/api/demo/v1/academic-years/"
STATUS = "/api/demo/v1/integrations/status/"


def _person(email, role):
    user = User.objects.create_user(email, "password-123", app_scope=User.AppScope.DEMO, display_name=email)
    RoleMembership.objects.create(user=user, role=role)
    return user


@override_settings(
    EMAIL_CONFIGURED=False,
    SMS_PROVIDER="",
    SMS_API_KEY="",
    SMS_USERNAME="",
    SMS_ACCOUNT_SID="",
    SMS_SENDER_ID="",
    WHATSAPP_PHONE_NUMBER_ID="",
    WHATSAPP_ACCESS_TOKEN="",
    WHATSAPP_VERIFY_TOKEN="",
    GOOGLE_OAUTH_CLIENT_ID="",
    GOOGLE_OAUTH_CLIENT_SECRET="",
    AI_PROVIDER="",
    AI_API_KEY="",
    AI_MODEL="",
    MTN_MOMO_ENV="",
    MTN_MOMO_SUBSCRIPTION_KEY="",
    MTN_MOMO_API_USER="",
    MTN_MOMO_API_KEY="",
    MTN_MOMO_WEBHOOK_SECRET="",
    ORANGE_MONEY_ENV="",
    ORANGE_MONEY_CLIENT_ID="",
    ORANGE_MONEY_CLIENT_SECRET="",
    ORANGE_MONEY_MERCHANT_KEY="",
    ORANGE_MONEY_WEBHOOK_SECRET="",
)
class SchoolSettingsTests(TestCase):
    def setUp(self):
        ensure_school_profile()
        self.principal = _person("samuel.kollie@gojo.test", "principal")
        self.bursar = _person("joseph.freeman@gojo.test", "bursar")
        self.registrar = _person("beatrice.toe@gojo.test", "registrar")
        self.teacher = _person("grace.johnson@gojo.test", "teacher")

    def test_seeded_profile_uses_period_two(self):
        response = self.client.get(SETTINGS, **self._auth(self.principal, "principal"))
        self.assertEqual(response.status_code, 200, response.content)
        body = response.json()
        self.assertEqual(body["school_name"], "Gojo Institute")
        self.assertEqual(body["lrd_per_usd"], "192.00")
        period = MarkingPeriod.objects.get(pk=body["active_marking_period_id"])
        self.assertEqual(period.code, "P2")
        campuses = self.client.get(CAMPUSES, **self._auth(self.principal, "principal")).json()
        names = {row["name"] for row in campuses}
        self.assertEqual(
            names,
            {"Sinkor Main Campus", "Paynesville Campus", "Gardnersville Campus"},
        )

    def test_weights_and_rate_are_rejected_when_invalid(self):
        bad_weights = self.client.patch(
            SETTINGS,
            data={"marking_period_weight": "70", "semester_exam_weight": "20"},
            content_type="application/json",
            **self._auth(self.principal, "principal"),
        )
        self.assertEqual(bad_weights.status_code, 400)
        self.assertEqual(bad_weights.json()["fields"]["marking_period_weight"], ["Grading weights must sum to 100%."])
        bad_rate = self.client.patch(
            SETTINGS,
            data={"lrd_per_usd": "0"},
            content_type="application/json",
            **self._auth(self.principal, "principal"),
        )
        self.assertEqual(bad_rate.status_code, 400)
        self.assertEqual(SchoolConfig.objects.get().lrd_per_usd, 192)

    def test_exam_cannot_be_the_active_period(self):
        exam = MarkingPeriod.objects.get(code="SEM1_EXAM")
        response = self.client.patch(
            SETTINGS,
            data={"active_marking_period_id": str(exam.pk)},
            content_type="application/json",
            **self._auth(self.principal, "principal"),
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("Period 1", response.json()["fields"]["active_marking_period_id"][0])

    def test_bursar_can_change_the_rate_and_not_the_school_name(self):
        saved = self.client.patch(
            SETTINGS,
            data={"lrd_per_usd": "200.5", "pass_threshold": "70"},
            content_type="application/json",
            **self._auth(self.bursar, "bursar"),
        )
        self.assertEqual(saved.status_code, 200, saved.content)
        self.assertEqual(saved.json()["lrd_per_usd"], "200.50")
        mixed = self.client.patch(
            SETTINGS,
            data={"lrd_per_usd": "180", "school_name": "Other School"},
            content_type="application/json",
            **self._auth(self.bursar, "bursar"),
        )
        self.assertEqual(mixed.status_code, 403)
        self.assertEqual(SchoolConfig.objects.get().lrd_per_usd, 200.5)
        self.assertEqual(SchoolConfig.objects.get().school_name, "Gojo Institute")
        created = self.client.post(
            CAMPUSES,
            data={"name": "New Campus"},
            content_type="application/json",
            **self._auth(self.bursar, "bursar"),
        )
        self.assertEqual(created.status_code, 403)

    def test_registrar_and_teacher_cannot_write(self):
        for user, role in ((self.registrar, "registrar"), (self.teacher, "teacher")):
            response = self.client.patch(
                SETTINGS,
                data={"lrd_per_usd": "150"},
                content_type="application/json",
                **self._auth(user, role),
            )
            self.assertEqual(response.status_code, 403, role)

    def test_current_year_last_campus_and_assigned_campus_cannot_be_deleted(self):
        year = AcademicYear.objects.get(is_current=True)
        denied_year = self.client.delete(
            f"{YEARS}{year.pk}/",
            **self._auth(self.principal, "principal"),
        )
        self.assertEqual(denied_year.status_code, 400)
        campus = Campus.objects.get(code="sinkor")
        RoleMembership.objects.create(user=self.teacher, role="support_staff", campus_id=campus.pk)
        denied_campus = self.client.delete(
            f"{CAMPUSES}{campus.pk}/",
            **self._auth(self.principal, "principal"),
        )
        self.assertEqual(denied_campus.status_code, 400)
        self.assertIn("moved", denied_campus.json()["fields"]["campus"][0])
        empty = Campus.objects.create(code="annex", name="Empty Annex")
        removed = self.client.delete(
            f"{CAMPUSES}{empty.pk}/",
            **self._auth(self.principal, "principal"),
        )
        self.assertEqual(removed.status_code, 204)
        self.assertFalse(Campus.objects.filter(code="annex").exists())

    @override_settings(
        SMS_PROVIDER="example",
        SMS_API_KEY="sms-secret-value",
        SMS_USERNAME="gojo",
        SMS_SENDER_ID="GOJOINST",
        AI_API_KEY="ai-secret-value",
    )
    def test_integration_status_hides_secrets(self):
        response = self.client.get(STATUS, **self._auth(self.registrar, "registrar"))
        self.assertEqual(response.status_code, 200, response.content)
        rendered = response.content.decode()
        self.assertNotIn("sms-secret-value", rendered)
        self.assertNotIn("ai-secret-value", rendered)
        body = response.json()
        self.assertEqual(body["sms"]["status"], "configured")
        self.assertEqual(body["sms"]["sender_id"], "GOJOINST")
        self.assertEqual(body["ai"]["status"], "unconfigured")
        self.assertEqual(body["google"]["status"], "unconfigured")
        self.assertEqual(body["email_smtp"]["status"], "unconfigured")

    def _auth(self, user, role):
        return {"HTTP_AUTHORIZATION": bearer_for(user, role)}
