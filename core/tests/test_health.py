from unittest.mock import patch

from django.test import TestCase, override_settings


class HealthTests(TestCase):
    def test_health_is_liveness_only(self):
        response = self.client.get("/api/health/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"status": "ok"})

    def test_ready_reports_postgresql_and_redis(self):
        response = self.client.get("/api/ready/")
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["status"], "ok")
        self.assertEqual(body["checks"]["postgresql"], "ok")
        self.assertEqual(body["checks"]["redis"], "ok")

    def test_ready_is_degraded_when_redis_ping_fails(self):
        class Refused:
            def ping(self):
                raise ConnectionError("redis refused")

        with patch("redis.Redis.from_url", return_value=Refused()):
            response = self.client.get("/api/ready/")

        self.assertEqual(response.status_code, 503)
        body = response.json()
        self.assertEqual(body["status"], "degraded")
        self.assertEqual(body["checks"]["postgresql"], "ok")
        self.assertEqual(body["checks"]["redis"], "unavailable")

    @override_settings(CELERY_BROKER_URL="", REDIS_URL="")
    def test_ready_reports_redis_unconfigured(self):
        response = self.client.get("/api/ready/")
        self.assertEqual(response.status_code, 503)
        body = response.json()
        self.assertEqual(body["status"], "degraded")
        self.assertEqual(body["checks"]["redis"], "unconfigured")
