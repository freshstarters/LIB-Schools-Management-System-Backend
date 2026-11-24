from unittest.mock import patch

from django.core.exceptions import ImproperlyConfigured
from django.test import SimpleTestCase, override_settings

from core.integrations.broker import guard_broker_url


class BrokerGuardTests(SimpleTestCase):
    @override_settings(CELERY_BROKER_URL="", REDIS_URL="")
    def test_publish_without_broker_url_fails_closed(self):
        from lib_school_management_system.celery import app

        with self.assertRaises(ImproperlyConfigured):
            app.send_task("core.tasks.unused")

    @override_settings(CELERY_BROKER_URL="redis://127.0.0.1:6379/15")
    def test_guard_does_not_ping_redis(self):
        with patch("redis.Redis.from_url") as ping:
            guard_broker_url()
        ping.assert_not_called()
