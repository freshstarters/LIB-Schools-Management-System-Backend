"""Classrooms, MoE subjects, timetable conflicts, and substitutions."""

from datetime import date, timedelta

from django.test import TestCase

from core.models import RoleMembership, User
from demo.models import Campus, Classroom, Subject, TimetableSlot
from demo.service.class_service import ensure_classrooms, ensure_subjects
from demo.service.school_service import ensure_school_profile
from demo.service.timetable_service import WEEKDAY_TO_DAY, monrovia_today

CLASSES = "/api/demo/v1/classes/"
SUBJECTS = "/api/demo/v1/subjects/"
TIMETABLE = "/api/demo/v1/timetable/"
UNCOVERED = "/api/demo/v1/timetable/uncovered/"
ABSENCES = "/api/demo/v1/timetable/absences/"
SUBSTITUTIONS = "/api/demo/v1/timetable/substitutions/"
TEACHERS = "/api/demo/v1/classes/teachers/"


def _person(email, role, campus=None):
    user = User.objects.create_user(
        email,
        "password-123",
        app_scope=User.AppScope.DEMO,
        display_name=email.split("@")[0].replace(".", " ").title(),
    )
    RoleMembership.objects.create(user=user, role=role, campus_id=None if campus is None else campus.pk)
    return user


def _auth(user, role):
    from core.tests.support import bearer_for

    return {"HTTP_AUTHORIZATION": bearer_for(user, role)}


