"""Parents and guardians directory, sibling links, and portal invites."""

from django.db import transaction
from django.db.models import Q
from django.utils import timezone
from rest_framework.exceptions import ValidationError

from core.helpers.errors import EnvelopeError
from core.helpers.phone import InvalidLiberianPhone, normalize_liberian_phone
from core.models import InviteToken, RoleMembership, User
from core.service.auth import DOOR_DEMO
from core.service.onboarding import issue_invite
from demo.models import GuardianProfile, Student, StudentGuardianLink
from demo.service.timetable_service import classrooms_for_actor

RELATIONSHIP_LABEL = {
    choice[0]: choice[1] for choice in StudentGuardianLink.Relationship.choices
}
STATUS_LABEL = {choice[0]: choice[1] for choice in GuardianProfile.Status.choices}
RELATIONSHIPS = frozenset(StudentGuardianLink.Relationship.values)
WRITE_ROLES = frozenset(
    {
        RoleMembership.Role.PRINCIPAL,
        RoleMembership.Role.REGISTRAR,
    }
)
OFFICE_READ = frozenset(
    {
        RoleMembership.Role.PRINCIPAL,
        RoleMembership.Role.REGISTRAR,
        RoleMembership.Role.BURSAR,
        RoleMembership.Role.SUPPORT_STAFF,
    }
)
FINANCE_ROLES = frozenset(
    {
        RoleMembership.Role.PRINCIPAL,
        RoleMembership.Role.REGISTRAR,
        RoleMembership.Role.BURSAR,
    }
)


def guardians_for_actor(memberships, *, actor=None):
    roles = {row.role for row in memberships}
    query = GuardianProfile.objects.select_related("user").prefetch_related(
        "child_links__student__campus",
        "child_links__student__current_classroom__grade_level__division",
    )
    if roles.intersection(OFFICE_READ):
        return query
    if RoleMembership.Role.TEACHER in roles:
        classroom_ids = classrooms_for_actor(memberships).values_list("pk", flat=True)
        return query.filter(
            child_links__student__current_classroom_id__in=classroom_ids
        ).distinct()
    if RoleMembership.Role.PARENT in roles and actor is not None:
        return query.filter(user_id=actor.pk)
    return GuardianProfile.objects.none()


def filtered_guardians(memberships, params, *, actor=None):
    query = guardians_for_actor(memberships, actor=actor)
    q = str(params.get("q") or "").strip()
    if q:
        query = query.filter(
            Q(full_name__icontains=q)
            | Q(email__icontains=q)
            | Q(phone__icontains=q)
            | Q(residential_zone__icontains=q)
        )
    return query.order_by("full_name", "email")


def get_guardian(memberships, guardian_id, *, actor=None):
    return guardians_for_actor(memberships, actor=actor).filter(pk=guardian_id).first()


def create_guardian(payload):
    profile = GuardianProfile.objects.create(
        full_name=_required_text(payload.get("full_name"), "full_name", max_length=160),
        phone=_phone(payload.get("phone"), "phone"),
        whatsapp_phone=_optional_phone(payload.get("whatsapp_phone"), "whatsapp_phone"),
        email=_email(payload.get("email"), "email"),
        residential_zone=_text(
            payload.get("residential_zone"),
            "residential_zone",
            max_length=120,
        ),
        occupation=_text(payload.get("occupation"), "occupation", max_length=120),
        sms_consent=_bool(payload.get("sms_consent"), default=True),
        status=GuardianProfile.Status.ACTIVE,
    )
    return guardian_payload(profile, memberships=None)


def update_guardian(profile, payload, *, memberships=None):
    if "full_name" in payload:
        profile.full_name = _required_text(payload.get("full_name"), "full_name", max_length=160)
    if "phone" in payload:
        profile.phone = _phone(payload.get("phone"), "phone")
    if "whatsapp_phone" in payload:
        profile.whatsapp_phone = _optional_phone(
            payload.get("whatsapp_phone"),
            "whatsapp_phone",
        )
    if "email" in payload:
        profile.email = _email(payload.get("email"), "email")
    if "residential_zone" in payload:
        profile.residential_zone = _text(
            payload.get("residential_zone"),
            "residential_zone",
            max_length=120,
        )
    if "occupation" in payload:
        profile.occupation = _text(payload.get("occupation"), "occupation", max_length=120)
    if "sms_consent" in payload:
        profile.sms_consent = _bool(payload.get("sms_consent"), default=profile.sms_consent)
    profile.save()
    return guardian_payload(profile, memberships=memberships)


def deactivate_guardian(profile, *, memberships=None):
    profile.status = GuardianProfile.Status.INACTIVE
    profile.save(update_fields=["status", "updated_at"])
    if profile.user_id:
        user = profile.user
        if user.is_active:
            user.is_active = False
            user.save(update_fields=["is_active"])
    return guardian_payload(profile, memberships=memberships)


def reactivate_guardian(profile, *, memberships=None):
    profile.status = GuardianProfile.Status.ACTIVE
    profile.save(update_fields=["status", "updated_at"])
    if profile.user_id:
        user = profile.user
        if user.has_usable_password() and not user.is_active:
            user.is_active = True
            user.save(update_fields=["is_active"])
    return guardian_payload(profile, memberships=memberships)


