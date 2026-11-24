"""Classroom CRUD, MoE subjects, and subject assignment."""

from django.db import transaction
from django.db.models import Prefetch
from rest_framework.exceptions import ValidationError

from core.models import RoleMembership, User
from demo.models import (
    AcademicYear,
    Campus,
    ClassSubjectAssignment,
    Classroom,
    GradeLevel,
    Subject,
)
from demo.service.school_service import ensure_school_profile
from demo.service.timetable_service import (
    classroom_payload,
    classrooms_for_actor,
    list_teacher_options,
    subject_payload,
)

SUBJECTS = (
    ("mathematics", "Mathematics", True, False, 1),
    ("english-language", "English Language", True, False, 2),
    ("general-science", "General Science", True, False, 3),
    ("social-studies", "Social Studies", True, False, 4),
    ("physics", "Physics", False, False, 5),
    ("chemistry", "Chemistry", False, False, 6),
    ("biology", "Biology", False, False, 7),
    ("liberian-history", "Liberian History", False, False, 8),
    ("literature", "Literature", False, False, 9),
    ("economics", "Economics", False, False, 10),
    ("french", "French", False, False, 11),
    ("computer-science", "Computer Science", False, False, 12),
    ("waec-practical-labs", "WAEC Practical Labs", False, True, 13),
)

DEFAULT_CAPACITY = 35


def ensure_subjects():
    for code, name, is_core, is_waec_lab, order in SUBJECTS:
        Subject.objects.get_or_create(
            code=code,
            defaults={
                "name": name,
                "division": None,
                "is_core": is_core,
                "is_waec_lab": is_waec_lab,
                "sort_order": order,
            },
        )


def ensure_classrooms():
    """Paynesville Grades 1–12 sections A and B for the current academic year."""
    ensure_school_profile()
    ensure_subjects()
    campus = Campus.objects.filter(code="paynesville").first()
    year = AcademicYear.objects.filter(is_current=True).first()
    if campus is None or year is None:
        return 0
    created = 0
    for number in range(1, 13):
        grade = GradeLevel.objects.filter(name=f"Grade {number}").first()
        if grade is None:
            continue
        if not campus.grades_served.filter(pk=grade.pk).exists():
            continue
        for section in ("A", "B"):
            room = f"{number}{section}"
            _classroom, was_created = Classroom.objects.get_or_create(
                campus=campus,
                academic_year=year,
                grade_level=grade,
                section_name=section,
                defaults={
                    "capacity": DEFAULT_CAPACITY,
                    "room_label": room,
                    "homeroom_teacher": None,
                },
            )
            if was_created:
                created += 1
    return created


def list_subjects():
    ensure_subjects()
    return [subject_payload(row) for row in Subject.objects.select_related("division")]


def list_classrooms(memberships):
    ensure_classrooms()
    rows = classrooms_for_actor(memberships)
    return [classroom_payload(row) for row in rows]


def get_classroom(memberships, classroom_id):
    classroom = classrooms_for_actor(memberships).filter(pk=classroom_id).first()
    if classroom is None:
        return None
    return classroom_payload(classroom)


def create_classroom(payload):
    campus = _campus(payload.get("campus_id"))
    year = _year(payload.get("academic_year_id"))
    grade = _grade(payload.get("grade_level_id"), campus)
    section = _section(payload.get("section_name"))
    if Classroom.objects.filter(
        campus=campus,
        academic_year=year,
        grade_level=grade,
        section_name=section,
    ).exists():
        _invalid("section_name", "This section already exists for the campus, year, and grade.")
    classroom = Classroom.objects.create(
        campus=campus,
        academic_year=year,
        grade_level=grade,
        section_name=section,
        homeroom_teacher=_optional_teacher(payload.get("homeroom_teacher_id")),
        capacity=_capacity(payload.get("capacity", DEFAULT_CAPACITY)),
        room_label=_text(payload.get("room_label"), "room_label"),
    )
    return classroom_payload(classroom)