class TimetableTests(TestCase):
    def setUp(self):
        ensure_school_profile()
        ensure_subjects()
        ensure_classrooms()
        self.principal = _person("samuel.kollie@gojo.test", "principal")
        self.registrar = _person("beatrice.toe@gojo.test", "registrar")
        self.bursar = _person("joseph.freeman@gojo.test", "bursar")
        self.teacher_a = _person("grace.johnson@gojo.test", "teacher")
        self.teacher_b = _person("aaron.cooper@gojo.test", "teacher")
        self.teacher_c = _person("victoria.weah@gojo.test", "teacher")
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
        self.math = Subject.objects.get(code="mathematics")
        self.english = Subject.objects.get(code="english-language")

    def _set_subjects(self, classroom, pairs):
        response = self.client.put(
            f"{CLASSES}{classroom.pk}/subjects/",
            data=[
                {"subject_id": str(subject.pk), "teacher_id": str(teacher.pk)}
                for subject, teacher in pairs
            ],
            content_type="application/json",
            **_auth(self.principal, "principal"),
        )
        self.assertEqual(response.status_code, 200, response.content)
        return response.json()

    def test_seeded_paynesville_classrooms_and_subjects(self):
        subjects = self.client.get(SUBJECTS, **_auth(self.principal, "principal"))
        self.assertEqual(subjects.status_code, 200)
        self.assertEqual(len(subjects.json()), 13)
        classes = self.client.get(CLASSES, **_auth(self.principal, "principal"))
        self.assertEqual(classes.status_code, 200)
        names = {row["name"] for row in classes.json()}
        self.assertIn("Grade 1A", names)
        self.assertIn("Grade 12B", names)
        self.assertEqual(len(classes.json()), 24)
        rooms = {row["room_label"] for row in classes.json()}
        self.assertEqual(rooms, {f"{n}{s}" for n in range(1, 13) for s in ("A", "B")})

    def test_teacher_double_booking_returns_400(self):
        self._set_subjects(
            self.classroom_a,
            [(self.math, self.teacher_a), (self.english, self.teacher_b)],
        )
        self._set_subjects(
            self.classroom_b,
            [(self.math, self.teacher_a), (self.english, self.teacher_b)],
        )
        first = self.client.post(
            TIMETABLE,
            data={
                "classroom_id": str(self.classroom_a.pk),
                "day_of_week": "monday",
                "period_number": 2,
                "subject_id": str(self.math.pk),
                "teacher_id": str(self.teacher_a.pk),
                "room_label": "1A",
            },
            content_type="application/json",
            **_auth(self.principal, "principal"),
        )
        self.assertEqual(first.status_code, 201, first.content)
        conflict = self.client.post(
            TIMETABLE,
            data={
                "classroom_id": str(self.classroom_b.pk),
                "day_of_week": "monday",
                "period_number": 2,
                "subject_id": str(self.math.pk),
                "teacher_id": str(self.teacher_a.pk),
                "room_label": "1B",
            },
            content_type="application/json",
            **_auth(self.principal, "principal"),
        )
        self.assertEqual(conflict.status_code, 400)
        self.assertIn("Monday 2nd Period", conflict.json()["fields"]["teacher"][0])

    def test_substitution_clears_uncovered(self):
        self._set_subjects(self.classroom_a, [(self.math, self.teacher_a)])
        today = monrovia_today()
        day = WEEKDAY_TO_DAY.get(today.weekday())
        if day is None:
            today = today + timedelta(days=(7 - today.weekday()) % 7 or 7)
            while WEEKDAY_TO_DAY.get(today.weekday()) is None:
                today += timedelta(days=1)
            day = WEEKDAY_TO_DAY[today.weekday()]
        slot = self.client.post(
            TIMETABLE,
            data={
                "classroom_id": str(self.classroom_a.pk),
                "day_of_week": day,
                "period_number": 3,
                "subject_id": str(self.math.pk),
                "teacher_id": str(self.teacher_a.pk),
                "room_label": "6A",
            },
            content_type="application/json",
            **_auth(self.principal, "principal"),
        )
        self.assertEqual(slot.status_code, 201, slot.content)
        slot_id = slot.json()["id"]
        absence = self.client.post(
            ABSENCES,
            data={"teacher_id": str(self.teacher_a.pk), "date": today.isoformat(), "reason": "Clinic"},
            content_type="application/json",
            **_auth(self.registrar, "registrar"),
        )
        self.assertEqual(absence.status_code, 201, absence.content)
        self.assertGreaterEqual(absence.json()["count"], 1)
        uncovered = self.client.get(
            f"{UNCOVERED}?date={today.isoformat()}",
            **_auth(self.principal, "principal"),
        )
        self.assertEqual(uncovered.status_code, 200)
        self.assertTrue(any(row["id"] == slot_id for row in uncovered.json()["results"]))
        covered = self.client.post(
            SUBSTITUTIONS,
            data={
                "slot_id": slot_id,
                "date": today.isoformat(),
                "substitute_teacher_id": str(self.teacher_b.pk),
                "reason": "Cover for clinic",
            },
            content_type="application/json",
            **_auth(self.principal, "principal"),
        )
        self.assertEqual(covered.status_code, 201, covered.content)
        self.assertFalse(any(row["id"] == slot_id for row in covered.json()["uncovered"]))
        again = self.client.get(
            f"{UNCOVERED}?date={today.isoformat()}",
            **_auth(self.principal, "principal"),
        )
        self.assertFalse(any(row["id"] == slot_id for row in again.json()["results"]))

    def test_teachers_only_see_own_classrooms_and_slots(self):
        self._set_subjects(self.classroom_a, [(self.math, self.teacher_a)])
        self._set_subjects(self.classroom_b, [(self.english, self.teacher_b)])
        self.client.post(
            TIMETABLE,
            data={
                "classroom_id": str(self.classroom_a.pk),
                "day_of_week": "tuesday",
                "period_number": 1,
                "subject_id": str(self.math.pk),
                "teacher_id": str(self.teacher_a.pk),
            },
            content_type="application/json",
            **_auth(self.principal, "principal"),
        )
        self.client.post(
            TIMETABLE,
            data={
                "classroom_id": str(self.classroom_b.pk),
                "day_of_week": "tuesday",
                "period_number": 1,
                "subject_id": str(self.english.pk),
                "teacher_id": str(self.teacher_b.pk),
                "room_label": "6B",
            },
            content_type="application/json",
            **_auth(self.principal, "principal"),
        )
        own_classes = self.client.get(CLASSES, **_auth(self.teacher_a, "teacher"))
        self.assertEqual(own_classes.status_code, 200)
        ids = {row["id"] for row in own_classes.json()}
        self.assertIn(str(self.classroom_a.pk), ids)
        self.assertNotIn(str(self.classroom_b.pk), ids)
        own_slots = self.client.get(TIMETABLE, **_auth(self.teacher_a, "teacher"))
        self.assertEqual(own_slots.status_code, 200)
        self.assertTrue(all(row["teacher"]["id"] == str(self.teacher_a.pk) for row in own_slots.json()))

    def test_room_conflict_and_bursar_cannot_write(self):
        self._set_subjects(self.classroom_a, [(self.math, self.teacher_a)])
        self._set_subjects(self.classroom_b, [(self.english, self.teacher_b)])
        first = self.client.post(
            TIMETABLE,
            data={
                "classroom_id": str(self.classroom_a.pk),
                "day_of_week": "wednesday",
                "period_number": 4,
                "subject_id": str(self.math.pk),
                "teacher_id": str(self.teacher_a.pk),
                "room_label": "Lab-1",
            },
            content_type="application/json",
            **_auth(self.principal, "principal"),
        )
        self.assertEqual(first.status_code, 201, first.content)
        conflict = self.client.post(
            TIMETABLE,
            data={
                "classroom_id": str(self.classroom_b.pk),
                "day_of_week": "wednesday",
                "period_number": 4,
                "subject_id": str(self.english.pk),
                "teacher_id": str(self.teacher_b.pk),
                "room_label": "Lab-1",
            },
            content_type="application/json",
            **_auth(self.principal, "principal"),
        )
        self.assertEqual(conflict.status_code, 400)
        self.assertIn("room_label", conflict.json()["fields"])
        denied = self.client.post(
            TIMETABLE,
            data={
                "classroom_id": str(self.classroom_a.pk),
                "day_of_week": "thursday",
                "period_number": 1,
                "subject_id": str(self.math.pk),
                "teacher_id": str(self.teacher_a.pk),
            },
            content_type="application/json",
            **_auth(self.bursar, "bursar"),
        )
        self.assertEqual(denied.status_code, 403)

    def test_period_out_of_range_and_unassigned_subject(self):
        self._set_subjects(self.classroom_a, [(self.math, self.teacher_a)])
        bad_period = self.client.post(
            TIMETABLE,
            data={
                "classroom_id": str(self.classroom_a.pk),
                "day_of_week": "friday",
                "period_number": 9,
                "subject_id": str(self.math.pk),
                "teacher_id": str(self.teacher_a.pk),
            },
            content_type="application/json",
            **_auth(self.principal, "principal"),
        )
        self.assertEqual(bad_period.status_code, 400)
        self.assertIn("period_number", bad_period.json()["fields"])
        science = Subject.objects.get(code="general-science")
        bad_subject = self.client.post(
            TIMETABLE,
            data={
                "classroom_id": str(self.classroom_a.pk),
                "day_of_week": "friday",
                "period_number": 1,
                "subject_id": str(science.pk),
                "teacher_id": str(self.teacher_a.pk),
            },
            content_type="application/json",
            **_auth(self.principal, "principal"),
        )
        self.assertEqual(bad_subject.status_code, 400)
        self.assertIn("subject_id", bad_subject.json()["fields"])

    def test_put_subjects_blocked_when_slot_exists_and_cascade_delete(self):
        self._set_subjects(self.classroom_a, [(self.math, self.teacher_a), (self.english, self.teacher_b)])
        slot = self.client.post(
            TIMETABLE,
            data={
                "classroom_id": str(self.classroom_a.pk),
                "day_of_week": "monday",
                "period_number": 5,
                "subject_id": str(self.math.pk),
                "teacher_id": str(self.teacher_a.pk),
            },
            content_type="application/json",
            **_auth(self.principal, "principal"),
        )
        self.assertEqual(slot.status_code, 201, slot.content)
        blocked = self.client.put(
            f"{CLASSES}{self.classroom_a.pk}/subjects/",
            data=[{"subject_id": str(self.english.pk), "teacher_id": str(self.teacher_b.pk)}],
            content_type="application/json",
            **_auth(self.principal, "principal"),
        )
        self.assertEqual(blocked.status_code, 400)
        self.assertIn("Mathematics", blocked.json()["fields"]["subjects"][0])
        deleted = self.client.delete(
            f"{CLASSES}{self.classroom_a.pk}/",
            **_auth(self.principal, "principal"),
        )
        self.assertEqual(deleted.status_code, 204)
        self.assertFalse(Classroom.objects.filter(pk=self.classroom_a.pk).exists())
        self.assertFalse(TimetableSlot.objects.filter(pk=slot.json()["id"]).exists())

    def test_substitute_cannot_be_permanent_teacher(self):
        self._set_subjects(self.classroom_a, [(self.math, self.teacher_a)])
        today = date(2026, 11, 23)  # Monday
        slot = self.client.post(
            TIMETABLE,
            data={
                "classroom_id": str(self.classroom_a.pk),
                "day_of_week": "monday",
                "period_number": 6,
                "subject_id": str(self.math.pk),
                "teacher_id": str(self.teacher_a.pk),
            },
            content_type="application/json",
            **_auth(self.principal, "principal"),
        )
        self.assertEqual(slot.status_code, 201, slot.content)
        bad = self.client.post(
            SUBSTITUTIONS,
            data={
                "slot_id": slot.json()["id"],
                "date": today.isoformat(),
                "substitute_teacher_id": str(self.teacher_a.pk),
            },
            content_type="application/json",
            **_auth(self.principal, "principal"),
        )
        self.assertEqual(bad.status_code, 400)
        self.assertIn("substitute_teacher_id", bad.json()["fields"])

    def test_teacher_options_list(self):
        response = self.client.get(TEACHERS, **_auth(self.principal, "principal"))
        self.assertEqual(response.status_code, 200)
        ids = {row["id"] for row in response.json()}
        self.assertIn(str(self.teacher_a.pk), ids)
        self.assertIn(str(self.teacher_b.pk), ids)
