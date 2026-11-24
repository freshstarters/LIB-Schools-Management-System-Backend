"""Celery application. Broker connectivity is not checked at import."""

import os

from celery import Celery

os.environ.setdefault(
    "DJANGO_SETTINGS_MODULE",
    "lib_school_management_system.settings",
)


class PlatformCelery(Celery):
    def send_task(self, *args, **kwargs):
        # Celery 5.7 opens the broker connection before before_task_publish.
        # Reject a missing URL here so publish fails closed before any socket.
        from core.integrations.broker import guard_broker_url

        guard_broker_url()
        return super().send_task(*args, **kwargs)


app = PlatformCelery("lib_school_management_system")
app.config_from_object("django.conf:settings", namespace="CELERY")
app.autodiscover_tasks()
