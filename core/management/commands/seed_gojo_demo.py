"""Create the first Gojo Institute staff accounts for local development."""

import os

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from core.models import RoleMembership, User

ACCOUNTS = (
    ("samuel.kollie@gojo.test", "Dr. Samuel Kollie", RoleMembership.Role.PRINCIPAL),
    ("beatrice.toe@gojo.test", "Beatrice Toe", RoleMembership.Role.REGISTRAR),
    ("joseph.freeman@gojo.test", "Joseph Freeman", RoleMembership.Role.BURSAR),
    ("grace.johnson@gojo.test", "Grace Johnson", RoleMembership.Role.TEACHER),
)


class Command(BaseCommand):
    help = "Create Gojo Institute's principal, registrar, bursar, and teacher for local use."

    def handle(self, *args, **options):
        if not settings.DEBUG:
            raise CommandError("seed_gojo_demo runs only when DEBUG is true.")
        password = os.environ.get("SEED_GOJO_PASSWORD", "").strip()
        if not password:
            raise CommandError("SEED_GOJO_PASSWORD is required.")
        for email, name, role in ACCOUNTS:
            user = User.objects.filter(email=email, app_scope=User.AppScope.DEMO).first()
            if user is None:
                user = User.objects.create_user(
                    email,
                    password,
                    app_scope=User.AppScope.DEMO,
                    display_name=name,
                )
            RoleMembership.objects.get_or_create(user=user, role=role)
            self.stdout.write(f"{email} {role}")
