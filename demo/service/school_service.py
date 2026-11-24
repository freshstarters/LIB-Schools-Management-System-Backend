"""Gojo school profile rules and the seeded calendar."""

from datetime import date
from decimal import Decimal, InvalidOperation

from django.db import transaction
from django.db.models import Count, Q
from django.utils.dateparse import parse_date
from django.utils.text import slugify
from rest_framework.exceptions import ValidationError

from core.helpers.errors import EnvelopeError
from core.helpers.permissions import PRINCIPAL
from core.models import RoleMembership
from demo.models import (
    AcademicYear,
    Campus,
    Division,
    GradeLevel,
    MarkingPeriod,
    SchoolConfig,
)

ACTIVE_CODES = frozenset({"P1", "P2", "P3", "P4", "P5", "P6"})
PERIOD_ORDER = (
    "P1",
    "P2",
    "P3",
    "SEM1_EXAM",
    "P4",
    "P5",
    "P6",
    "SEM2_EXAM",
)
PERIOD_SEMESTER = {
    "P1": 1,
    "P2": 1,
    "P3": 1,
    "SEM1_EXAM": 1,
    "P4": 2,
    "P5": 2,
    "P6": 2,
    "SEM2_EXAM": 2,
}
BURSAR_FIELDS = frozenset(
    {
        "lrd_per_usd",
        "pass_threshold",
        "honor_roll_threshold",
        "marking_period_weight",
        "semester_exam_weight",
    }
)

DIVISIONS = (
    ("ece", "ECE", 1),
    ("elementary", "Elementary", 2),
    ("junior-high", "Junior High", 3),
    ("senior-high", "Senior High", 4),
)
GRADE_LEVELS = (
    ("Nursery", "ece", 1),
    ("ABC", "ece", 2),
    ("K1", "ece", 3),
    ("K2", "ece", 4),
    ("Grade 1", "elementary", 5),
    ("Grade 2", "elementary", 6),
    ("Grade 3", "elementary", 7),
    ("Grade 4", "elementary", 8),
    ("Grade 5", "elementary", 9),
    ("Grade 6", "elementary", 10),
    ("Grade 7", "junior-high", 11),
    ("Grade 8", "junior-high", 12),
    ("Grade 9", "junior-high", 13),
    ("Grade 10", "senior-high", 14),
    ("Grade 11", "senior-high", 15),
    ("Grade 12", "senior-high", 16),
)
SEEDED_PERIODS = (
    ("P1", "2026-09-07", "2026-10-16"),
    ("P2", "2026-10-19", "2026-11-27"),
    ("P3", "2026-11-30", "2027-01-22"),
    ("SEM1_EXAM", "2027-01-25", "2027-02-05"),
    ("P4", "2027-02-08", "2027-03-19"),
    ("P5", "2027-03-22", "2027-04-30"),
    ("P6", "2027-05-03", "2027-06-11"),
    ("SEM2_EXAM", "2027-06-14", "2027-06-25"),
)
CAMPUSES = (
    {
        "code": "sinkor",
        "name": "Sinkor Main Campus",
        "address": "Tubman Boulevard, Sinkor, Monrovia",
        "catchment_zone": "Sinkor & Central",
        "lead_staff": "Dr. Samuel Kollie",
        "grades": tuple(f"Grade {number}" for number in range(1, 13)),
    },
    {
        "code": "paynesville",
        "name": "Paynesville Campus",
        "address": "Red Light, Paynesville",
        "catchment_zone": "Paynesville & ELWA",
        "lead_staff": "Beatrice Toe",
        "grades": tuple(f"Grade {number}" for number in range(1, 13)),
    },
    {
        "code": "gardnersville",
        "name": "Gardnersville Campus",
        "address": "Gardnersville, Monrovia",
        "catchment_zone": "Gardnersville & Bushrod",
        "lead_staff": "Grace Johnson",
        "grades": ("Nursery", "ABC", "K1", "K2"),
    },
)


def period_label(code):
    return MarkingPeriod.Code(code).label


