"""Celery broker URL resolution.

Publishing a task with no broker URL fails closed. A configured URL that
Redis refuses is left to Celery's own connection error. This module does
not ping Redis.
"""

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured

_connected = False


def resolve_broker_url():
    broker = getattr(settings, "CELERY_BROKER_URL", "") or ""
    redis_url = getattr(settings, "REDIS_URL", "") or ""
    for candidate in (broker, redis_url):
        cleaned = str(candidate).strip()
        if cleaned:
            return cleaned
    return ""


def guard_broker_url(**kwargs):
    if resolve_broker_url():
        return
    raise ImproperlyConfigured(
        "Celery broker is unavailable: set CELERY_BROKER_URL or REDIS_URL."
    )


def connect_broker_guard():
    global _connected
    if _connected:
        return
    from celery.signals import before_task_publish

    before_task_publish.connect(guard_broker_url, weak=False)
    _connected = True