def update_classroom(classroom, payload):
    if "campus_id" in payload:
        classroom.campus = _campus(payload.get("campus_id"))
    if "academic_year_id" in payload:
        classroom.academic_year = _year(payload.get("academic_year_id"))
    if "grade_level_id" in payload or "campus_id" in payload:
        grade_id = payload.get("grade_level_id", classroom.grade_level_id)
        classroom.grade_level = _grade(grade_id, classroom.campus)
    if "section_name" in payload:
        classroom.section_name = _section(payload.get("section_name"))
    if "homeroom_teacher_id" in payload:
        classroom.homeroom_teacher = _optional_teacher(payload.get("homeroom_teacher_id"))
    if "capacity" in payload:
        classroom.capacity = _capacity(payload.get("capacity"))
    if "room_label" in payload:
        classroom.room_label = _text(payload.get("room_label"), "room_label")
    clash = (
        Classroom.objects.filter(
            campus=classroom.campus,
            academic_year=classroom.academic_year,
            grade_level=classroom.grade_level,
            section_name=classroom.section_name,
        )
        .exclude(pk=classroom.pk)
        .exists()
    )
    if clash:
        _invalid("section_name", "This section already exists for the campus, year, and grade.")
    classroom.save()
    return classroom_payload(classroom)


def delete_classroom(classroom):
    classroom.delete()


def replace_subjects(classroom, payload):
    if not isinstance(payload, list):
        _invalid("subjects", "Subjects must be a list.")
    wanted = []
    seen = set()
    for index, item in enumerate(payload):
        if not isinstance(item, dict):
            _invalid("subjects", "Each subject assignment must be an object.")
        subject = _subject(item.get("subject_id"))
        if subject.pk in seen:
            _invalid("subjects", "A subject was listed more than once.")
        seen.add(subject.pk)
        wanted.append((subject, _optional_teacher(item.get("teacher_id"))))
    current = {
        row.subject_id: row
        for row in ClassSubjectAssignment.objects.filter(classroom=classroom).select_related("subject")
    }
    removing = [row for subject_id, row in current.items() if subject_id not in seen]
    blocked = []
    for row in removing:
        if classroom.timetable_slots.filter(subject_id=row.subject_id).exists():
            blocked.append(row.subject.name)
    if blocked:
        names = ", ".join(blocked)
        _invalid(
            "subjects",
            f"Remove timetable slots for {names} before dropping those subjects.",
        )
    with transaction.atomic():
        for row in removing:
            row.delete()
        for subject, teacher in wanted:
            ClassSubjectAssignment.objects.update_or_create(
                classroom=classroom,
                subject=subject,
                defaults={"teacher": teacher},
            )
    classroom = (
        Classroom.objects.select_related(
            "campus",
            "academic_year",
            "grade_level__division",
            "homeroom_teacher",
        )
        .prefetch_related(
            Prefetch(
                "subject_assignments",
                queryset=ClassSubjectAssignment.objects.select_related("subject__division", "teacher"),
            )
        )
        .get(pk=classroom.pk)
    )
    return classroom_payload(classroom)


def teacher_options(campus_id=None):
    return list_teacher_options(campus_id)


def _campus(raw):
    campus = Campus.objects.filter(pk=raw).first()
    if campus is None:
        _invalid("campus_id", "Campus was not found.")
    return campus


def _year(raw):
    year = AcademicYear.objects.filter(pk=raw).first()
    if year is None:
        _invalid("academic_year_id", "Academic year was not found.")
    return year


def _grade(raw, campus):
    grade = GradeLevel.objects.select_related("division").filter(pk=raw).first()
    if grade is None:
        _invalid("grade_level_id", "Grade level was not found.")
    if not campus.grades_served.filter(pk=grade.pk).exists():
        _invalid("grade_level_id", "This grade is not served by the campus.")
    return grade


def _section(raw):
    value = _text(raw, "section_name")
    if not value:
        _invalid("section_name", "Section name is required.")
    return value


def _capacity(raw):
    try:
        value = int(raw)
    except (TypeError, ValueError):
        _invalid("capacity", "Capacity must be a number.")
    if value < 1 or value > 120:
        _invalid("capacity", "Capacity must be between 1 and 120.")
    return value


def _subject(raw):
    subject = Subject.objects.filter(pk=raw).first()
    if subject is None:
        _invalid("subject_id", "Subject was not found.")
    return subject


def _optional_teacher(raw):
    if raw in (None, ""):
        return None
    user = User.objects.filter(pk=raw, app_scope=User.AppScope.DEMO, is_active=True).first()
    if user is None:
        _invalid("teacher_id", "Teacher was not found.")
    if not user.memberships.filter(role=RoleMembership.Role.TEACHER).exists():
        _invalid("teacher_id", "User is not a teacher.")
    return user


def _text(raw, field):
    if raw is None:
        return ""
    if not isinstance(raw, str):
        _invalid(field, "Enter text.")
    return raw.strip()


def _invalid(field, message):
    raise ValidationError({field: [message]})