def ensure_school_profile():
    """Create the Gojo profile when it is missing. Existing rows are left as edited."""
    with transaction.atomic():
        divisions = {}
        for code, name, order in DIVISIONS:
            division, _created = Division.objects.get_or_create(
                code=code,
                defaults={"name": name, "sort_order": order},
            )
            divisions[code] = division
        grades = {}
        for name, division_code, order in GRADE_LEVELS:
            grade, _created = GradeLevel.objects.get_or_create(
                name=name,
                defaults={"division": divisions[division_code], "sort_order": order},
            )
            grades[name] = grade
        year, _created = AcademicYear.objects.get_or_create(
            label="2026/2027",
            defaults={"is_current": True},
        )
        for code, start, end in SEEDED_PERIODS:
            MarkingPeriod.objects.get_or_create(
                academic_year=year,
                code=code,
                defaults={
                    "semester": PERIOD_SEMESTER[code],
                    "start_date": start,
                    "end_date": end,
                    "is_locked": False,
                },
            )
        for spec in CAMPUSES:
            campus, _created = Campus.objects.get_or_create(
                code=spec["code"],
                defaults={
                    "name": spec["name"],
                    "address": spec["address"],
                    "catchment_zone": spec["catchment_zone"],
                    "lead_staff": spec["lead_staff"],
                },
            )
            if not campus.grades_served.exists():
                campus.grades_served.set(grades[name] for name in spec["grades"])
        active = year.marking_periods.get(code="P2")
        SchoolConfig.objects.get_or_create(
            singleton=True,
            defaults={
                "school_name": "Gojo Institute",
                "moe_registration_code": "",
                "lrd_per_usd": Decimal("192.00"),
                "pass_threshold": Decimal("70.00"),
                "honor_roll_threshold": Decimal("85.00"),
                "marking_period_weight": Decimal("60.00"),
                "semester_exam_weight": Decimal("40.00"),
                "academic_year": year,
                "active_marking_period": active,
            },
        )


def config_payload(config):
    year = config.academic_year
    period = config.active_marking_period
    return {
        "id": str(config.pk),
        "school_name": config.school_name,
        "moe_registration_code": config.moe_registration_code,
        "lrd_per_usd": _money(config.lrd_per_usd),
        "pass_threshold": _money(config.pass_threshold),
        "honor_roll_threshold": _money(config.honor_roll_threshold),
        "marking_period_weight": _money(config.marking_period_weight),
        "semester_exam_weight": _money(config.semester_exam_weight),
        "academic_year_id": None if year is None else str(year.pk),
        "active_marking_period_id": None if period is None else str(period.pk),
        "updated_at": config.updated_at,
    }


def grade_payload(grade):
    return {
        "id": str(grade.pk),
        "name": grade.name,
        "division": grade.division.name,
    }


def campus_counts():
    rows = (
        RoleMembership.objects.exclude(campus_id=None)
        .values("campus_id")
        .annotate(
            student=Count("id", filter=Q(role=RoleMembership.Role.STUDENT)),
            teacher=Count("id", filter=Q(role=RoleMembership.Role.TEACHER)),
        )
    )
    return {
        row["campus_id"]: {"student": row["student"], "teacher": row["teacher"]}
        for row in rows
    }


def campus_payload(campus, grades, counts):
    bucket = counts.get(campus.pk, {"student": 0, "teacher": 0})
    return {
        "id": str(campus.pk),
        "code": campus.code,
        "name": campus.name,
        "address": campus.address,
        "catchment_zone": campus.catchment_zone,
        "lead_staff": campus.lead_staff,
        "grades_served": [grade_payload(grade) for grade in grades],
        "student_count": bucket["student"],
        "teacher_count": bucket["teacher"],
        "updated_at": campus.updated_at,
    }


def period_payload(period):
    return {
        "id": str(period.pk),
        "code": period.code,
        "label": period_label(period.code),
        "semester": period.semester,
        "start_date": period.start_date,
        "end_date": period.end_date,
        "is_locked": period.is_locked,
    }


def year_payload(year, periods):
    ordered = sorted(periods, key=lambda period: PERIOD_ORDER.index(period.code))
    return {
        "id": str(year.pk),
        "label": year.label,
        "is_current": year.is_current,
        "marking_periods": [period_payload(period) for period in ordered],
        "updated_at": year.updated_at,
    }


