"""Create Gojo Institute's staff and campus population for local development."""

import os
import uuid

from django.conf import settings
from django.contrib.auth.hashers import make_password
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from core.models import RoleMembership, User
from core.models.platform import account_username
from demo.models import Campus
from demo.service.population import population_plan
from demo.service.school_service import ensure_school_profile

ACCOUNTS = (
    ("samuel.kollie@gojo.test", "Dr. Samuel Kollie", RoleMembership.Role.PRINCIPAL, None),
    ("beatrice.toe@gojo.test", "Beatrice Toe", RoleMembership.Role.REGISTRAR, None),
    ("joseph.freeman@gojo.test", "Joseph Freeman", RoleMembership.Role.BURSAR, None),
    ("grace.johnson@gojo.test", "Grace Johnson", RoleMembership.Role.TEACHER, "gardnersville"),
)
BATCH = 500


class Command(BaseCommand):
    help = "Create Gojo Institute's staff and the campus student and teacher logins."

    def handle(self, *args, **options):
        if not settings.DEBUG:
            raise CommandError("seed_gojo_demo runs only when DEBUG is true.")
        password = os.environ.get("SEED_GOJO_PASSWORD", "").strip()
        if not password:
            raise CommandError("SEED_GOJO_PASSWORD is required.")
        ensure_school_profile()
        campuses = {campus.code: campus for campus in Campus.objects.all()}
        for email, name, role, campus_code in ACCOUNTS:
            user = User.objects.filter(email=email, app_scope=User.AppScope.DEMO).first()
            if user is None:
                user = User.objects.create_user(
                    email,
                    password,
                    app_scope=User.AppScope.DEMO,
                    display_name=name,
                )
            campus_id = None if campus_code is None else campuses[campus_code].pk
            membership, _created = RoleMembership.objects.get_or_create(user=user, role=role)
            if membership.campus_id != campus_id:
                membership.campus_id = campus_id
                membership.save(update_fields=["campus_id"])
            self.stdout.write(f"{email} {role}")
        created = _create_population(password, campuses)
        self.stdout.write(f"population {created}")


def _create_population(password, campuses):
    plan = population_plan()
    emails = [row["email"] for row in plan]
    existing = set(
        User.objects.filter(app_scope=User.AppScope.DEMO, email__in=emails).values_list(
            "email",
            flat=True,
        )
    )
    missing = [row for row in plan if row["email"] not in existing]
    if not missing:
        return 0
    password_hash = make_password(password)
    created = 0
    with transaction.atomic():
        for start in range(0, len(missing), BATCH):
            chunk = missing[start : start + BATCH]
            users = [
                User(
                    id=uuid.uuid4(),
                    email=row["email"],
                    username=account_username(row["email"], User.AppScope.DEMO, None),
                    display_name=row["display_name"],
                    app_scope=User.AppScope.DEMO,
                    password=password_hash,
                    is_active=True,
                )
                for row in chunk
            ]
            User.objects.bulk_create(users)
            RoleMembership.objects.bulk_create(
                [
                    RoleMembership(
                        id=uuid.uuid4(),
                        user=user,
                        role=row["role"],
                        campus_id=campuses[row["campus"]].pk,
                    )
                    for user, row in zip(users, chunk)
                ]
            )
            created += len(users)
    return created
