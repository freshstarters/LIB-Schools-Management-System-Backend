"""DRF errors as ``{code, detail, fields}``."""

from django.http import JsonResponse
from rest_framework.exceptions import (
    APIException,
    MethodNotAllowed,
    NotAuthenticated,
    NotFound,
    ParseError,
    PermissionDenied,
    Throttled,
    ValidationError,
)
from rest_framework.views import exception_handler as drf_exception_handler


class EnvelopeError(APIException):
    """An API error whose code and detail stay intact in the envelope."""

    status_code = 400

    def __init__(self, status_code, code, detail, server_record=None):
        self.status_code = status_code
        self.envelope_code = code
        self.server_record = server_record
        super().__init__(detail=detail, code=code)

_CODE_BY_TYPE = (
    (ValidationError, "validation_error"),
    (ParseError, "parse_error"),
    (NotFound, "not_found"),
    (PermissionDenied, "permission_denied"),
    (NotAuthenticated, "authentication_failed"),
    (MethodNotAllowed, "method_not_allowed"),
    (Throttled, "throttled"),
)

_DETAIL_BY_CODE = {
    "validation_error": "Invalid input.",
    "parse_error": "Malformed request.",
    "not_found": "Not found.",
    "permission_denied": "You do not have permission to perform this action.",
    "authentication_failed": "Authentication credentials were not provided.",
    "method_not_allowed": "Method not allowed.",
    "throttled": "Request was throttled.",
    "error": "Request failed.",
}


class ApiNotFoundMiddleware:
    """Replace Django's HTML 404 with the API envelope on /api/ paths."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)
        content_type = response.get("Content-Type", "")
        if (
            response.status_code == 404
            and request.path.startswith("/api/")
            and content_type.startswith("text/html")
        ):
            return JsonResponse(
                {"code": "not_found", "detail": "Not found.", "fields": {}},
                status=404,
            )
        return response


def csrf_failure(request, reason=""):
    return JsonResponse(
        {
            "code": "csrf_failed",
            "detail": "CSRF verification failed.",
            "fields": {},
        },
        status=403,
    )


def envelope_exception_handler(exc, context):
    response = drf_exception_handler(exc, context)
    if response is None:
        return None

    if isinstance(exc, EnvelopeError):
        response.data = {
            "code": exc.envelope_code,
            "detail": str(exc.detail),
            "fields": {},
        }
        if exc.server_record is not None:
            response.data["server_record"] = exc.server_record
        return response

    code = _code_for(exc)
    if code == "validation_error":
        fields = _validation_fields(response.data)
    else:
        fields = {}

    response.data = {
        "code": code,
        "detail": _DETAIL_BY_CODE.get(code, _DETAIL_BY_CODE["error"]),
        "fields": fields,
    }
    return response


def _code_for(exc):
    for exc_type, code in _CODE_BY_TYPE:
        if isinstance(exc, exc_type):
            return code
    return "error"


def _validation_fields(data):
    if isinstance(data, list):
        return {"non_field_errors": _messages(data)}

    if not isinstance(data, dict):
        return {"non_field_errors": _messages([data])}

    fields = {}
    for key, value in data.items():
        fields[str(key)] = _messages(value if isinstance(value, list) else [value])
    return fields


def _messages(values):
    return [str(value) for value in values]
