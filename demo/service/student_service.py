"""Student directory, welfare, authorized pickup, and conduct notes."""

from datetime import date

from django.db import transaction
from django.db.models import Q
from django.utils import timezone
from django.utils.dateparse import parse_date
from rest_framework.exceptions import PermissionDenied, ValidationError

from core.helpers.phone import InvalidLiberianPhone, normalize_liberian_phone
from core.integrations.storage import photo_url
from core.models import RoleMembership, User
from demo.models import (
    AuthorizedPickup,
    Campus,
    Classroom,
    ConductNote,
    Student,
    StudentWelfare,
)
from demo.service.timetable_service import classrooms_for_actor, monrovia_today

STATUS_LABEL = {choice[0]: choice[1] for choice in Student.Status.choices}
GENDER_LABEL = {choice[0]: choice[1] for choice in Student.Gender.choices}
NOTE_TYPE_LABEL = {choice[0]: choice[1] for choice in ConductNote.NoteType.choices}
SEVERITY_LABEL = {choice[0]: choice[1] for choice in ConductNote.Severity.choices}

DEFAULT_LIST_STATUSES = frozenset(
    {
        Student.Status.ACTIVE,
        Student.Status.HONOR_ROLL,
        Student.Status.AT_RISK,
    }
)

OFFICE_ROLES = frozenset(
    {
        RoleMembership.Role.PRINCIPAL,
        RoleMembership.Role.REGISTRAR,
    }
)
READ_ROLES = frozenset(
    {
        RoleMembership.Role.PRINCIPAL,
        RoleMembership.Role.REGISTRAR,
        RoleMembership.Role.BURSAR,
        RoleMembership.Role.TEACHER,
        RoleMembership.Role.SUPPORT_STAFF,
    }
)


def allocate_student_code(*, year=None):
    """Return the next GI-YYYY-NNNN code for the Monrovia calendar year."""
    year = year or timezone.localdate().year
    prefix = f"GI-{year}-"
    with transaction.atomic():
        latest = (
            Student.objects.select_for_update()
            .filter(student_code__startswith=prefix)
            .order_by("-student_code")
            .values_list("student_code", flat=True)
            .first()
        )
        next_number = 1
        if latest:
            try:
                next_number = int(latest.rsplit("-", 1)[-1]) + 1
            except ValueError:
                next_number = 1
        if next_number > 9999:
            _invalid("student_code", "Student ID sequence for this year is exhausted.")
        return f"{prefix}{next_number:04d}"


def students_for_actor(memberships):
    roles = {row.role for row in memberships}
    query = Student.objects.select_related(
        "campus",
        "current_classroom__grade_level__division",
        "current_classroom__homeroom_teacher",
        "last_homeroom_teacher",
        "welfare",
        "user",
    )
    if roles.intersection(
        {
            RoleMembership.Role.PRINCIPAL,
            RoleMembership.Role.REGISTRAR,
            RoleMembership.Role.BURSAR,
            RoleMembership.Role.SUPPORT_STAFF,
        }
    ):
        campus_ids = [
            row.campus_id
            for row in memberships
            if row.role
            in {
                RoleMembership.Role.PRINCIPAL,
                RoleMembership.Role.REGISTRAR,
                RoleMembership.Role.BURSAR,
                RoleMembership.Role.SUPPORT_STAFF,
            }
            and row.campus_id is not None
        ]
        school_wide = any(
            row.campus_id is None
            and row.role
            in {
                RoleMembership.Role.PRINCIPAL,
                RoleMembership.Role.REGISTRAR,
                RoleMembership.Role.BURSAR,
                RoleMembership.Role.SUPPORT_STAFF,
            }
            for row in memberships
        )
        if school_wide or not campus_ids:
            return query
        return query.filter(campus_id__in=campus_ids)
    if RoleMembership.Role.TEACHER in roles:
        allowed = classrooms_for_actor(memberships)
        return query.filter(current_classroom__in=allowed)
    return Student.objects.none()


