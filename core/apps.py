from django.apps import AppConfig


class CoreConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "core"

    def ready(self):
        from core.integrations.broker import connect_broker_guard
        from core.tenancy.provisioning import connect_provisioning

        connect_broker_guard()
        connect_provisioning()
