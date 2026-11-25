"""Teachers and administrative staff directory."""

from django.db import transaction
from django.db.models import Q
from django.utils import timezone
from rest_framework.exceptions import ValidationError

from core.helpers.errors import EnvelopeError
from core.helpers.phone import InvalidLiberianPhone, normalize_liberian_phone
from core.models import InviteToken, RoleMembership, User
from core.service.auth import DOOR_DEMO
from core.service.onboarding import issue_invite
from demo.models import (
    Campus,
    ClassSubjectAssignment,
    Classroom,
    Division,
    StaffProfile,
)

ROLE_LABEL = {choice[0]: choice[1] for choice in StaffProfile.Role.choices}
STATUS_LABEL = {choice[0]: choice[1] for choice in StaffProfile.EmploymentStatus.choices}
STAFF_ROLES = frozenset(StaffProfile.Role.values)
WRITE_ROLES = frozenset({RoleMembership.Role.PRINCIPAL})
READ_ROLES = frozenset(
    {
        RoleMembership.Role.PRINCIPAL,
        RoleMembership.Role.REGISTRAR,
        RoleMembership.Role.BURSAR,
        RoleMembership.Role.TEACHER,
        RoleMembership.Role.SUPPORT_STAFF,
    }
)


def allocate_staff_code(*, year=None):
    year = year or timezone.localdate().year
    prefix = f"STF-{year}-"
    with transaction.atomic():
        latest = (
            StaffProfile.objects.select_for_update()
            .filter(staff_code__startswith=prefix)
            .order_by("-staff_code")
            .values_list("staff_code", flat=True)
            .first()
        )
        next_number = 1
        if latest:
            try:
                next_number = int(latest.rsplit("-", 1)[-1]) + 1
            except ValueError:
                next_number = 1
        if next_number > 9999:
            _invalid("staff_code", "Staff ID sequence for this year is exhausted.")
        return f"{prefix}{next_number:04d}"


def staff_for_actor(memberships):
    roles = {row.role for row in memberships}
    if not roles.intersection(READ_ROLES):
        return StaffProfile.objects.none()
    return StaffProfile.objects.select_related("campus", "division", "user").all()


def filtered_staff(memberships, params):
    query = staff_for_actor(memberships)
    q = str(params.get("q") or "").strip()
    if q:
        query = query.filter(
            Q(full_name__icontains=q)
            | Q(staff_code__icontains=q)
            | Q(job_title__icontains=q)
            | Q(email__icontains=q)
            | Q(phone__icontains=q)
        )
    campus = str(params.get("campus") or "").strip()
    if campus:
        query = query.filter(campus_id=campus)
    division = str(params.get("division") or "").strip()
    if division:
        query = query.filter(Q(division_id=division) | Q(division__name=division))
    return query.order_by("staff_code")


def get_staff(memberships, staff_id):
    return staff_for_actor(memberships).filter(pk=staff_id).first()


def create_staff(payload):
    profile = StaffProfile.objects.create(
        staff_code=allocate_staff_code(),
        full_name=_required_text(payload.get("full_name"), "full_name", max_length=160),
        role=_role(payload.get("role")),
        job_title=_text(payload.get("job_title"), "job_title", max_length=120),
        division=_optional_division(payload.get("division_id")),
        campus=_optional_campus(payload.get("campus_id")),
        qualifications=_text(payload.get("qualifications"), "qualifications", max_length=4000),
        phone=_phone(payload.get("phone"), "phone"),
        email=_email(payload.get("email"), "email"),
        employment_status=StaffProfile.EmploymentStatus.ACTIVE,
    )
    return staff_payload(profile)


def update_staff(profile, payload):
    if "full_name" in payload:
        profile.full_name = _required_text(payload.get("full_name"), "full_name", max_length=160)
    if "role" in payload:
        profile.role = _role(payload.get("role"))
    if "job_title" in payload:
        profile.job_title = _text(payload.get("job_title"), "job_title", max_length=120)
    if "division_id" in payload:
        profile.division = _optional_division(payload.get("division_id"))
    if "campus_id" in payload:
        profile.campus = _optional_campus(payload.get("campus_id"))
    if "qualifications" in payload:
        profile.qualifications = _text(
            payload.get("qualifications"),
            "qualifications",
            max_length=4000,
        )
    if "phone" in payload:
        profile.phone = _phone(payload.get("phone"), "phone")
    if "email" in payload:
        profile.email = _email(payload.get("email"), "email")
    profile.save()
    return staff_payload(profile)


def deactivate_staff(profile):
    profile.employment_status = StaffProfile.EmploymentStatus.INACTIVE
    profile.save(update_fields=["employment_status", "updated_at"])
    if profile.user_id:
        user = profile.user
        if user.is_active:
            user.is_active = False
            user.save(update_fields=["is_active"])
    return staff_payload(profile)


