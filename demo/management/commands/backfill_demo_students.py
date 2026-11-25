"""Create Student rows from existing demo role=student users."""

import hashlib
import random
from datetime import date, timedelta

from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone

from core.models import RoleMembership, User
from demo.models import AcademicYear, Campus, Classroom, GradeLevel, Student, StudentWelfare
from demo.service.class_service import DEFAULT_CAPACITY, ensure_subjects
from demo.service.school_service import CAMPUSES, ensure_school_profile
from demo.service.student_service import allocate_student_code

SECTIONS = ("A", "B")
# Typical ages by grade label for Liberian ECE + Grades 1–12.
GRADE_AGE = {
    "Nursery": (3, 4),
    "ABC": (3, 4),
    "K1": (4, 5),
    "K2": (5, 6),
    "Grade 1": (6, 7),
    "Grade 2": (7, 8),
    "Grade 3": (8, 9),
    "Grade 4": (9, 10),
    "Grade 5": (10, 11),
    "Grade 6": (11, 12),
    "Grade 7": (12, 13),
    "Grade 8": (13, 14),
    "Grade 9": (14, 15),
    "Grade 10": (15, 16),
    "Grade 11": (16, 17),
    "Grade 12": (17, 18),
}


class Command(BaseCommand):
    help = "Idempotently backfill demo Student rows from role=student users."

    def handle(self, *args, **options):
        ensure_school_profile()
        ensure_subjects()
        year = AcademicYear.objects.filter(is_current=True).first()
        if year is None:
            self.stderr.write("No current academic year.")
            return

        restored = _restore_grades_served()
        created_classrooms = 0
        removed_classrooms = 0
        for campus in Campus.objects.order_by("code"):
            created_classrooms += _ensure_campus_classrooms(campus, year)
            removed_classrooms += _remove_out_of_scope_classrooms(campus, year)

        assigned_homerooms = _assign_homeroom_teachers()
        reassigned = _reassign_students_to_campus_classrooms()
        created_students = _backfill_students()

        self.stdout.write(
            self.style.SUCCESS(
                f"grades_restored={restored} "
                f"classrooms_created={created_classrooms} "
                f"classrooms_removed={removed_classrooms} "
                f"homerooms_assigned={assigned_homerooms} "
                f"students_reassigned={reassigned} "
                f"students_created={created_students}"
            )
        )


def _restore_grades_served():
    """Put each campus back on the seeded grade list (e.g. Gardnersville = ECE)."""
    restored = 0
    by_code = {spec["code"]: spec for spec in CAMPUSES}
    for campus in Campus.objects.all():
        spec = by_code.get(campus.code)
        if spec is None:
            continue
        wanted = list(GradeLevel.objects.filter(name__in=spec["grades"]).order_by("sort_order"))
        current = set(campus.grades_served.values_list("pk", flat=True))
        wanted_ids = {grade.pk for grade in wanted}
        if current != wanted_ids:
            campus.grades_served.set(wanted)
            restored += 1
    return restored


def _ensure_campus_classrooms(campus, year):
    """Create A/B sections only for grades this campus serves."""
    created = 0
    grades = list(campus.grades_served.order_by("sort_order"))
    capacity = 120 if campus.code != "gardnersville" else 60
    for grade in grades:
        for section in SECTIONS:
            room_label = _room_label(grade.name, section)
            classroom, was_created = Classroom.objects.get_or_create(
                campus=campus,
                academic_year=year,
                grade_level=grade,
                section_name=section,
                defaults={
                    "capacity": max(DEFAULT_CAPACITY, capacity),
                    "room_label": room_label,
                    "homeroom_teacher": None,
                },
            )
            if was_created:
                created += 1
            else:
                updates = []
                if classroom.capacity < capacity:
                    classroom.capacity = capacity
                    updates.append("capacity")
                if classroom.room_label != room_label:
                    classroom.room_label = room_label
                    updates.append("room_label")
                if updates:
                    updates.append("updated_at")
                    classroom.save(update_fields=updates)
    return created


def _remove_out_of_scope_classrooms(campus, year):
    """Drop classrooms whose grade is not on this campus's grades_served."""
    allowed = set(campus.grades_served.values_list("pk", flat=True))
    extras = list(
        Classroom.objects.filter(campus=campus, academic_year=year)
        .exclude(grade_level_id__in=allowed)
        .select_related("grade_level")
    )
    if not extras:
        return 0
    extra_ids = [row.pk for row in extras]
    # Move any lingering students off these rooms before delete.
    Student.objects.filter(current_classroom_id__in=extra_ids).update(current_classroom=None)
    deleted, _counts = Classroom.objects.filter(pk__in=extra_ids).delete()
    return len(extra_ids)


