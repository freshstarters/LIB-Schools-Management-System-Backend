"""Object storage fail-closed rules for PR-05."""

from django.core.exceptions import ImproperlyConfigured
from django.test import SimpleTestCase, override_settings

from core.integrations.storage import assert_storage_configured


class StorageConfigTests(SimpleTestCase):
    @override_settings(DEBUG=False, STORAGE_BACKEND="local")
    def test_local_without_debug_fails_closed(self):
        with self.assertRaises(ImproperlyConfigured):
            assert_storage_configured()

    @override_settings(DEBUG=True, STORAGE_BACKEND="local")
    def test_local_allowed_when_debug(self):
        self.assertEqual(assert_storage_configured(), "local")

    @override_settings(
        DEBUG=False,
        STORAGE_BACKEND="s3",
        AWS_ACCESS_KEY_ID="",
        AWS_SECRET_ACCESS_KEY="secret",
        AWS_STORAGE_BUCKET_NAME="bucket",
        AWS_S3_ENDPOINT_URL="https://example.r2.cloudflarestorage.com",
    )
    def test_s3_missing_access_key_fails_closed(self):
        with self.assertRaises(ImproperlyConfigured) as raised:
            assert_storage_configured()
        self.assertIn("AWS_ACCESS_KEY_ID", str(raised.exception))

    @override_settings(
        DEBUG=False,
        STORAGE_BACKEND="s3",
        AWS_ACCESS_KEY_ID="key",
        AWS_SECRET_ACCESS_KEY="secret",
        AWS_STORAGE_BUCKET_NAME="bucket",
        AWS_S3_ENDPOINT_URL="https://example.r2.cloudflarestorage.com",
    )
    def test_s3_configured(self):
        self.assertEqual(assert_storage_configured(), "s3")

    @override_settings(DEBUG=True, STORAGE_BACKEND="")
    def test_blank_backend_fails_closed(self):
        with self.assertRaises(ImproperlyConfigured):
            assert_storage_configured()