def update_settings(actor_memberships, payload, config):
    if not _is_principal(actor_memberships):
        extra = set(payload) - BURSAR_FIELDS
        if extra:
            raise EnvelopeError(
                403,
                "permission_denied",
                "You do not have permission to perform this action.",
            )
    if "school_name" in payload:
        name = str(payload["school_name"]).strip()
        if not name:
            _invalid("school_name", "School name is required.")
        config.school_name = name
    if "moe_registration_code" in payload:
        config.moe_registration_code = str(payload["moe_registration_code"]).strip()
    if "lrd_per_usd" in payload:
        config.lrd_per_usd = _positive_rate(payload["lrd_per_usd"])
    if "pass_threshold" in payload:
        config.pass_threshold = _percent(payload["pass_threshold"], "pass_threshold")
    if "honor_roll_threshold" in payload:
        config.honor_roll_threshold = _percent(
            payload["honor_roll_threshold"],
            "honor_roll_threshold",
        )
    if "marking_period_weight" in payload or "semester_exam_weight" in payload:
        marking = (
            _percent(payload["marking_period_weight"], "marking_period_weight")
            if "marking_period_weight" in payload
            else config.marking_period_weight
        )
        exam = (
            _percent(payload["semester_exam_weight"], "semester_exam_weight")
            if "semester_exam_weight" in payload
            else config.semester_exam_weight
        )
        if marking + exam != Decimal("100.00"):
            _invalid("marking_period_weight", "Grading weights must sum to 100%.")
        config.marking_period_weight = marking
        config.semester_exam_weight = exam
    if "academic_year_id" in payload:
        year = _year(payload["academic_year_id"])
        AcademicYear.objects.exclude(pk=year.pk).update(is_current=False)
        if not year.is_current:
            year.is_current = True
            year.save(update_fields=["is_current", "updated_at"])
        config.academic_year = year
        if config.active_marking_period_id and config.active_marking_period.academic_year_id != year.pk:
            config.active_marking_period = None
    if "active_marking_period_id" in payload:
        config.active_marking_period = _active_period(
            payload["active_marking_period_id"],
            config.academic_year,
        )
    config.save()
    return config


def create_campus(payload):
    name = str(payload.get("name", "")).strip()
    if not name:
        _invalid("name", "Campus name is required.")
    code = _unique_code(slugify(name) or "campus")
    campus = Campus.objects.create(
        code=code,
        name=name,
        address=str(payload.get("address", "")).strip(),
        catchment_zone=str(payload.get("catchment_zone", "")).strip(),
        lead_staff=str(payload.get("lead_staff", "")).strip(),
    )
    campus.grades_served.set(_grades(payload.get("grade_level_ids", [])))
    return campus


def update_campus(campus, payload):
    if "name" in payload:
        name = str(payload["name"]).strip()
        if not name:
            _invalid("name", "Campus name is required.")
        campus.name = name
    if "address" in payload:
        campus.address = str(payload["address"]).strip()
    if "catchment_zone" in payload:
        campus.catchment_zone = str(payload["catchment_zone"]).strip()
    if "lead_staff" in payload:
        campus.lead_staff = str(payload["lead_staff"]).strip()
    campus.save()
    if "grade_level_ids" in payload:
        campus.grades_served.set(_grades(payload["grade_level_ids"]))
    return campus


def delete_campus(campus):
    if Campus.objects.count() <= 1:
        _invalid("campus", "The last campus cannot be deleted.")
    if RoleMembership.objects.filter(campus_id=campus.pk).exists():
        _invalid("campus", "This campus cannot be deleted until the people assigned to it are moved.")
    campus.delete()


def create_year(payload):
    label = str(payload.get("label", "")).strip()
    if not label:
        _invalid("label", "Academic year label is required.")
    if AcademicYear.objects.filter(label=label).exists():
        _invalid("label", "An academic year with this label already exists.")
    make_current = bool(payload.get("is_current", False))
    with transaction.atomic():
        if make_current:
            AcademicYear.objects.update(is_current=False)
        year = AcademicYear.objects.create(label=label, is_current=make_current)
        supplied = {item.get("code"): item for item in payload.get("marking_periods", [])}
        MarkingPeriod.objects.bulk_create(
            [
                MarkingPeriod(
                    academic_year=year,
                    code=code,
                    semester=PERIOD_SEMESTER[code],
                    start_date=_date((supplied.get(code) or {}).get("start_date"), "start_date"),
                    end_date=_date((supplied.get(code) or {}).get("end_date"), "end_date"),
                    is_locked=bool((supplied.get(code) or {}).get("is_locked", False)),
                )
                for code in PERIOD_ORDER
            ]
        )
        if make_current:
            config = SchoolConfig.objects.get(singleton=True)
            config.academic_year = year
            config.active_marking_period = None
            config.save(update_fields=["academic_year", "active_marking_period", "updated_at"])
    return year


