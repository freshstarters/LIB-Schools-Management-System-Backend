"""Student directory, IDs, pickup phones, and teacher conduct scope."""

from datetime import date

from django.test import TestCase, override_settings

from core.models import RoleMembership, User
from demo.models import Campus, Classroom, ConductNote, Student
from demo.service.class_service import ensure_classrooms, ensure_subjects
from demo.service.school_service import ensure_school_profile

STUDENTS = "/api/demo/v1/students/"


def _person(email, role, campus=None):
    user = User.objects.create_user(
        email,
        "password-123",
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


@override_settings(
    STORAGE_BACKEND="local",
    DEBUG=True,
    MEDIA_ROOT="/tmp/gojo-test-media",
    STORAGES={
        "default": {
            "BACKEND": "django.core.files.storage.FileSystemStorage",
            "OPTIONS": {"location": "/tmp/gojo-test-media", "base_url": "/media/"},
        },
        "staticfiles": {
            "BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage",
        },
    },
)
class StudentDirectoryTests(TestCase):
    def setUp(self):
        ensure_school_profile()
        ensure_subjects()
        ensure_classrooms()
        self.principal = _person("samuel.kollie@gojo.test", "principal")
        self.registrar = _person("beatrice.toe@gojo.test", "registrar")
        self.teacher_a = _person("grace.johnson@gojo.test", "teacher")
        self.teacher_b = _person("aaron.cooper@gojo.test", "teacher")
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

    def _enroll(self, name="Comfort Kollie", classroom=None, **extra):
        classroom = classroom or self.classroom_a
        body = {
            "full_name": name,
            "campus_id": str(self.paynesville.pk),
            "classroom_id": str(classroom.pk),
            "date_of_birth": "2014-05-01",
            "gender": "female",
            **extra,
        }
        response = self.client.post(
            STUDENTS,
            data=body,
            content_type="application/json",
            **_auth(self.registrar, "registrar"),
        )
        self.assertEqual(response.status_code, 201, response.content)
        return response.json()

    def test_enroll_generates_unique_gi_code(self):
        first = self._enroll("Prince Toe")
        second = self._enroll("Mary Wreh")
        year = date.today().year
        self.assertRegex(first["student_code"], rf"^GI-{year}-\d{{4}}$")
        self.assertRegex(second["student_code"], rf"^GI-{year}-\d{{4}}$")
        self.assertNotEqual(first["student_code"], second["student_code"])
        listed = self.client.get(STUDENTS, **_auth(self.registrar, "registrar"))
        self.assertEqual(listed.status_code, 200)
        codes = {row["student_code"] for row in listed.json()["results"]}
        self.assertIn(first["student_code"], codes)
        self.assertIn(second["student_code"], codes)

    def test_pickup_phone_normalizes_to_plus_231(self):
        student = self._enroll()
        response = self.client.post(
            f"{STUDENTS}{student['id']}/pickups/",
            data={
                "name": "Moses Toe",
                "relationship": "Uncle",
                "phone": "0770123456",
                "id_note": "National ID",
            },
            content_type="application/json",
            **_auth(self.registrar, "registrar"),
        )
        self.assertEqual(response.status_code, 201, response.content)
        self.assertEqual(response.json()["phone"], "+231770123456")

    def test_teacher_conduct_forbidden_for_unassigned_class(self):
        student = self._enroll(classroom=self.classroom_b)
        denied = self.client.post(
            f"{STUDENTS}{student['id']}/conduct/",
            data={
                "note_type": "merit",
                "severity": "low",
                "description": "Good effort",
            },
            content_type="application/json",
            **_auth(self.teacher_a, "teacher"),
        )
        self.assertEqual(denied.status_code, 403)

        self.classroom_b.homeroom_teacher = self.teacher_b
        self.classroom_b.save(update_fields=["homeroom_teacher"])
        allowed = self.client.post(
            f"{STUDENTS}{student['id']}/conduct/",
            data={
                "note_type": "homeroom_note",
                "severity": "medium",
                "description": "Settled in well",
            },
            content_type="application/json",
            **_auth(self.teacher_b, "teacher"),
        )
        self.assertEqual(allowed.status_code, 201, allowed.content)
        self.assertEqual(ConductNote.objects.filter(student_id=student["id"]).count(), 1)

    def test_withdraw_clears_classroom_and_snapshots_homeroom(self):
        student = self._enroll()
        response = self.client.post(
            f"{STUDENTS}{student['id']}/withdraw/",
            data={},
            content_type="application/json",
            **_auth(self.principal, "principal"),
        )
        self.assertEqual(response.status_code, 200, response.content)
        body = response.json()
        self.assertEqual(body["status"], "withdrawn")
        self.assertIsNone(body["classroom_id"])
        row = Student.objects.get(pk=student["id"])
        self.assertEqual(row.last_homeroom_teacher_id, self.teacher_a.pk)
