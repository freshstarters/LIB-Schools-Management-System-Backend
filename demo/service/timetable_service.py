"""Weekly timetable conflicts, uncovered slots, absences, and substitutions."""

from datetime import date

from django.db import transaction
from django.db.models import Q
from django.utils import timezone
from django.utils.dateparse import parse_date
from rest_framework.exceptions import ValidationError

from core.models import RoleMembership, User
from demo.models import (
    ClassSubjectAssignment,
    Classroom,
    Subject,
    TeacherAbsence,
    TeacherSubstitution,
    TimetableSlot,
)

DAYS = tuple(choice[0] for choice in TimetableSlot.Day.choices)
DAY_LABEL = {choice[0]: choice[1] for choice in TimetableSlot.Day.choices}
PERIOD_ORDINAL = {
    1: "1st",
    2: "2nd",
    3: "3rd",
    4: "4th",
    5: "5th",
    6: "6th",
    7: "7th",
    8: "8th",
}
WEEKDAY_TO_DAY = {
    0: TimetableSlot.Day.MONDAY,
    1: TimetableSlot.Day.TUESDAY,
    2: TimetableSlot.Day.WEDNESDAY,
    3: TimetableSlot.Day.THURSDAY,
    4: TimetableSlot.Day.FRIDAY,
}


def monrovia_today():
    return timezone.localdate()


def period_label(period_number):
    return f"{PERIOD_ORDINAL[period_number]} Period"


def slot_when(day_of_week, period_number):
    return f"{DAY_LABEL[day_of_week]} {period_label(period_number)}"


def classroom_display(classroom):
    return classroom.display_name


def subject_payload(subject):
    return {
        "id": str(subject.pk),
        "code": subject.code,
        "name": subject.name,
        "division": None if subject.division_id is None else subject.division.name,
        "is_core": subject.is_core,
        "is_waec_lab": subject.is_waec_lab,
    }


def teacher_payload(user):
    if user is None:
        return None
    return {
        "id": str(user.pk),
        "display_name": user.display_name or user.email,
    }


def assignment_payload(row):
    return {
        "id": str(row.pk),
        "subject": subject_payload(row.subject),
        "teacher": teacher_payload(row.teacher),
    }


def classroom_payload(classroom, assignments=None):
    grade = classroom.grade_level
    return {
        "id": str(classroom.pk),
        "name": classroom.display_name,
        "campus_id": str(classroom.campus_id),
        "campus_name": classroom.campus.name,
        "academic_year_id": str(classroom.academic_year_id),
        "academic_year_label": classroom.academic_year.label,
        "grade_level": {
            "id": str(grade.pk),
            "name": grade.name,
            "division": grade.division.name,
        },
        "section_name": classroom.section_name,
        "homeroom_teacher": teacher_payload(classroom.homeroom_teacher),
        "capacity": classroom.capacity,
        "room_label": classroom.room_label,
        "enrolled": 0,
        "subjects": [
            assignment_payload(row)
            for row in (assignments if assignments is not None else classroom.subject_assignments.select_related("subject__division", "teacher"))
        ],
        "updated_at": classroom.updated_at,
    }


def slot_payload(slot):
    return {
        "id": str(slot.pk),
        "classroom_id": str(slot.classroom_id),
        "classroom_name": slot.classroom.display_name,
        "campus_id": str(slot.classroom.campus_id),
        "day_of_week": slot.day_of_week,
        "period_number": slot.period_number,
        "period_label": period_label(slot.period_number),
        "subject": subject_payload(slot.subject),
        "teacher": teacher_payload(slot.teacher),
        "room_label": slot.room_label,
        "updated_at": slot.updated_at,
    }


def absence_payload(row):
    return {
        "id": str(row.pk),
        "teacher": teacher_payload(row.teacher),
        "date": row.date.isoformat(),
        "reason": row.reason,
    }


def substitution_payload(row):
    return {
        "id": str(row.pk),
        "slot_id": str(row.slot_id),
        "date": row.date.isoformat(),
        "substitute_teacher": teacher_payload(row.substitute_teacher),
        "reason": row.reason,
        "slot": slot_payload(row.slot),
    }


def uncovered_payload(slot, on_date, reason):
    body = slot_payload(slot)
    body["date"] = on_date.isoformat()
    body["reason"] = reason
    return body


