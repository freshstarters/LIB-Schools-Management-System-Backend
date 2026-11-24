import json

from django.http import JsonResponse
from django.test import RequestFactory, SimpleTestCase
from rest_framework.exceptions import NotFound, ValidationError

from core.helpers.errors import ApiNotFoundMiddleware, envelope_exception_handler


class ErrorEnvelopeTests(SimpleTestCase):
    def test_field_validation_error(self):
        response = envelope_exception_handler(
            ValidationError({"email": ["This field is required."]}),
            {},
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(
            response.data,
            {
                "code": "validation_error",
                "detail": "Invalid input.",
                "fields": {"email": ["This field is required."]},
            },
        )

    def test_non_field_validation_error(self):
        response = envelope_exception_handler(
            ValidationError("Passwords do not match."),
            {},
        )
        self.assertEqual(response.data["code"], "validation_error")
        self.assertEqual(
            response.data["fields"]["non_field_errors"],
            ["Passwords do not match."],
        )

    def test_not_found(self):
        response = envelope_exception_handler(NotFound(), {})
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.data["code"], "not_found")
        self.assertEqual(response.data["detail"], "Not found.")
        self.assertEqual(response.data["fields"], {})

    def test_unknown_api_path_uses_the_envelope(self):
        response = self.client.get("/api/demo/v1/does-not-exist/")
        self.assertEqual(response.status_code, 404)
        self.assertEqual(
            response.json(),
            {"code": "not_found", "detail": "Not found.", "fields": {}},
        )

    def test_json_not_found_is_unchanged(self):
        def get_response(request):
            return JsonResponse(
                {"code": "tenant_not_found", "detail": "Tenant not found.", "fields": {}},
                status=404,
            )

        request = RequestFactory().get("/api/v1/divisions/")
        response = ApiNotFoundMiddleware(get_response)(request)
        self.assertEqual(json.loads(response.content)["code"], "tenant_not_found")