def update_year(year, payload):
    if "label" in payload:
        label = str(payload["label"]).strip()
        if not label:
            _invalid("label", "Academic year label is required.")
        if AcademicYear.objects.exclude(pk=year.pk).filter(label=label).exists():
            _invalid("label", "An academic year with this label already exists.")
        year.label = label
    if payload.get("is_current") is True:
        AcademicYear.objects.exclude(pk=year.pk).update(is_current=False)
        year.is_current = True
        config = SchoolConfig.objects.get(singleton=True)
        config.academic_year = year
        if config.active_marking_period_id and config.active_marking_period.academic_year_id != year.pk:
            config.active_marking_period = None
        config.save(update_fields=["academic_year", "active_marking_period", "updated_at"])
    year.save()
    incoming = payload.get("marking_periods")
    if incoming is not None:
        by_code = {period.code: period for period in year.marking_periods.all()}
        for item in incoming:
            code = item.get("code")
            period = by_code.get(code)
            if period is None:
                _invalid("marking_periods", "Unknown marking period.")
            if "start_date" in item:
                period.start_date = _date(item["start_date"], "start_date")
            if "end_date" in item:
                period.end_date = _date(item["end_date"], "end_date")
            if "is_locked" in item:
                period.is_locked = bool(item["is_locked"])
            if period.start_date and period.end_date and period.end_date < period.start_date:
                _invalid("end_date", "The end date is before the start date.")
            period.save()
    return year


def delete_year(year):
    if year.is_current:
        _invalid("academic_year", "The current academic year cannot be deleted.")
    config = SchoolConfig.objects.get(singleton=True)
    if config.academic_year_id == year.pk or (
        config.active_marking_period_id and config.active_marking_period.academic_year_id == year.pk
    ):
        _invalid("academic_year", "The current academic year cannot be deleted.")
    year.delete()


def _is_principal(memberships):
    return any(membership.role == PRINCIPAL for membership in memberships)


def _year(raw):
    year = AcademicYear.objects.filter(pk=raw).first()
    if year is None:
        _invalid("academic_year_id", "Academic year was not found.")
    return year


def _active_period(raw, year):
    if raw in (None, ""):
        return None
    period = MarkingPeriod.objects.filter(pk=raw).first()
    if period is None:
        _invalid("active_marking_period_id", "Marking period was not found.")
    if period.code not in ACTIVE_CODES:
        _invalid("active_marking_period_id", "The active period must be Period 1 through Period 6.")
    if year is None or period.academic_year_id != year.pk:
        _invalid("active_marking_period_id", "The active period belongs to another academic year.")
    return period


def _grades(raw):
    if raw is None:
        raw = []
    if not isinstance(raw, list):
        _invalid("grade_level_ids", "Grade levels must be a list.")
    grades = list(GradeLevel.objects.filter(pk__in=raw))
    if len(grades) != len(set(raw)):
        _invalid("grade_level_ids", "A grade level was not found.")
    return grades


def _unique_code(base):
    code = base
    number = 2
    while Campus.objects.filter(code=code).exists():
        code = f"{base}-{number}"
        number += 1
    return code


def _positive_rate(raw):
    value = _decimal(raw, "lrd_per_usd")
    if value <= 0:
        _invalid("lrd_per_usd", "The exchange rate must be greater than zero.")
    return value


def _percent(raw, field):
    value = _decimal(raw, field)
    if value < 0 or value > Decimal("100.00"):
        _invalid(field, "Enter a percentage from 0 to 100.")
    return value


def _date(raw, field):
    if raw in (None, ""):
        return None
    if isinstance(raw, date):
        return raw
    parsed = parse_date(str(raw))
    if parsed is None:
        _invalid(field, "Enter a valid date.")
    return parsed


def _decimal(raw, field):
    try:
        value = Decimal(str(raw))
    except (InvalidOperation, ValueError):
        _invalid(field, "Enter a number.")
    return value.quantize(Decimal("0.01"))


def _money(value):
    return str(value.quantize(Decimal("0.01")))


def _invalid(field, message):
    raise ValidationError({field: [message]})