def list_teacher_options(campus_id=None):
    query = User.objects.filter(
        app_scope=User.AppScope.DEMO,
        memberships__role=RoleMembership.Role.TEACHER,
        is_active=True,
    ).distinct()
    if campus_id:
        query = query.filter(
            Q(memberships__campus_id=None) | Q(memberships__campus_id=campus_id),
            memberships__role=RoleMembership.Role.TEACHER,
        ).distinct()
    return [
        teacher_payload(user)
        for user in query.order_by("display_name", "email")
    ]


def classrooms_for_actor(memberships):
    roles = {row.role for row in memberships}
    if RoleMembership.Role.PARENT in roles or RoleMembership.Role.STUDENT in roles:
        if not roles.intersection(
            {
                RoleMembership.Role.PRINCIPAL,
                RoleMembership.Role.REGISTRAR,
                RoleMembership.Role.BURSAR,
                RoleMembership.Role.TEACHER,
            }
        ):
            return Classroom.objects.none()
    query = Classroom.objects.select_related(
        "campus",
        "academic_year",
        "grade_level__division",
        "homeroom_teacher",
    ).prefetch_related("subject_assignments__subject__division", "subject_assignments__teacher")
    if roles.intersection(
        {
            RoleMembership.Role.PRINCIPAL,
            RoleMembership.Role.REGISTRAR,
            RoleMembership.Role.BURSAR,
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
            }
            for row in memberships
        )
        if school_wide or not campus_ids:
            return query
        return query.filter(campus_id__in=campus_ids)
    if RoleMembership.Role.TEACHER in roles:
        user_ids = {row.user_id for row in memberships}
        return query.filter(
            Q(homeroom_teacher_id__in=user_ids)
            | Q(subject_assignments__teacher_id__in=user_ids)
            | Q(timetable_slots__teacher_id__in=user_ids)
            | Q(timetable_slots__substitutions__substitute_teacher_id__in=user_ids)
        ).distinct()
    return Classroom.objects.none()


def slots_for_actor(memberships, *, classroom_id=None, teacher_id=None):
    query = TimetableSlot.objects.select_related(
        "classroom__campus",
        "classroom__grade_level",
        "subject__division",
        "teacher",
    )
    allowed = classrooms_for_actor(memberships)
    query = query.filter(classroom__in=allowed)
    if classroom_id:
        query = query.filter(classroom_id=classroom_id)
    if teacher_id:
        query = query.filter(
            Q(teacher_id=teacher_id)
            | Q(substitutions__substitute_teacher_id=teacher_id)
        ).distinct()
    return query


def create_slot(payload):
    classroom = _classroom(payload.get("classroom_id"))
    day = _day(payload.get("day_of_week"))
    period = _period(payload.get("period_number"))
    subject = _assigned_subject(classroom, payload.get("subject_id"))
    teacher = _optional_teacher(payload.get("teacher_id"))
    room = _text(payload.get("room_label"), "room_label") or classroom.room_label
    if TimetableSlot.objects.filter(
        classroom=classroom,
        day_of_week=day,
        period_number=period,
    ).exists():
        _invalid("period_number", f"{slot_when(day, period)} already has a slot for this classroom.")
    with transaction.atomic():
        slot = TimetableSlot(
            classroom=classroom,
            day_of_week=day,
            period_number=period,
            subject=subject,
            teacher=teacher,
            room_label=room,
        )
        _assert_slot_free(slot)
        slot.save()
    return slot


def update_slot(slot, payload):
    if "subject_id" in payload:
        slot.subject = _assigned_subject(slot.classroom, payload.get("subject_id"))
    if "teacher_id" in payload:
        slot.teacher = _optional_teacher(payload.get("teacher_id"))
    if "room_label" in payload:
        slot.room_label = _text(payload.get("room_label"), "room_label")
    if "day_of_week" in payload or "period_number" in payload:
        day = _day(payload.get("day_of_week", slot.day_of_week))
        period = _period(payload.get("period_number", slot.period_number))
        clash = (
            TimetableSlot.objects.filter(
                classroom=slot.classroom,
                day_of_week=day,
                period_number=period,
            )
            .exclude(pk=slot.pk)
            .exists()
        )
        if clash:
            _invalid("period_number", f"{slot_when(day, period)} already has a slot for this classroom.")
        slot.day_of_week = day
        slot.period_number = period
    with transaction.atomic():
        _assert_slot_free(slot, exclude_id=slot.pk)
        slot.save()
    return slot


