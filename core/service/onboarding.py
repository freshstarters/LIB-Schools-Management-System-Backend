"""Invite activation and password reset. Raw tokens are emailed, never stored."""

import hashlib
import secrets
from datetime import timedelta

from urllib.parse import quote

from django.conf import settings
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError as DjangoValidationError
from django.core.validators import validate_email
from django.db import transaction
from django.utils import timezone
from rest_framework.exceptions import ValidationError

from core.helpers.errors import EnvelopeError
from core.helpers.permissions import (
    PLATFORM,
    SCHOOL_ROLES,
    actor_can_grant,
)
from core.helpers.phone import InvalidLiberianPhone, normalize_liberian_phone
from core.integrations.google_oauth import (
    GoogleSignInUnavailable,
    GoogleTokenInvalid,
    verify_id_token,
)
from core.integrations.transactional_email import deliver
from core.models import InviteToken, PasswordResetToken, RoleMembership, User
from core.service.auth import (
    DOOR_DEMO,
    DOOR_PLATFORM,
    DOOR_SCHOOL,
    _door_matches,
    revoke_all_sessions,
    start_session,
)

INVITE_HOURS = 72
RESET_HOURS = 1
INVITE_INVALID = "This invite is invalid."
INVITE_USED = "This invite was already used. Sign in."
RESET_INVALID = "This reset link is invalid."
RESET_RESPONSE = "If the account can be reset, a message is on its way."


def issue_invite(*, actor, door, school, email, role, display_name, phone):
    """Create an invite, or add a role when the account is already active."""
    if not actor_can_grant(actor, role):
        raise EnvelopeError(403, "permission_denied", "You cannot grant that role.")
    _role_fits_door(role, door, school)
    address = _email(email)
    name = _display_name(display_name)
    number = _phone(phone)
    now = timezone.now()
    with transaction.atomic():
        user = _bucket(door, school, role).filter(email=address).select_for_update().first()
        if user is not None and _live_invite(user, now):
            raise EnvelopeError(400, "invite_pending", "An invite is already pending.")
        if user is not None and user.is_active:
            if user.memberships.filter(role=role).exists():
                raise EnvelopeError(400, "role_exists", "This account already has that role.")
            RoleMembership.objects.create(user=user, role=role)
            return {"delivery": "none", "user_id": str(user.pk), "invite_id": None}
        if user is None:
            user = User.objects.create_user(
                address,
                None,
                app_scope=_scope_for(door, role),
                school=school if role != PLATFORM else None,
                phone=number,
                display_name=name,
                is_active=False,
            )
            user.set_unusable_password()
            user.save(update_fields=["password"])
        elif number and not user.phone:
            user.phone = number
            user.save(update_fields=["phone"])
        RoleMembership.objects.get_or_create(user=user, role=role)
        raw = secrets.token_urlsafe(32)
        invite = InviteToken.objects.create(
            user=user,
            role=role,
            token_hash=_hash(raw),
            expires_at=now + timedelta(hours=INVITE_HOURS),
        )
    delivery = deliver(
        recipient=user.email,
        subject="Your school invite",
        body=(
            f"Hello {user.display_name},\n\n"
            f"Your invite token is {raw}. It expires in {INVITE_HOURS} hours "
            f"and can be used once.\n"
            f"Activate it at {_accept_path_for(user)}.\n"
            f"{_frontend_link(user, '/invite', raw)}"
        ),
        user=user,
    )
    return {
        "delivery": delivery,
        "user_id": str(user.pk),
        "invite_id": str(invite.pk),
    }


def revoke_invite(*, actor, door, school, invite_id):
    invite = (
        InviteToken.objects.select_related("user", "user__school")
        .filter(pk=invite_id)
        .first()
    )
    if invite is None or not _invite_visible(door, invite.user, school):
        raise EnvelopeError(404, "not_found", "Invite not found.")
    if not actor_can_grant(actor, invite.role):
        raise EnvelopeError(403, "permission_denied", "You cannot revoke that invite.")
    now = timezone.now()
    if invite.used_at is not None or invite.expires_at <= now:
        raise EnvelopeError(400, "invite_closed", "This invite is no longer pending.")
    user = invite.user
    with transaction.atomic():
        invite.delete()
        if not user.is_active:
            user.memberships.filter(role=invite.role).delete()
            if not user.memberships.exists():
                user.delete()


