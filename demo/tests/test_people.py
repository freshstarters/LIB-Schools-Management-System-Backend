"""Staff and guardian directories, invites, soft-deactivate, sibling links."""

from django.test import TestCase, override_settings
from django.utils import timezone

from core.models import InviteToken, RoleMembership, User
from demo.models import (
    Campus,
    Classroom,
    GuardianProfile,
    StaffProfile,
    StudentGuardianLink,
    Subject,
    TimetableSlot,
)
from demo.service.class_service import ensure_classrooms, ensure_subjects
from demo.service.school_service import ensure_school_profile

TEACHERS = "/api/demo/v1/teachers/"
PARENTS = "/api/demo/v1/parents/"
STUDENTS = "/api/demo/v1/students/"


def _person(email, role, campus=None, *, password="password-123"):
    user = User.objects.create_user(
        email,
        password,
        app_scope=User.AppScope.DEMO,
        display_name=email.split("@")[0].replace(".", " ").title(),
    )
    RoleMembership.objects.create(
        user=user,
        role=role,
        campus_id=None if campus is None else campus.pk,
    )
    return user


def _auth(user, role):
    from core.tests.support import bearer_for

    return {"HTTP_AUTHORIZATION": bearer_for(user, role)}


@override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
class PeopleDirectoryTests(TestCase):
    def setUp(self):
        ensure_school_profile()
        ensure_subjects()
        ensure_classrooms()
        self.principal = _person("samuel.kollie@gojo.test", "principal")
        self.registrar = _person("beatrice.toe@gojo.test", "registrar")
        self.teacher_a = _person("grace.johnson@gojo.test", "teacher")
        self.paynesville = Campus.objects.get(code="paynesville")
        self.classroom_a = Classroom.objects.get(
            campus=self.paynesville,
            grade_level__name="Grade 6",
            section_name="A",
        )
        self.classroom_b = Classroom.objects.get(
            campus=self.paynesville,
            grade_level__name="Grade 6",
            section_name="B",
        )
        self.classroom_a.homeroom_teacher = self.teacher_a
        self.classroom_a.save(update_fields=["homeroom_teacher"])
        self.subject = Subject.objects.first()
        TimetableSlot.objects.create(
            classroom=self.classroom_a,
            day_of_week=TimetableSlot.Day.MONDAY,
            period_number=1,
            subject=self.subject,
            teacher=self.teacher_a,
            room_label="Main 202",
        )

    def _create_staff(self, **extra):
        body = {
            "full_name": "Aaron Cooper",
            "role": "teacher",
            "job_title": "Physics Teacher",
            "phone": "0775550019",
            "email": "aaron.cooper@gojo.test",
            "campus_id": str(self.paynesville.pk),
            **extra,
        }
        response = self.client.post(
            TEACHERS,
            data=body,
            content_type="application/json",
            **_auth(self.principal, "principal"),
        )
        self.assertEqual(response.status_code, 201, response.content)
        return response.json()

    def _create_guardian(self, **extra):
        body = {
            "full_name": "Mary Pewee",
            "phone": "0775550103",
            "email": "mary.pewee@gojo.test",
            "residential_zone": "Congo Town",
            **extra,
        }
        response = self.client.post(
            PARENTS,
            data=body,
            content_type="application/json",
            **_auth(self.registrar, "registrar"),
        )
        self.assertEqual(response.status_code, 201, response.content)
        return response.json()

    def _enroll(self, name, classroom):
        response = self.client.post(
            STUDENTS,
            data={
                "full_name": name,
                "campus_id": str(self.paynesville.pk),
                "classroom_id": str(classroom.pk),
                "date_of_birth": "2014-05-01",
                "gender": "female",
            },
            content_type="application/json",
            **_auth(self.registrar, "registrar"),
        )
        self.assertEqual(response.status_code, 201, response.content)
        return response.json()

    def test_staff_phone_normalizes_and_email_required(self):
        row = self._create_staff()
        self.assertEqual(row["phone"], "+231775550019")
        self.assertRegex(row["staff_code"], r"^STF-\d{4}-\d{4}$")
        denied = self.client.post(
            TEACHERS,
            data={
                "full_name": "No Email",
                "role": "teacher",
                "phone": "0775550099",
            },
            content_type="application/json",
            **_auth(self.principal, "principal"),
        )
        self.assertEqual(denied.status_code, 400)

    def test_registrar_cannot_write_staff(self):
        response = self.client.post(
            TEACHERS,
            data={
                "full_name": "Blocked",
                "role": "teacher",
                "phone": "0775550088",
                "email": "blocked@gojo.test",
            },
            content_type="application/json",
            **_auth(self.registrar, "registrar"),
        )
        self.assertEqual(response.status_code, 403)

    def test_deactivate_preserves_timetable_teacher(self):
        profile = StaffProfile.objects.create(
            user=self.teacher_a,
            staff_code="STF-2026-0001",
            full_name=self.teacher_a.display_name,
            role=StaffProfile.Role.TEACHER,
            phone="+231886550012",
            email=self.teacher_a.email,
        )
        slot = TimetableSlot.objects.get(teacher=self.teacher_a)
        response = self.client.post(
            f"{TEACHERS}{profile.pk}/deactivate/",
            data={},
            content_type="application/json",
            **_auth(self.principal, "principal"),
        )
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response.json()["employment_status"], "inactive")
        self.assertEqual(response.json()["portal_status"], "inactive")
        slot.refresh_from_db()
        self.assertEqual(slot.teacher_id, self.teacher_a.pk)
        self.teacher_a.refresh_from_db()
        self.assertFalse(self.teacher_a.is_active)

    def test_send_invite_creates_token_and_invited_badge(self):
        row = self._create_staff(email="invite.staff@gojo.test")
        response = self.client.post(
            f"{TEACHERS}{row['id']}/send-invite/",
            data={},
            content_type="application/json",
            **_auth(self.principal, "principal"),
        )
        self.assertEqual(response.status_code, 200, response.content)
        body = response.json()
        self.assertEqual(body["portal_status"], "invited")
        profile = StaffProfile.objects.get(pk=row["id"])
        self.assertIsNotNone(profile.user_id)
        self.assertTrue(
            InviteToken.objects.filter(
                user_id=profile.user_id,
                used_at__isnull=True,
                expires_at__gt=timezone.now(),
            ).exists()
        )

    def test_guardian_siblings_and_primary_and_null_balance(self):
        guardian = self._create_guardian()
        child_a = self._enroll("Comfort Pewee", self.classroom_a)
        child_b = self._enroll("Daniel Kroma", self.classroom_b)
        first = self.client.post(
            f"{PARENTS}{guardian['id']}/children/",
            data={
                "student_id": child_a["id"],
                "relationship": "mother",
            },
            content_type="application/json",
            **_auth(self.registrar, "registrar"),
        )
        self.assertEqual(first.status_code, 201, first.content)
        self.assertTrue(first.json()["is_primary"])
        second = self.client.post(
            f"{PARENTS}{guardian['id']}/children/",
            data={
                "student_id": child_b["id"],
                "relationship": "guardian",
                "is_financial_responsible": True,
            },
            content_type="application/json",
            **_auth(self.registrar, "registrar"),
        )
        self.assertEqual(second.status_code, 201, second.content)
        self.assertTrue(second.json()["is_primary"])
        detail = self.client.get(
            f"{PARENTS}{guardian['id']}/",
            **_auth(self.registrar, "registrar"),
        )
        self.assertEqual(detail.status_code, 200)
        body = detail.json()
        self.assertEqual(len(body["children"]), 2)
        self.assertIsNone(body["family_balance_lrd"])
        names = {row["student_name"] for row in body["children"]}
        self.assertEqual(names, {"Comfort Pewee", "Daniel Kroma"})

        teacher_view = self.client.get(
            f"{PARENTS}{guardian['id']}/",
            **_auth(self.teacher_a, "teacher"),
        )
        self.assertEqual(teacher_view.status_code, 200)
        self.assertNotIn("family_balance_lrd", teacher_view.json())

    def test_inactive_guardian_rejects_new_link(self):
        guardian = self._create_guardian(email="inactive.parent@gojo.test")
        child_a = self._enroll("Faith Mulbah", self.classroom_a)
        child_b = self._enroll("Prince Toe", self.classroom_b)
        self.client.post(
            f"{PARENTS}{guardian['id']}/children/",
            data={"student_id": child_a["id"], "relationship": "mother"},
            content_type="application/json",
            **_auth(self.registrar, "registrar"),
        )
        self.client.post(
            f"{PARENTS}{guardian['id']}/children/",
            data={"student_id": child_b["id"], "relationship": "mother"},
            content_type="application/json",
            **_auth(self.registrar, "registrar"),
        )
        deactivated = self.client.post(
            f"{PARENTS}{guardian['id']}/deactivate/",
            data={},
            content_type="application/json",
            **_auth(self.registrar, "registrar"),
        )
        self.assertEqual(deactivated.status_code, 200, deactivated.content)
        self.assertEqual(
            StudentGuardianLink.objects.filter(guardian_id=guardian["id"]).count(),
            2,
        )
        child_c = self._enroll("Esther Varney", self.classroom_a)
        blocked = self.client.post(
            f"{PARENTS}{guardian['id']}/children/",
            data={"student_id": child_c["id"], "relationship": "mother"},
            content_type="application/json",
            **_auth(self.registrar, "registrar"),
        )
        self.assertEqual(blocked.status_code, 400)

    def test_guardian_phone_normalizes(self):
        row = self._create_guardian(phone="0886550102", whatsapp_phone="0775550102")
        self.assertEqual(row["phone"], "+231886550102")
        self.assertEqual(row["whatsapp_phone"], "+231775550102")
        self.assertTrue(row["sms_consent"])
        self.assertEqual(GuardianProfile.objects.count(), 1)