def delete_slot(slot):
    slot.delete()


def uncovered_slots(on_date=None):
    on_date = on_date or monrovia_today()
    day = WEEKDAY_TO_DAY.get(on_date.weekday())
    if day is None:
        return [], 0
    covered = set(
        TeacherSubstitution.objects.filter(date=on_date).values_list("slot_id", flat=True)
    )
    absent = set(
        TeacherAbsence.objects.filter(date=on_date).values_list("teacher_id", flat=True)
    )
    rows = []
    slots = TimetableSlot.objects.filter(day_of_week=day).select_related(
        "classroom__campus",
        "classroom__grade_level",
        "subject__division",
        "teacher",
    )
    for slot in slots:
        if slot.pk in covered:
            continue
        if slot.teacher_id is None:
            rows.append(uncovered_payload(slot, on_date, "unassigned"))
        elif slot.teacher_id in absent:
            rows.append(uncovered_payload(slot, on_date, "absent"))
    return rows, len(rows)


def upsert_absence(payload):
    teacher = _required_teacher(payload.get("teacher_id"))
    on_date = _date_value(payload.get("date"), "date")
    reason = _text(payload.get("reason"), "reason")
    row, _created = TeacherAbsence.objects.update_or_create(
        teacher=teacher,
        date=on_date,
        defaults={"reason": reason},
    )
    rows, count = uncovered_slots(on_date)
    return {
        "absence": absence_payload(row),
        "uncovered": rows,
        "count": count,
    }


def delete_absence(*, absence_id=None, teacher_id=None, on_date=None):
    if absence_id:
        row = TeacherAbsence.objects.filter(pk=absence_id).first()
        if row is None:
            _invalid("id", "Absence was not found.")
        day = row.date
        row.delete()
        rows, count = uncovered_slots(day)
        return {"uncovered": rows, "count": count}
    teacher = _required_teacher(teacher_id)
    day = _date_value(on_date, "date")
    TeacherAbsence.objects.filter(teacher=teacher, date=day).delete()
    rows, count = uncovered_slots(day)
    return {"uncovered": rows, "count": count}


def create_substitution(payload):
    slot = _slot(payload.get("slot_id"))
    on_date = _date_value(payload.get("date"), "date")
    day = WEEKDAY_TO_DAY.get(on_date.weekday())
    if day is None or day != slot.day_of_week:
        _invalid("date", "The substitution date must fall on the slot's weekday.")
    substitute = _required_teacher(
        payload.get("substitute_teacher_id"),
        field="substitute_teacher_id",
    )
    if slot.teacher_id and substitute.pk == slot.teacher_id:
        _invalid("substitute_teacher_id", "The substitute cannot be the assigned teacher.")
    reason = _text(payload.get("reason"), "reason")
    with transaction.atomic():
        _assert_substitute_free(slot, on_date, substitute)
        row, _created = TeacherSubstitution.objects.update_or_create(
            slot=slot,
            date=on_date,
            defaults={
                "substitute_teacher": substitute,
                "reason": reason,
            },
        )
    rows, count = uncovered_slots(on_date)
    return {
        "substitution": substitution_payload(row),
        "uncovered": rows,
        "count": count,
    }


def list_substitutions(*, on_date=None, slot_id=None):
    query = TeacherSubstitution.objects.select_related(
        "substitute_teacher",
        "slot__classroom__campus",
        "slot__classroom__grade_level",
        "slot__subject__division",
        "slot__teacher",
    )
    if on_date is not None:
        query = query.filter(date=on_date)
    if slot_id is not None:
        query = query.filter(slot_id=slot_id)
    return [substitution_payload(row) for row in query]


