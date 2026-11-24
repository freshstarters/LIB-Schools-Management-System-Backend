"""Process liveness and dependency readiness."""

from django.db import connection
from django.db.utils import Error as DatabaseError
from django.http import JsonResponse

from core.integrations.broker import resolve_broker_url

_OK = "ok"
_UNCONFIGURED = "unconfigured"
_UNAVAILABLE = "unavailable"


def health(request):
    return JsonResponse({"status": _OK})


def ready(request):
    checks = {
        "postgresql": postgres_status(),
        "redis": redis_status(resolve_broker_url()),
    }
    passed = all(value == _OK for value in checks.values())
    return JsonResponse(
        {"status": _OK if passed else "degraded", "checks": checks},
        status=200 if passed else 503,
    )


def postgres_status():
    engine = connection.settings_dict.get("ENGINE", "")
    if "postgresql" not in engine:
        return _UNCONFIGURED
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
            cursor.fetchone()
    except DatabaseError:
        return _UNAVAILABLE
    return _OK


def redis_status(url):
    if not url:
        return _UNCONFIGURED
    try:
        import redis

        redis.Redis.from_url(
            url,
            socket_connect_timeout=1,
            socket_timeout=1,
        ).ping()
    except Exception:
        return _UNAVAILABLE
    return _OK
