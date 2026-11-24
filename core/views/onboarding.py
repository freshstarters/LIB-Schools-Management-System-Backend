"""Invite, revoke, accept, and password reset for one auth door."""

from rest_framework.exceptions import ValidationError
from rest_framework.response import Response
from rest_framework.throttling import ScopedRateThrottle

from core.helpers.errors import EnvelopeError
from core.helpers.idempotency import run_idempotent
from core.helpers.permissions import PLATFORM, ROLE_ORDER
from core.models import TenantSchool
from core.service.auth import DOOR_DEMO, DOOR_PLATFORM, user_from_bearer
from core.service.onboarding import (
    accept_invite,
    confirm_password_reset,
    issue_invite,
    replay_accept,
    request_password_reset,
    revoke_invite,
    user_for_invite_token,
    user_for_reset_login,
    user_for_reset_token,
)
from core.views.auth import _DoorView, _session_response, enforce_csrf


class _RecoveryView(_DoorView):
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "auth_recovery"


class InviteCreateView(_DoorView):
    def post(self, request):
        enforce_csrf(request)
        return run_idempotent(self, request, lambda: self._create(request))

    def idempotency_user(self, request):
        return _actor(self, request)

    def _create(self, request):
        school = _target_school(self, request)
        actor = _actor(self, request)
        role = _role(request.data.get("role"))
        result = issue_invite(
            actor=actor,
            door=self.door,
            school=school,
            email=request.data.get("email"),
            role=role,
            display_name=request.data.get("display_name"),
            phone=request.data.get("phone"),
        )
        status = 201 if result["invite_id"] else 200
        return Response(result, status=status)


class InviteRevokeView(_DoorView):
    def post(self, request, invite_id):
        enforce_csrf(request)
        self._invite_id = invite_id
        return run_idempotent(self, request, lambda: self._revoke(request))

    def idempotency_user(self, request):
        return _actor(self, request)

    def _revoke(self, request):
        school = self._school(request)
        if self.door == DOOR_PLATFORM:
            school = None
        revoke_invite(
            actor=_actor(self, request),
            door=self.door,
            school=school,
            invite_id=self._invite_id,
        )
        return Response(status=204)


class InviteAcceptView(_RecoveryView):
    def post(self, request):
        enforce_csrf(request)
        self._accept_school = self._school(request)
        return run_idempotent(
            self,
            request,
            lambda: self._accept(request),
            stored_body={"accepted": True},
            on_replay=lambda: self._replay(request),
        )

    def idempotency_user(self, request):
        return user_for_invite_token(
            request.data.get("token"),
            self.door,
            self._accept_school,
        )

    def _accept(self, request):
        user, (access, raw) = accept_invite(
            door=self.door,
            school=self._accept_school,
            token=request.data.get("token"),
            password=request.data.get("password"),
            id_token=request.data.get("id_token"),
        )
        return _session_response(user, access, raw)

    def _replay(self, request):
        user, (access, raw) = replay_accept(
            door=self.door,
            school=self._accept_school,
            token=request.data.get("token"),
            password=request.data.get("password"),
            id_token=request.data.get("id_token"),
        )
        return _session_response(user, access, raw)


class PasswordResetRequestView(_RecoveryView):
    def post(self, request):
        enforce_csrf(request)
        self._reset_school = self._school(request)
        return run_idempotent(self, request, lambda: self._request(request))

    def idempotency_user(self, request):
        return user_for_reset_login(
            request.data.get("login"),
            self.door,
            self._reset_school,
        )

    def _request(self, request):
        body = request_password_reset(
            door=self.door,
            school=self._reset_school,
            login=request.data.get("login"),
        )
        return Response(body)


class PasswordResetConfirmView(_RecoveryView):
    def post(self, request):
        enforce_csrf(request)
        self._reset_school = self._school(request)
        return run_idempotent(self, request, lambda: self._confirm(request))

    def idempotency_user(self, request):
        return user_for_reset_token(
            request.data.get("token"),
            self.door,
            self._reset_school,
        )

    def _confirm(self, request):
        body = confirm_password_reset(
            door=self.door,
            school=self._reset_school,
            token=request.data.get("token"),
            password=request.data.get("password"),
        )
        return Response(body)


def _actor(view, request):
    if view.door == DOOR_DEMO:
        return user_from_bearer(request, "demo")
    user = user_from_bearer(request, "core")
    if view.door == DOOR_PLATFORM:
        if user.school_id is not None or not user.memberships.filter(role=PLATFORM).exists():
            raise EnvelopeError(403, "permission_denied", "You do not have permission.")
        return user
    school = view._school(request)
    if user.school_id != school.pk:
        raise EnvelopeError(403, "permission_denied", "You do not have permission.")
    return user


def _target_school(view, request):
    if view.door == DOOR_DEMO:
        if request.data.get("school_slug"):
            raise ValidationError(
                {"school_slug": ["School is chosen by the sign-in door."]}
            )
        return None
    if view.door == DOOR_PLATFORM:
        role = request.data.get("role")
        slug = request.data.get("school_slug")
        if role == PLATFORM:
            if slug:
                raise ValidationError(
                    {"school_slug": ["A platform invite does not take a school."]}
                )
            return None
        return _school_from_slug(slug)
    if request.data.get("school_slug"):
        raise ValidationError({"school_slug": ["School is chosen by the sign-in door."]})
    return view._school(request)


def _school_from_slug(slug):
    if not isinstance(slug, str) or not slug.strip():
        raise ValidationError({"school_slug": ["Choose a school for this role."]})
    try:
        school = TenantSchool.objects.get(slug=slug.strip().lower())
    except TenantSchool.DoesNotExist:
        raise EnvelopeError(404, "tenant_not_found", "Tenant not found.") from None
    if school.status != TenantSchool.Status.ACTIVE:
        raise EnvelopeError(403, "tenant_unavailable", "Tenant is not available.")
    return school


def _role(value):
    if value not in ROLE_ORDER:
        raise ValidationError({"role": ["Choose a valid role."]})
    return value