def filtered_students(memberships, params):
    query = students_for_actor(memberships)
    status_raw = (params.get("status") or "").strip().lower()
    if status_raw in ("", "all"):
        query = query.filter(status__in=DEFAULT_LIST_STATUSES)
    elif status_raw in ("waec_candidate", "waec"):
        query = query.filter(is_waec_candidate=True).exclude(
            status__in={Student.Status.WITHDRAWN, Student.Status.GRADUATED}
        )
    elif status_raw in STATUS_LABEL:
        query = query.filter(status=status_raw)
    else:
        _invalid("status", "Unknown status filter.")

    campus = (params.get("campus") or "").strip()
    if campus:
        query = query.filter(campus_id=campus)

    division = (params.get("division") or "").strip()
    if division:
        query = query.filter(
            Q(current_classroom__grade_level__division_id=division)
            | Q(current_classroom__grade_level__division__name__iexact=division)
            | Q(current_classroom__grade_level__division__code__iexact=division)
        )

    classroom = (params.get("classroom") or "").strip()
    if classroom:
        query = query.filter(current_classroom_id=classroom)

    grade = (params.get("grade") or "").strip()
    if grade:
        query = query.filter(current_classroom__grade_level_id=grade)

    q = (params.get("q") or "").strip()
    if q:
        query = query.filter(
            Q(full_name__icontains=q) | Q(student_code__icontains=q)
        )
    return query.distinct()


def list_summary(queryset):
    """Counts for the default-visible roster universe of the same filters.

    Summary ignores pagination but uses the filtered queryset before page slice.
    For cards, recompute against Active/Honor Roll/At Risk (+ WAEC flag) of the
    actor-visible set matching non-status filters.
    """
    base = queryset.model.objects.filter(pk__in=queryset.values("pk"))
    default_visible = base.filter(status__in=DEFAULT_LIST_STATUSES)
    return {
        "total": default_visible.count(),
        "at_risk": default_visible.filter(status=Student.Status.AT_RISK).count(),
        "waec_candidates": default_visible.filter(is_waec_candidate=True).count(),
        "honor_roll": default_visible.filter(status=Student.Status.HONOR_ROLL).count(),
    }


def get_student(memberships, student_id):
    return students_for_actor(memberships).filter(pk=student_id).first()


def enroll_student(payload, *, photo=None):
    full_name = _required_text(payload.get("full_name"), "full_name")
    campus = _campus(payload.get("campus_id"))
    classroom = _classroom(payload.get("classroom_id"))
    if classroom.campus_id != campus.id:
        _invalid("classroom_id", "Classroom must belong to the selected campus.")
    dob = _date(payload.get("date_of_birth"), "date_of_birth")
    gender = _gender(payload.get("gender"))
    catchment = _text(payload.get("catchment_zone"), "catchment_zone")
    status = _status(payload.get("status"), default=Student.Status.ACTIVE)
    waec = _bool(payload.get("is_waec_candidate"), default=False)
    waec = _waec_for_classroom(classroom, waec)

    with transaction.atomic():
        student = Student.objects.create(
            student_code=allocate_student_code(),
            full_name=full_name,
            campus=campus,
            current_classroom=classroom,
            status=status,
            is_waec_candidate=waec,
            date_of_birth=dob,
            gender=gender,
            catchment_zone=catchment,
        )
        if photo is not None:
            student.photo = photo
            student.save(update_fields=["photo", "updated_at"])
        StudentWelfare.objects.create(student=student)
    return student_payload(student)