def reactivate_staff(profile):
    profile.employment_status = StaffProfile.EmploymentStatus.ACTIVE
    profile.save(update_fields=["employment_status", "updated_at"])
    if profile.user_id:
        user = profile.user
        if user.has_usable_password() and not user.is_active:
            user.is_active = True
            user.save(update_fields=["is_active"])
    return staff_payload(profile)


def send_staff_invite(*, actor, profile):
    if profile.employment_status == StaffProfile.EmploymentStatus.INACTIVE:
        raise EnvelopeError(400, "staff_inactive", "Reactivate this staff member before inviting.")
    if not profile.email:
        _invalid("email", "Email is required to send an invite.")
    result = issue_invite(
        actor=actor,
        door=DOOR_DEMO,
        school=None,
        email=profile.email,
        role=profile.role,
        display_name=profile.full_name,
        phone=profile.phone,
    )
    user = User.objects.filter(pk=result["user_id"]).first()
    if user is not None and profile.user_id != user.pk:
        profile.user = user
        profile.save(update_fields=["user", "updated_at"])
    return staff_payload(profile)


def staff_payload(profile):
    return {
        "id": str(profile.pk),
        "staff_code": profile.staff_code,
        "full_name": profile.full_name,
        "role": profile.role,
        "role_label": ROLE_LABEL.get(profile.role, profile.role),
        "job_title": profile.job_title,
        "division_id": str(profile.division_id) if profile.division_id else None,
        "division": profile.division.name if profile.division_id else None,
        "campus_id": str(profile.campus_id) if profile.campus_id else None,
        "campus_name": profile.campus.name if profile.campus_id else None,
        "qualifications": profile.qualifications,
        "phone": profile.phone,
        "email": profile.email,
        "employment_status": profile.employment_status,
        "employment_status_label": STATUS_LABEL.get(
            profile.employment_status,
            profile.employment_status,
        ),
        "portal_status": portal_status(profile),
        "user_id": str(profile.user_id) if profile.user_id else None,
        "homerooms": _homerooms(profile),
        "subjects": _subjects(profile),
        "updated_at": profile.updated_at.isoformat(),
    }


def portal_status(profile):
    if profile.employment_status == StaffProfile.EmploymentStatus.INACTIVE:
        return "inactive"
    user = profile.user
    if user is None:
        return "not_invited"
    if user.is_active:
        return "active"
    now = timezone.now()
    if InviteToken.objects.filter(
        user=user,
        used_at__isnull=True,
        expires_at__gt=now,
    ).exists():
        return "invited"
    return "not_invited"


def _homerooms(profile):
    if not profile.user_id:
        return []
    rows = (
        Classroom.objects.select_related("grade_level", "campus")
        .filter(homeroom_teacher_id=profile.user_id)
        .order_by("grade_level__sort_order", "section_name")
    )
    return [
        {
            "id": str(row.pk),
            "name": row.display_name,
            "campus_name": row.campus.name,
        }
        for row in rows
    ]


def _subjects(profile):
    if not profile.user_id:
        return []
    rows = (
        ClassSubjectAssignment.objects.select_related(
            "subject",
            "classroom__grade_level",
            "classroom__campus",
        )
        .filter(teacher_id=profile.user_id)
        .order_by("subject__sort_order", "subject__name")
    )
    return [
        {
            "id": str(row.pk),
            "subject": row.subject.name,
            "classroom": row.classroom.display_name,
            "campus_name": row.classroom.campus.name,
        }
        for row in rows
    ]


def _role(raw):
    value = str(raw or "").strip().lower()
    if value not in STAFF_ROLES:
        _invalid("role", "Choose a valid staff role.")
    return value


def _optional_division(raw):
    if raw in (None, ""):
        return None
    text = str(raw).strip()
    division = Division.objects.filter(pk=text).first()
    if division is None:
        division = Division.objects.filter(name=text).first()
    if division is None:
        _invalid("division_id", "Division was not found.")
    return division


def _optional_campus(raw):
    if raw in (None, ""):
        return None
    campus = Campus.objects.filter(pk=raw).first()
    if campus is None:
        _invalid("campus_id", "Campus was not found.")
    return campus


def _email(raw, field):
    value = _required_text(raw, field, max_length=254).lower()
    if "@" not in value:
        _invalid(field, "Enter a valid email.")
    return value


def _phone(raw, field):
    try:
        return normalize_liberian_phone(str(raw or ""))
    except InvalidLiberianPhone as exc:
        raise ValidationError({field: [str(exc)]}) from exc


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