def send_guardian_invite(*, actor, profile, memberships=None):
    if profile.status == GuardianProfile.Status.INACTIVE:
        raise EnvelopeError(
            400,
            "guardian_inactive",
            "Reactivate this guardian before inviting.",
        )
    if not profile.email:
        _invalid("email", "Email is required to send an invite.")
    result = issue_invite(
        actor=actor,
        door=DOOR_DEMO,
        school=None,
        email=profile.email,
        role=RoleMembership.Role.PARENT,
        display_name=profile.full_name,
        phone=profile.phone,
    )
    user = User.objects.filter(pk=result["user_id"]).first()
    if user is not None and profile.user_id != user.pk:
        profile.user = user
        profile.save(update_fields=["user", "updated_at"])
    return guardian_payload(profile, memberships=memberships)


def link_child(guardian, payload, *, memberships=None):
    if guardian.status == GuardianProfile.Status.INACTIVE:
        raise EnvelopeError(
            400,
            "guardian_inactive",
            "Cannot link a child to an inactive guardian.",
        )
    student = Student.objects.filter(pk=payload.get("student_id")).first()
    if student is None:
        _invalid("student_id", "Student was not found.")
    if guardian.child_links.filter(student=student).exists():
        _invalid("student_id", "This child is already linked.")
    relationship = _relationship(payload.get("relationship"))
    note = _relationship_note(relationship, payload.get("relationship_note"))
    is_primary = _bool(payload.get("is_primary"), default=False)
    is_financial = _bool(payload.get("is_financial_responsible"), default=False)
    with transaction.atomic():
        has_primary = StudentGuardianLink.objects.filter(
            student=student,
            is_primary=True,
        ).exists()
        if not has_primary:
            is_primary = True
        if is_primary:
            StudentGuardianLink.objects.filter(student=student, is_primary=True).update(
                is_primary=False
            )
        link = StudentGuardianLink.objects.create(
            student=student,
            guardian=guardian,
            relationship=relationship,
            relationship_note=note,
            is_primary=is_primary,
            is_financial_responsible=is_financial,
        )
    return link_payload(link)


def unlink_child(guardian, link_id):
    link = guardian.child_links.filter(pk=link_id).first()
    if link is None:
        return False
    link.delete()
    return True


def guardian_payload(profile, *, memberships=None):
    show_balance = memberships is None or bool(
        {row.role for row in memberships}.intersection(FINANCE_ROLES)
    )
    children = [
        link_payload(link, include_balance=show_balance)
        for link in profile.child_links.select_related(
            "student__campus",
            "student__current_classroom__grade_level__division",
        ).all()
    ]
    body = {
        "id": str(profile.pk),
        "full_name": profile.full_name,
        "phone": profile.phone,
        "whatsapp_phone": profile.whatsapp_phone,
        "email": profile.email,
        "residential_zone": profile.residential_zone,
        "occupation": profile.occupation,
        "sms_consent": profile.sms_consent,
        "status": profile.status,
        "status_label": STATUS_LABEL.get(profile.status, profile.status),
        "portal_status": portal_status(profile),
        "user_id": str(profile.user_id) if profile.user_id else None,
        "children": children,
        "linked_children_count": len(children),
        "updated_at": profile.updated_at.isoformat(),
    }
    if show_balance:
        body["family_balance_lrd"] = None
    return body


def link_payload(link, *, include_balance=True):
    student = link.student
    classroom = student.current_classroom
    grade = classroom.grade_level if classroom else None
    division = grade.division if grade else None
    body = {
        "id": str(link.pk),
        "student_id": str(student.pk),
        "student_code": student.student_code,
        "student_name": student.full_name,
        "campus_name": student.campus.name,
        "classroom_name": classroom.display_name if classroom else None,
        "division": division.name if division else None,
        "grade": grade.name if grade else None,
        "relationship": link.relationship,
        "relationship_label": RELATIONSHIP_LABEL.get(link.relationship, link.relationship),
        "relationship_note": link.relationship_note,
        "is_primary": link.is_primary,
        "is_financial_responsible": link.is_financial_responsible,
    }
    if include_balance:
        body["lrd_balance_lrd"] = None
    return body


def portal_status(profile):
    if profile.status == GuardianProfile.Status.INACTIVE:
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


def _relationship(raw):
    value = str(raw or "").strip().lower()
    if value not in RELATIONSHIPS:
        _invalid("relationship", "Choose a valid relationship.")
    return value


def _relationship_note(relationship, raw):
    if relationship != StudentGuardianLink.Relationship.OTHER:
        return ""
    return _text(raw, "relationship_note", max_length=120)


def _optional_phone(raw, field):
    if raw in (None, ""):
        return ""
    return _phone(raw, field)


def _phone(raw, field):
    try:
        return normalize_liberian_phone(str(raw or ""))
    except InvalidLiberianPhone as exc:
        raise ValidationError({field: [str(exc)]}) from exc


def _email(raw, field):
    value = _required_text(raw, field, max_length=254).lower()
    if "@" not in value:
        _invalid(field, "Enter a valid email.")
    return value


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