def accept_invite(*, door, school, token, password, id_token):
    _password, has_google = _accept_shape(password, id_token)
    row = _invite_row(token)
    if row is None or not _user_in_door(door, row.user, school):
        raise EnvelopeError(400, "invite_invalid", INVITE_INVALID)
    now = timezone.now()
    if row.used_at is not None or row.expires_at <= now:
        raise EnvelopeError(400, "invite_invalid", INVITE_INVALID)
    user = row.user
    if has_google:
        _bind_google(user, id_token)
        if not user.has_usable_password():
            user.set_unusable_password()
    else:
        _set_validated_password(user, password)
    user.is_active = True
    user.save(update_fields=["password", "is_active"])
    row.used_at = now
    row.save(update_fields=["used_at"])
    return user, start_session(user)


def request_password_reset(*, door, school, login):
    """Always the same response. A matching active account is emailed."""
    user = user_for_reset_login(login, door, school)
    if user is None and _login_text(login) == "":
        raise ValidationError({"login": ["Enter an email or phone number."]})
    if user is None:
        return {"detail": RESET_RESPONSE}
    return _send_reset(user)


def user_for_reset_login(login, door, school):
    """Return the active account for this login, or None when none matches."""
    text = _login_text(login)
    if text == "":
        return None
    bucket = _bucket(door, school, None)
    if "@" not in text:
        try:
            phone = normalize_liberian_phone(text)
        except InvalidLiberianPhone:
            return None
        return bucket.filter(phone=phone, is_active=True).first()
    try:
        address = _email(text)
    except ValidationError:
        return None
    return bucket.filter(email=address, is_active=True).first()


def user_for_invite_token(token, door, school):
    row = _invite_row(token)
    if row is None or not _user_in_door(door, row.user, school):
        return None
    return row.user


def replay_accept(*, door, school, token, password, id_token):
    """Check the original credentials again and start a new session."""
    has_password, has_google = _accept_shape(password, id_token)
    row = _invite_row(token)
    if row is None or not _user_in_door(door, row.user, school) or not row.user.is_active:
        raise EnvelopeError(400, "invite_invalid", INVITE_INVALID)
    user = row.user
    if has_google:
        _google_still_matches(user, id_token)
    elif not user.check_password(password):
        raise EnvelopeError(400, "invite_used", INVITE_USED)
    return user, start_session(user)


def confirm_password_reset(*, door, school, token, password):
    row = _reset_row(token)
    if row is None or not _user_in_door(door, row.user, school):
        raise EnvelopeError(400, "reset_invalid", RESET_INVALID)
    now = timezone.now()
    if row.used_at is not None or row.expires_at <= now or not row.user.is_active:
        raise EnvelopeError(400, "reset_invalid", RESET_INVALID)
    _set_validated_password(row.user, password)
    row.user.save(update_fields=["password"])
    row.used_at = now
    row.save(update_fields=["used_at"])
    revoke_all_sessions(row.user)
    return {"detail": "Password updated."}


def user_for_reset_token(token, door, school):
    row = _reset_row(token)
    if row is None or not _user_in_door(door, row.user, school):
        return None
    return row.user


def _invite_row(token):
    if not isinstance(token, str) or not token.strip():
        return None
    return (
        InviteToken.objects.select_related("user", "user__school")
        .filter(token_hash=_hash(token.strip()))
        .first()
    )


def _reset_row(token):
    if not isinstance(token, str) or not token.strip():
        return None
    return (
        PasswordResetToken.objects.select_related("user", "user__school")
        .filter(token_hash=_hash(token.strip()))
        .first()
    )


def _live_invite(user, now):
    return user.invite_tokens.filter(used_at__isnull=True, expires_at__gt=now).exists()


def _role_fits_door(role, door, school):
    if role == PLATFORM:
        if door != DOOR_PLATFORM:
            raise EnvelopeError(
                403,
                "permission_denied",
                "You cannot grant that role.",
            )
        return
    if role not in SCHOOL_ROLES:
        raise ValidationError({"role": ["Choose a valid role."]})
    if door == DOOR_PLATFORM and school is None:
        raise ValidationError({"school_slug": ["Choose a school for this role."]})
    if door == DOOR_SCHOOL and school is None:
        raise EnvelopeError(400, "tenant_header_required", "X-Tenant-Slug is required.")


def _scope_for(door, role):
    if door == DOOR_DEMO:
        return User.AppScope.DEMO
    return User.AppScope.CORE