def update_student(student, payload, *, photo=None, memberships=None):
    roles = {row.role for row in (memberships or [])}
    if not roles.intersection(OFFICE_ROLES):
        raise PermissionDenied()

    if "full_name" in payload:
        student.full_name = _required_text(payload.get("full_name"), "full_name")
    if "campus_id" in payload or "classroom_id" in payload:
        campus = _campus(payload.get("campus_id", student.campus_id))
        classroom_raw = payload.get("classroom_id", student.current_classroom_id)
        classroom = _classroom(classroom_raw) if classroom_raw not in (None, "") else None
        if classroom is not None and classroom.campus_id != campus.id:
            _invalid("classroom_id", "Classroom must belong to the selected campus.")
        student.campus = campus
        student.current_classroom = classroom
    if "date_of_birth" in payload:
        student.date_of_birth = _date(payload.get("date_of_birth"), "date_of_birth")
    if "gender" in payload:
        student.gender = _gender(payload.get("gender"))
    if "catchment_zone" in payload:
        student.catchment_zone = _text(payload.get("catchment_zone"), "catchment_zone")
    if "status" in payload:
        new_status = _status(payload.get("status"))
        if new_status == Student.Status.WITHDRAWN and student.status != Student.Status.WITHDRAWN:
            _apply_withdraw(student)
        else:
            student.status = new_status
            if new_status == Student.Status.GRADUATED:
                student.is_waec_candidate = False
    if "is_waec_candidate" in payload:
        student.is_waec_candidate = _waec_for_classroom(
            student.current_classroom,
            _bool(payload.get("is_waec_candidate"), default=False),
        )
    else:
        student.is_waec_candidate = _waec_for_classroom(
            student.current_classroom,
            student.is_waec_candidate,
        )

    if photo is not None:
        student.photo = photo
    student.save()
    return student_payload(student)


def withdraw_student(student):
    _apply_withdraw(student)
    student.save()
    return student_payload(student)


def get_welfare(student):
    welfare, _created = StudentWelfare.objects.get_or_create(student=student)
    return welfare_payload(welfare)


def save_welfare(student, payload, *, actor, memberships):
    _assert_welfare_writer(student, actor, memberships)
    welfare, _created = StudentWelfare.objects.get_or_create(student=student)
    if "allergies" in payload:
        welfare.allergies = _text(payload.get("allergies"), "allergies")
    if "medical_notes" in payload:
        welfare.medical_notes = _text(payload.get("medical_notes"), "medical_notes")
    if "blood_group" in payload:
        welfare.blood_group = _text(payload.get("blood_group"), "blood_group", max_length=8)
    if "emergency_contact" in payload:
        raw = payload.get("emergency_contact")
        if raw in (None, ""):
            welfare.emergency_contact = ""
        else:
            welfare.emergency_contact = _phone(raw, "emergency_contact")
    welfare.save()
    return welfare_payload(welfare)


def list_pickups(student):
    return [pickup_payload(row) for row in student.pickups.all()]


def create_pickup(student, payload, *, actor, memberships):
    _assert_welfare_writer(student, actor, memberships)
    pickup = AuthorizedPickup.objects.create(
        student=student,
        name=_required_text(payload.get("name"), "name"),
        relationship=_required_text(payload.get("relationship"), "relationship"),
        phone=_phone(payload.get("phone"), "phone"),
        id_note=_text(payload.get("id_note"), "id_note"),
    )
    return pickup_payload(pickup)


def update_pickup(student, pickup_id, payload, *, actor, memberships):
    _assert_welfare_writer(student, actor, memberships)
    pickup = student.pickups.filter(pk=pickup_id).first()
    if pickup is None:
        return None
    if "name" in payload:
        pickup.name = _required_text(payload.get("name"), "name")
    if "relationship" in payload:
        pickup.relationship = _required_text(payload.get("relationship"), "relationship")
    if "phone" in payload:
        pickup.phone = _phone(payload.get("phone"), "phone")
    if "id_note" in payload:
        pickup.id_note = _text(payload.get("id_note"), "id_note")
    pickup.save()
    return pickup_payload(pickup)


def delete_pickup(student, pickup_id, *, actor, memberships):
    _assert_welfare_writer(student, actor, memberships)
    pickup = student.pickups.filter(pk=pickup_id).first()
    if pickup is None:
        return False
    pickup.delete()
    return True


