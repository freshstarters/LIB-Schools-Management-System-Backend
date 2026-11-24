"""Route core tenant models to the active tenant database in per-tenant mode."""

from django.conf import settings

from core.tenancy.context import TenantContextError, get_current_tenant


class TenantDatabaseRouter:
    """Shared mode uses default. Per-tenant mode uses the active school's alias."""

    def db_for_read(self, model, **hints):
        return self._route(model)

    def db_for_write(self, model, **hints):
        return self._route(model)

    def allow_relation(self, obj1, obj2, **hints):
        # Read the raw attribute. Django 6 defers fields, and a lookup during
        # model construction would query a row that does not exist yet.
        school_one = obj1.__dict__.get("school_id")
        school_two = obj2.__dict__.get("school_id")
        if school_one and school_two:
            return school_one == school_two
        return True

    def allow_migrate(self, db, app_label, model_name=None, **hints):
        return True

    def _route(self, model):
        if not _is_tenant_model(model):
            return "default"
        if settings.TENANCY_DB_MODE != "per_tenant":
            return "default"
        tenant = get_current_tenant()
        if tenant is None:
            raise TenantContextError("Query outside tenant context.")
        from core.tenancy.provisioning import register_tenant_database

        return register_tenant_database(tenant)


def _is_tenant_model(model):
    from core.models.platform import TenantScopedModel

    return isinstance(model, type) and issubclass(model, TenantScopedModel)
