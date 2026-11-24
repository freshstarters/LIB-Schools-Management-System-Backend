"""Apply pending migrations to every active tenant database."""

from django.conf import settings
from django.core.management import call_command
from django.core.management.base import BaseCommand, CommandError

from core.models import TenantMigrationRecord, TenantSchool
from core.tenancy.provisioning import migration_fingerprint, register_tenant_database


class Command(BaseCommand):
    help = "Migrate every active tenant database. Shared mode refuses to run."

    def handle(self, *args, **options):
        if settings.TENANCY_DB_MODE != "per_tenant":
            raise CommandError(
                "migrate_all_tenants requires TENANCY_DB_MODE=per_tenant."
            )
        failures = 0
        tenants = TenantSchool.objects.filter(status=TenantSchool.Status.ACTIVE)
        for tenant in tenants:
            try:
                alias = register_tenant_database(tenant)
                call_command("migrate", database=alias, interactive=False, verbosity=0)
                digest = migration_fingerprint(alias)
            except Exception as exc:
                failures += 1
                TenantMigrationRecord.objects.create(
                    tenant=tenant,
                    schema_hash="",
                    status=TenantMigrationRecord.Status.FAILED,
                    error=type(exc).__name__,
                )
                self.stderr.write(f"{tenant.slug} failed")
                continue
            TenantMigrationRecord.objects.create(
                tenant=tenant,
                schema_hash=digest,
                status=TenantMigrationRecord.Status.SUCCEEDED,
                error="",
            )
            tenant.schema_version = digest
            tenant.save(update_fields=["schema_version"])
            self.stdout.write(f"{tenant.slug} migrated")
        if failures:
            raise CommandError(f"{failures} tenant migration(s) failed.")