def _assert_slot_free(slot, exclude_id=None):
    if slot.teacher_id:
        busy = TimetableSlot.objects.filter(
            teacher_id=slot.teacher_id,
            day_of_week=slot.day_of_week,
            period_number=slot.period_number,
        )
        if exclude_id:
            busy = busy.exclude(pk=exclude_id)
        other = busy.select_related("classroom__grade_level").first()
        if other is not None:
            _invalid(
                "teacher",
                (
                    f"Teacher is already assigned to {classroom_display(other.classroom)} "
                    f"on {slot_when(slot.day_of_week, slot.period_number)}."
                ),
            )
    room = (slot.room_label or "").strip()
    if room:
        busy_room = TimetableSlot.objects.filter(
            classroom__campus_id=slot.classroom.campus_id,
            day_of_week=slot.day_of_week,
            period_number=slot.period_number,
            room_label__iexact=room,
        )
        if exclude_id:
            busy_room = busy_room.exclude(pk=exclude_id)
        other = busy_room.select_related("classroom__grade_level").first()
        if other is not None:
            _invalid(
                "room_label",
                (
                    f"Room {room} is already used by {classroom_display(other.classroom)} "
                    f"on {slot_when(slot.day_of_week, slot.period_number)}."
                ),
            )


def _assert_substitute_free(slot, on_date, substitute):
    teaching = TimetableSlot.objects.filter(
        teacher_id=substitute.pk,
        day_of_week=slot.day_of_week,
        period_number=slot.period_number,
    ).exclude(pk=slot.pk).select_related("classroom__grade_level").first()
    if teaching is not None:
        _invalid(
            "substitute_teacher_id",
            (
                f"Teacher is already assigned to {classroom_display(teaching.classroom)} "
                f"on {slot_when(slot.day_of_week, slot.period_number)}."
            ),
        )
    covering = (
        TeacherSubstitution.objects.filter(
            substitute_teacher_id=substitute.pk,
            date=on_date,
            slot__day_of_week=slot.day_of_week,
            slot__period_number=slot.period_number,
        )
        .exclude(slot_id=slot.pk)
        .select_related("slot__classroom__grade_level")
        .first()
    )
    if covering is not None:
        _invalid(
            "substitute_teacher_id",
            (
                f"Teacher is already substituting for {classroom_display(covering.slot.classroom)} "
                f"on {slot_when(slot.day_of_week, slot.period_number)}."
            ),
        )


def _classroom(raw):
    classroom = (
        Classroom.objects.select_related("campus", "grade_level", "academic_year")
        .filter(pk=raw)
        .first()
    )
    if classroom is None:
        _invalid("classroom_id", "Classroom was not found.")
    return classroom


def _slot(raw):
    slot = (
        TimetableSlot.objects.select_related(
            "classroom__campus",
            "classroom__grade_level",
            "subject__division",
            "teacher",
        )
        .filter(pk=raw)
        .first()
    )
    if slot is None:
        _invalid("slot_id", "Timetable slot was not found.")
    return slot


def _assigned_subject(classroom, raw):
    subject = Subject.objects.filter(pk=raw).first()
    if subject is None:
        _invalid("subject_id", "Subject was not found.")
    if not ClassSubjectAssignment.objects.filter(classroom=classroom, subject=subject).exists():
        _invalid("subject_id", "Subject is not assigned to this classroom.")
    return subject


def _day(raw):
    value = str(raw or "").strip().lower()
    if value not in DAYS:
        _invalid("day_of_week", "Day must be monday through friday.")
    return value


def _period(raw):
    try:
        value = int(raw)
    except (TypeError, ValueError):
        _invalid("period_number", "Period must be a number from 1 to 8.")
    if value < 1 or value > 8:
        _invalid("period_number", "Period must be a number from 1 to 8.")
    return value


def _optional_teacher(raw, field="teacher_id"):
    if raw in (None, ""):
        return None
    return _required_teacher(raw, field=field)


def _required_teacher(raw, field="teacher_id"):
    user = User.objects.filter(pk=raw, app_scope=User.AppScope.DEMO, is_active=True).first()
    if user is None:
        _invalid(field, "Teacher was not found.")
    if not user.memberships.filter(role=RoleMembership.Role.TEACHER).exists():
        _invalid(field, "User is not a teacher.")
    return user


def _date_value(raw, field):
    if isinstance(raw, date):
        return raw
    if raw in (None, ""):
        _invalid(field, "Enter a valid date.")
    parsed = parse_date(str(raw))
    if parsed is None:
        _invalid(field, "Enter a valid date.")
    return parsed


def _text(raw, field):
    if raw is None:
        return ""
    if not isinstance(raw, str):
        _invalid(field, "Enter text.")
    return raw.strip()


def _invalid(field, message):
    raise ValidationError({field: [message]})