def list_conduct(student):
    rows = student.conduct_notes.select_related("author").all()
    return [conduct_payload(row) for row in rows]


def add_conduct(student, payload, *, actor, memberships):
    _assert_conduct_writer(student, actor, memberships)
    note = ConductNote.objects.create(
        student=student,
        note_type=_note_type(payload.get("note_type") or payload.get("type")),
        severity=_severity(payload.get("severity")),
        description=_required_text(payload.get("description"), "description"),
        author=actor,
    )
    return conduct_payload(note)


def student_payload(student):
    classroom = student.current_classroom
    grade = classroom.grade_level if classroom else None
    division = grade.division if grade else None
    return {
        "id": str(student.pk),
        "student_code": student.student_code,
        "full_name": student.full_name,
        "campus_id": str(student.campus_id),
        "campus_name": student.campus.name,
        "classroom_id": str(classroom.pk) if classroom else None,
        "classroom_name": classroom.display_name if classroom else None,
        "division": division.name if division else None,
        "division_id": str(division.pk) if division else None,
        "grade": grade.name if grade else None,
        "grade_id": str(grade.pk) if grade else None,
        "status": student.status,
        "status_label": STATUS_LABEL.get(student.status, student.status),
        "is_waec_candidate": student.is_waec_candidate,
        "date_of_birth": student.date_of_birth.isoformat(),
        "gender": student.gender,
        "gender_label": GENDER_LABEL.get(student.gender, student.gender),
        "catchment_zone": student.catchment_zone,
        "photo_url": photo_url(student.photo),
        "primary_guardian": None,
        "attendance_pct": None,
        "yearly_avg_pct": None,
        "exam_clearance": None,
        "lrd_balance_lrd": None,
        "updated_at": student.updated_at.isoformat(),
    }


def welfare_payload(welfare):
    return {
        "student_id": str(welfare.student_id),
        "allergies": welfare.allergies,
        "medical_notes": welfare.medical_notes,
        "blood_group": welfare.blood_group,
        "emergency_contact": welfare.emergency_contact,
        "updated_at": welfare.updated_at.isoformat(),
    }


def pickup_payload(pickup):
    return {
        "id": str(pickup.pk),
        "student_id": str(pickup.student_id),
        "name": pickup.name,
        "relationship": pickup.relationship,
        "phone": pickup.phone,
        "id_note": pickup.id_note,
        "updated_at": pickup.updated_at.isoformat(),
    }


def conduct_payload(note):
    return {
        "id": str(note.pk),
        "student_id": str(note.student_id),
        "note_type": note.note_type,
        "note_type_label": NOTE_TYPE_LABEL.get(note.note_type, note.note_type),
        "severity": note.severity,
        "severity_label": SEVERITY_LABEL.get(note.severity, note.severity),
        "description": note.description,
        "author_id": str(note.author_id),
        "author_name": note.author.display_name or note.author.email,
        "created_at": note.created_at.isoformat(),
    }


def _apply_withdraw(student):
    if student.current_classroom and student.current_classroom.homeroom_teacher_id:
        student.last_homeroom_teacher_id = student.current_classroom.homeroom_teacher_id
    student.current_classroom = None
    student.status = Student.Status.WITHDRAWN
    student.is_waec_candidate = False


def _assert_welfare_writer(student, actor, memberships):
    roles = {row.role for row in memberships}
    if roles.intersection(OFFICE_ROLES):
        return
    if RoleMembership.Role.TEACHER not in roles:
        raise PermissionDenied()
    if student.current_classroom and student.current_classroom.homeroom_teacher_id == actor.pk:
        return
    if student.last_homeroom_teacher_id == actor.pk:
        return
    raise PermissionDenied()


