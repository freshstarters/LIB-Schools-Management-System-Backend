"""Create StaffProfile rows from existing demo staff role users."""

from django.core.management.base import BaseCommand
from django.db import transaction

from core.models import RoleMembership, User
from demo.models import Classroom, StaffProfile
from demo.service.school_service import ensure_school_profile
from demo.service.staff_service import allocate_staff_code

STAFF_ROLES = frozenset(
    {
        RoleMembership.Role.PRINCIPAL,
        RoleMembership.Role.REGISTRAR,
        RoleMembership.Role.BURSAR,
        RoleMembership.Role.TEACHER,
        RoleMembership.Role.SUPPORT_STAFF,
    }
)
ROLE_PRIORITY = (
    RoleMembership.Role.PRINCIPAL,
    RoleMembership.Role.REGISTRAR,
    RoleMembership.Role.BURSAR,
    RoleMembership.Role.TEACHER,
    RoleMembership.Role.SUPPORT_STAFF,
)
SHARED_PHONE = "+231770000000"
JOB_TITLES = {
    RoleMembership.Role.PRINCIPAL: "Principal",
    RoleMembership.Role.REGISTRAR: "Registrar",
    RoleMembership.Role.BURSAR: "Bursar",
    RoleMembership.Role.TEACHER: "Teacher",
    RoleMembership.Role.SUPPORT_STAFF: "Support Staff",
}


class Command(BaseCommand):
    help = "Idempotently backfill demo StaffProfile rows from staff role users."

    def handle(self, *args, **options):
        ensure_school_profile()
        users = (
            User.objects.filter(
                app_scope=User.AppScope.DEMO,
                memberships__role__in=STAFF_ROLES,
            )
            .prefetch_related("memberships")
            .distinct()
            .order_by("email")
        )
        created = 0
        skipped = 0
        repaired = 0
        for user in users:
            role = _staff_role(user)
            if role is None:
                skipped += 1
                continue
            phone = _staff_phone(user)
            existing = StaffProfile.objects.filter(user=user).first()
            if existing is not None:
                if existing.phone == SHARED_PHONE and existing.phone != phone:
                    existing.phone = phone
                    existing.save(update_fields=["phone", "updated_at"])
                    repaired += 1
                else:
                    skipped += 1
                continue
            campus_id = _campus_id(user, role)
            division = _division_for(user)
            with transaction.atomic():
                StaffProfile.objects.create(
                    user=user,
                    staff_code=allocate_staff_code(),
                    full_name=user.display_name or user.email.split("@")[0],
                    role=role,
                    job_title=JOB_TITLES.get(role, "Staff"),
                    division=division,
                    campus_id=campus_id,
                    phone=phone,
                    email=user.email,
                    employment_status=(
                        StaffProfile.EmploymentStatus.ACTIVE
                        if user.is_active
                        else StaffProfile.EmploymentStatus.INACTIVE
                    ),
                )
            created += 1
        self.stdout.write(
            self.style.SUCCESS(
                f"staff_created={created} staff_skipped={skipped} staff_repaired={repaired}"
            )
        )


def _staff_phone(user):
    phone = (user.phone or "").strip()
    if phone == SHARED_PHONE:
        return ""
    return phone


def _staff_role(user):
    held = {row.role for row in user.memberships.all() if row.role in STAFF_ROLES}
    for role in ROLE_PRIORITY:
        if role in held:
            return role
    return None


def _campus_id(user, role):
    for row in user.memberships.all():
        if row.role == role and row.campus_id:
            return row.campus_id
    return None


def _division_for(user):
    classroom = (
        Classroom.objects.select_related("grade_level__division")
        .filter(homeroom_teacher_id=user.pk)
        .order_by("grade_level__sort_order", "section_name")
        .first()
    )
    if classroom is None:
        return None
    return classroom.grade_level.division