def _assign_homeroom_teachers():
    assigned = 0
    for campus in Campus.objects.order_by("code"):
        teachers = list(
            User.objects.filter(
                memberships__role=RoleMembership.Role.TEACHER,
                memberships__campus_id=campus.pk,
                app_scope=User.AppScope.DEMO,
            )
            .distinct()
            .order_by("email")
        )
        if not teachers:
            teachers = list(
                User.objects.filter(
                    memberships__role=RoleMembership.Role.TEACHER,
                    memberships__campus_id__isnull=True,
                    app_scope=User.AppScope.DEMO,
                )
                .distinct()
                .order_by("email")
            )
        classrooms = list(
            Classroom.objects.filter(campus=campus).order_by(
                "grade_level__sort_order",
                "section_name",
            )
        )
        if not teachers or not classrooms:
            continue
        for index, classroom in enumerate(classrooms):
            teacher = teachers[index % len(teachers)]
            if classroom.homeroom_teacher_id != teacher.pk:
                classroom.homeroom_teacher = teacher
                classroom.save(update_fields=["homeroom_teacher", "updated_at"])
                assigned += 1
    return assigned


def _reassign_students_to_campus_classrooms():
    """Round-robin every campus's students into that campus's valid classrooms."""
    reassigned = 0
    for campus in Campus.objects.order_by("code"):
        classrooms = list(
            Classroom.objects.filter(campus=campus)
            .select_related("grade_level")
            .order_by("grade_level__sort_order", "section_name")
        )
        if not classrooms:
            continue
        students = list(
            Student.objects.filter(campus=campus).order_by("student_code")
        )
        for index, student in enumerate(students):
            classroom = classrooms[index % len(classrooms)]
            dob = _dob_for_grade(classroom.grade_level.name, student.user_id or student.pk)
            changed = False
            if student.current_classroom_id != classroom.pk:
                student.current_classroom = classroom
                changed = True
            if student.date_of_birth != dob:
                student.date_of_birth = dob
                changed = True
            if student.is_waec_candidate and classroom.grade_level.name not in (
                "Grade 9",
                "Grade 12",
            ):
                student.is_waec_candidate = False
                changed = True
            if changed:
                student.save(
                    update_fields=[
                        "current_classroom",
                        "date_of_birth",
                        "is_waec_candidate",
                        "updated_at",
                    ]
                )
                reassigned += 1
    return reassigned


def _backfill_students():
    created = 0
    memberships = (
        RoleMembership.objects.filter(role=RoleMembership.Role.STUDENT)
        .select_related("user")
        .order_by("user__email")
    )
    by_campus = {}
    for membership in memberships:
        if Student.objects.filter(user_id=membership.user_id).exists():
            continue
        by_campus.setdefault(membership.campus_id, []).append(membership)

    for campus_id, rows in by_campus.items():
        if campus_id is None:
            continue
        classrooms = list(
            Classroom.objects.filter(campus_id=campus_id)
            .select_related("grade_level")
            .order_by("grade_level__sort_order", "section_name")
        )
        if not classrooms:
            continue
        for index, membership in enumerate(rows):
            classroom = classrooms[index % len(classrooms)]
            with transaction.atomic():
                student = Student.objects.create(
                    student_code=allocate_student_code(),
                    user=membership.user,
                    full_name=membership.user.display_name or membership.user.email,
                    campus_id=campus_id,
                    current_classroom=classroom,
                    status=Student.Status.ACTIVE,
                    is_waec_candidate=False,
                    date_of_birth=_dob_for_grade(
                        classroom.grade_level.name, membership.user_id
                    ),
                    gender=_gender_for_user(membership.user_id),
                    catchment_zone="",
                )
                StudentWelfare.objects.create(student=student)
                created += 1
    return created


def _room_label(grade_name, section):
    if grade_name.startswith("Grade "):
        number = grade_name.split(" ", 1)[1]
        return f"{number}{section}"
    return f"{grade_name} {section}"


def _gender_for_user(user_id):
    digest = hashlib.sha256(str(user_id).encode()).digest()
    return Student.Gender.FEMALE if digest[0] % 2 == 0 else Student.Gender.MALE


def _dob_for_grade(grade_name, user_id):
    low, high = GRADE_AGE.get(str(grade_name), (5, 6))
    rng = random.Random(str(user_id))
    age = rng.randint(low, high)
    today = timezone.localdate()
    day_offset = rng.randint(0, 364)
    try:
        born = date(today.year - age, 1, 1) + timedelta(days=day_offset)
    except ValueError:
        born = date(today.year - age, 6, 15)
    if born >= today:
        born = date(born.year - 1, born.month, min(born.day, 28))
    return born
