"""Auth endpoints shared by Gojo, a core school, and the platform."""

import logging

from django.conf import settings
from django.http import HttpResponse
from django.middleware.csrf import CsrfViewMiddleware, RejectRequest, get_token
from rest_framework.exceptions import ValidationError
from rest_framework.response import Response
from rest_framework.throttling import ScopedRateThrottle
from rest_framework.views import APIView

from core.helpers.errors import EnvelopeError
from core.integrations.google_oauth import (
    GoogleSignInUnavailable,
    google_credentials_present,
)
from core.service.auth import (
    DOOR_DEMO,
    REFRESH_SECONDS,
    TENANT_MISMATCH,
    access_payload,
    authenticate_google,
    authenticate_password,
    cookie_for_door,
    cookie_name_for,
    cookie_path_for,
    public_config,
    public_user,
    resolve_door,
    revoke_browser_session,
    revoke_presented,
    rotate_session,
    sessions_from_cookies,
    start_session,
    user_from_bearer,
)
from core.tenancy.middleware import slug_from_host

logger = logging.getLogger(__name__)


class _DoorView(APIView):
    authentication_classes = []
    permission_classes = []
    door = DOOR_DEMO

    def _school(self, request):
        return resolve_door(self.door, request)


class LoginView(_DoorView):
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "auth_login"

    def post(self, request):
        enforce_csrf(request)
        school = self._school(request)
        login, password = _login_body(request.data)
        user = authenticate_password(self.door, login, password, school)
        return _session_response(user, *start_session(user))


class GoogleView(_DoorView):
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "auth_login"

    def initial(self, request, *args, **kwargs):
        if not google_credentials_present():
            raise EnvelopeError(
                503,
                "service_unavailable",
                "Google sign-in is not configured.",
            )
        super().initial(request, *args, **kwargs)

    def post(self, request):
        enforce_csrf(request)
        school = self._school(request)
        token = request.data.get("id_token", "")
        if not isinstance(token, str) or not token.strip():
            raise ValidationError({"id_token": ["Enter a Google ID token."]})
        try:
            user = authenticate_google(self.door, token, school)
        except GoogleSignInUnavailable:
            raise EnvelopeError(
                503,
                "service_unavailable",
                "Google sign-in is not configured.",
            ) from None
        return _session_response(user, *start_session(user))


class RefreshView(_DoorView):
    def post(self, request):
        enforce_csrf(request)
        school = self._school(request)
        name, _path = cookie_for_door(self.door, school)
        access, raw, user = rotate_session(
            self.door,
            request.COOKIES.get(name, ""),
            school,
        )
        return _session_response(user, access, raw)


class LogoutView(_DoorView):
    def post(self, request):
        enforce_csrf(request)
        school = self._school(request)
        name, path = cookie_for_door(self.door, school)
        revoke_presented(request.COOKIES.get(name, ""))
        response = Response(status=204)
        _clear_refresh_cookie(response, name, path)
        return response


class MeView(_DoorView):
    def get(self, request):
        expected = "demo" if self.door == DOOR_DEMO else "core"
        user = user_from_bearer(request, expected)
        if expected == "core":
            header = request.headers.get("X-Tenant-Slug", "").strip().lower()
            if header and header != (user.school.slug if user.school_id else ""):
                raise EnvelopeError(403, "tenant_mismatch", TENANT_MISMATCH)
            _reject_subdomain_mismatch(request, user)
        return Response({"user": public_user(user)})


class ConfigView(APIView):
    authentication_classes = []
    permission_classes = []

    def get(self, request):
        django_request = getattr(request, "_request", request)
        return Response(public_config(get_token(django_request)))


class SessionsView(_DoorView):
    def get(self, request):
        door = DOOR_DEMO if self.door == DOOR_DEMO else "core"
        return Response({"sessions": sessions_from_cookies(request.COOKIES, door)})


class SessionDetailView(_DoorView):
    def delete(self, request, session_id):
        enforce_csrf(request)
        door = DOOR_DEMO if self.door == DOOR_DEMO else "core"
        name, path = revoke_browser_session(request.COOKIES, door, session_id)
        response = Response(status=204)
        _clear_refresh_cookie(response, name, path)
        return response


def _reject_subdomain_mismatch(request, user):
    if settings.TENANCY_ROUTING_MODE != "subdomain":
        return
    host_slug = slug_from_host(request.get_host(), settings.TENANCY_BASE_DOMAIN)
    if user.school_id:
        matches = host_slug == user.school.slug
    else:
        matches = host_slug == "platform"
    if matches:
        return
    logger.warning("tenant jwt mismatch path=%s slug=%s", request.path, host_slug or "")
    raise EnvelopeError(403, "tenant_mismatch", TENANT_MISMATCH)


def enforce_csrf(request):
    django_request = getattr(request, "_request", request)
    if getattr(django_request, "_dont_enforce_csrf_checks", False):
        return
    middleware = CsrfViewMiddleware(lambda req: HttpResponse())
    try:
        middleware._check_token(django_request)
    except RejectRequest:
        raise EnvelopeError(403, "csrf_failed", "CSRF verification failed.") from None


def _login_body(data):
    login = data.get("login", "")
    password = data.get("password", "")
    errors = {}
    if not isinstance(login, str) or not login.strip():
        errors["login"] = ["Enter an email or phone number."]
    if not isinstance(password, str) or password == "":
        errors["password"] = ["Enter a password."]
    if errors:
        raise ValidationError(errors)
    return login.strip(), password


def _session_response(user, access, raw):
    response = Response(access_payload(user, access))
    _set_refresh_cookie(response, cookie_name_for(user), raw, cookie_path_for(user))
    return response


def _set_refresh_cookie(response, name, value, path):
    response.set_cookie(
        name,
        value,
        max_age=REFRESH_SECONDS,
        path=path,
        secure=not settings.DEBUG,
        httponly=True,
        samesite="Lax",
    )


def _clear_refresh_cookie(response, name, path):
    response.delete_cookie(name, path=path, samesite="Lax")