def _assert_conduct_writer(student, actor, memberships):
    roles = {row.role for row in memberships}
    if roles.intersection(OFFICE_ROLES):
        return
    if RoleMembership.Role.TEACHER not in roles:
        raise PermissionDenied()
    if student.current_classroom_id is None:
        raise PermissionDenied()
    allowed = classrooms_for_actor(memberships).filter(pk=student.current_classroom_id)
    if not allowed.exists():
        raise PermissionDenied()


def _waec_for_classroom(classroom, wanted):
    if not wanted:
        return False
    if classroom is None:
        return False
    number = _grade_number(classroom.grade_level.name)
    if number not in (9, 12):
        return False
    return True


def _grade_number(name):
    if not name:
        return None
    parts = str(name).split()
    if len(parts) >= 2 and parts[0].lower() == "grade":
        try:
            return int(parts[1])
        except ValueError:
            return None
    return None


def _campus(raw):
    campus = Campus.objects.filter(pk=raw).first()
    if campus is None:
        _invalid("campus_id", "Campus was not found.")
    return campus


def _classroom(raw):
    classroom = (
        Classroom.objects.select_related("campus", "grade_level__division", "homeroom_teacher")
        .filter(pk=raw)
        .first()
    )
    if classroom is None:
        _invalid("classroom_id", "Classroom was not found.")
    return classroom


def _status(raw, default=None):
    if raw in (None, "") and default is not None:
        return default
    value = str(raw or "").strip().lower().replace(" ", "_")
    aliases = {
        "active": Student.Status.ACTIVE,
        "honor_roll": Student.Status.HONOR_ROLL,
        "at_risk": Student.Status.AT_RISK,
        "withdrawn": Student.Status.WITHDRAWN,
        "graduated": Student.Status.GRADUATED,
    }
    if value not in aliases:
        _invalid("status", "Unknown status.")
    return aliases[value]


def _gender(raw):
    value = str(raw or "").strip().lower()
    if value in (Student.Gender.FEMALE, "f"):
        return Student.Gender.FEMALE
    if value in (Student.Gender.MALE, "m"):
        return Student.Gender.MALE
    _invalid("gender", "Gender must be female or male.")


def _note_type(raw):
    value = str(raw or "").strip().lower().replace(" ", "_")
    aliases = {
        "merit": ConductNote.NoteType.MERIT,
        "incident": ConductNote.NoteType.INCIDENT,
        "homeroom_note": ConductNote.NoteType.HOMEROOM_NOTE,
        "homeroom": ConductNote.NoteType.HOMEROOM_NOTE,
        "note": ConductNote.NoteType.HOMEROOM_NOTE,
    }
    if value not in aliases:
        _invalid("note_type", "Unknown conduct note type.")
    return aliases[value]


def _severity(raw):
    value = str(raw or "").strip().lower()
    if value not in SEVERITY_LABEL:
        _invalid("severity", "Severity must be low, medium, or high.")
    return value


def _date(raw, field):
    if isinstance(raw, date):
        return raw
    parsed = parse_date(str(raw or "").strip())
    if parsed is None:
        _invalid(field, "Enter a valid date.")
    return parsed


def _phone(raw, field):
    try:
        return normalize_liberian_phone(str(raw or ""))
    except InvalidLiberianPhone as exc:
        raise ValidationError({field: [str(exc)]}) from exc


def _bool(raw, default=False):
    if raw in (None, ""):
        return default
    if isinstance(raw, bool):
        return raw
    value = str(raw).strip().lower()
    if value in {"1", "true", "yes", "on"}:
        return True
    if value in {"0", "false", "no", "off"}:
        return False
    return default


def _required_text(raw, field, *, max_length=200):
    value = _text(raw, field, max_length=max_length)
    if not value:
        _invalid(field, "This field is required.")
    return value


def _text(raw, field, *, max_length=2000):
    if raw is None:
        return ""
    value = str(raw).strip()
    if len(value) > max_length:
        _invalid(field, f"Must be at most {max_length} characters.")
    return value


def _invalid(field, message):
    raise ValidationError({field: [message]})