def _bucket(door, school, role):
    if door == DOOR_DEMO:
        return User.objects.filter(app_scope=User.AppScope.DEMO, school__isnull=True)
    if role == PLATFORM or (door == DOOR_PLATFORM and school is None):
        return User.objects.filter(app_scope=User.AppScope.CORE, school__isnull=True)
    return User.objects.filter(app_scope=User.AppScope.CORE, school=school)


def _user_in_door(door, user, school):
    return _door_matches(door, user, school)


def _frontend_link(user, path, token):
    if user.app_scope != User.AppScope.DEMO:
        return ""
    origin = str(getattr(settings, "GOJO_APP_ORIGIN", "") or "").strip().rstrip("/")
    if not origin:
        return ""
    return f"Open it at {origin}{path}?token={quote(token, safe='')}.\n"


def _accept_path_for(user):
    if user.app_scope == User.AppScope.DEMO:
        return "/api/demo/v1/auth/invites/accept/"
    if user.school_id:
        return "/api/v1/auth/invites/accept/"
    return "/api/v1/auth/platform/invites/accept/"


def _invite_visible(door, user, school):
    if door == DOOR_PLATFORM and user.app_scope == User.AppScope.CORE:
        return True
    return _door_matches(door, user, school)


def _email(value):
    if not isinstance(value, str):
        raise ValidationError({"email": ["Enter an email address."]})
    address = value.strip().lower()
    try:
        validate_email(address)
    except DjangoValidationError as exc:
        raise ValidationError({"email": ["Enter an email address."]}) from exc
    return address


def _display_name(value):
    if not isinstance(value, str) or not value.strip():
        raise ValidationError({"display_name": ["Enter the person's name."]})
    name = value.strip()
    if len(name) > 200:
        raise ValidationError({"display_name": ["Enter a shorter name."]})
    return name


def _phone(value):
    if value in (None, ""):
        return None
    if not isinstance(value, str):
        raise ValidationError({"phone": ["Enter a Liberian phone number."]})
    try:
        return normalize_liberian_phone(value)
    except InvalidLiberianPhone as exc:
        raise ValidationError({"phone": ["Enter a Liberian phone number."]}) from exc


def _set_validated_password(user, password):
    if not isinstance(password, str) or password == "":
        raise ValidationError({"password": ["Enter a password."]})
    try:
        validate_password(password, user)
    except DjangoValidationError as exc:
        raise ValidationError({"password": list(exc.messages)}) from exc
    user.set_password(password)


def _login_text(login):
    if not isinstance(login, str):
        return ""
    return login.strip()


def _send_reset(user):
    raw = secrets.token_urlsafe(32)
    PasswordResetToken.objects.create(
        user=user,
        token_hash=_hash(raw),
        expires_at=timezone.now() + timedelta(hours=RESET_HOURS),
    )
    deliver(
        recipient=user.email,
        subject="Reset your password",
        body=(
            f"Your password reset token is {raw}. "
            f"It expires in {RESET_HOURS} hour and can be used once.\n"
            f"{_frontend_link(user, '/reset-password', raw)}"
        ),
        user=user,
    )
    return {"detail": RESET_RESPONSE}


def _accept_shape(password, id_token):
    has_password = isinstance(password, str) and password != ""
    has_google = isinstance(id_token, str) and id_token.strip() != ""
    if has_password == has_google:
        raise ValidationError(
            {"non_field_errors": ["Send a password or a Google ID token, not both."]}
        )
    return has_password, has_google


def _google_still_matches(user, id_token):
    try:
        email = verify_id_token(id_token)
    except GoogleSignInUnavailable:
        raise EnvelopeError(
            503,
            "service_unavailable",
            "Google sign-in is not configured.",
        ) from None
    except GoogleTokenInvalid:
        raise EnvelopeError(400, "invite_used", INVITE_USED) from None
    if email != user.email:
        raise EnvelopeError(400, "invite_used", INVITE_USED)


def _bind_google(user, id_token):
    try:
        email = verify_id_token(id_token)
    except GoogleSignInUnavailable:
        raise EnvelopeError(
            503,
            "service_unavailable",
            "Google sign-in is not configured.",
        ) from None
    except GoogleTokenInvalid:
        raise EnvelopeError(400, "invite_invalid", INVITE_INVALID) from None
    if email != user.email:
        raise EnvelopeError(400, "invite_invalid", INVITE_INVALID)


def _hash(raw):
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()
