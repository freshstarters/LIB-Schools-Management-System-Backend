"""Sign-in, refresh rotation, and session listing for one account bucket."""

import hashlib
import secrets
import uuid
from datetime import timedelta

from django.conf import settings
from django.utils import timezone

from core.helpers.errors import EnvelopeError
from core.helpers.permissions import highest_role
from core.helpers.phone import InvalidLiberianPhone, normalize_liberian_phone
from core.integrations.google_oauth import (
    GoogleSignInUnavailable,
    GoogleTokenInvalid,
    google_credentials_present,
    verify_id_token,
)
from core.models import RefreshToken, TenantSchool, User
from core.models.platform import RESERVED_SLUGS
from core.tenancy.claims import InvalidAccessToken, encode_access_token, read_bearer
from core.tenancy.middleware import slug_from_host

DOOR_DEMO = "demo"
DOOR_SCHOOL = "school"
DOOR_PLATFORM = "platform"

ACCESS_MINUTES = 15
REFRESH_DAYS = 7
REFRESH_SECONDS = REFRESH_DAYS * 24 * 60 * 60

AUTH_FAILED = "Authentication credentials were not valid."
AUTH_MISSING = "Authentication credentials were not provided."
SCOPE_REJECTED = "This token cannot be used on this application."
TENANT_MISMATCH = "Token tenant does not match the request."

SMS_CONSENT = (
    "Guardians can opt in or out of SMS messages. "
    "Consent can be changed later and is not assumed."
)
PRIVACY_WARNING = (
    "Privacy notice is not configured. Set the hosting region "
    "and data controller before production."
)


def resolve_door(door, request):
    """Return the school for a school door. Demo and platform return None."""
    if door == DOOR_DEMO:
        return None
    if door == DOOR_PLATFORM:
        _guard_platform(request)
        return None
    return _resolve_school(request)


def authenticate_password(door, login, password, school):
    user = _match_login(door, login, school)
    if user is None or not user.is_active or not user.check_password(password):
        raise EnvelopeError(401, "authentication_failed", AUTH_FAILED)
    return user


def authenticate_google(door, token, school):
    if not google_credentials_present():
        raise GoogleSignInUnavailable()
    try:
        email = verify_id_token(token)
    except GoogleSignInUnavailable:
        raise
    except GoogleTokenInvalid:
        raise EnvelopeError(401, "authentication_failed", AUTH_FAILED) from None
    user = _match_email(door, email, school)
    if user is None or not user.is_active:
        raise EnvelopeError(401, "authentication_failed", AUTH_FAILED)
    return user


def start_session(user):
    now = timezone.now()
    access = encode_access_token(
        {
            "sub": str(user.pk),
            "app_scope": user.app_scope,
            "tenant_slug": tenant_slug_for(user),
            "role": highest_role(user),
            "iat": now,
            "exp": now + timedelta(minutes=ACCESS_MINUTES),
        }
    )
    raw = secrets.token_urlsafe(32)
    RefreshToken.objects.create(
        user=user,
        token_hash=_hash(raw),
        expires_at=now + timedelta(days=REFRESH_DAYS),
    )
    return access, raw


def rotate_session(door, raw, school):
    row = _row_for_door(door, raw, school)
    now = timezone.now()
    if row.revoked_at is not None or row.replaced_by_id is not None:
        _revoke_all(row.user, now)
        raise EnvelopeError(401, "authentication_failed", AUTH_FAILED)
    if row.expires_at <= now or not row.user.is_active:
        raise EnvelopeError(401, "authentication_failed", AUTH_FAILED)
    access, new_raw = start_session(row.user)
    successor = RefreshToken.objects.get(token_hash=_hash(new_raw))
    row.revoked_at = now
    row.replaced_by = successor
    row.save(update_fields=["revoked_at", "replaced_by"])
    return access, new_raw, row.user


def revoke_all_sessions(user):
    """Revoke every live refresh token for this account."""
    _revoke_all(user, timezone.now())


def revoke_browser_session(cookies, door, session_id):
    """Revoke one refresh cookie this browser sent. Return its cookie name and path."""
    now = timezone.now()
    target = str(session_id)
    for name, raw in cookies.items():
        row = _cookie_row(name, raw, door, now)
        if row is None or str(row.pk) != target:
            continue
        row.revoked_at = now
        row.save(update_fields=["revoked_at"])
        return name, cookie_path_for(row.user)
    raise EnvelopeError(404, "not_found", "Session not found.")


def revoke_presented(raw):
    """Revoke one browser session. Other devices and accounts stay signed in."""
    if not raw:
        return
    RefreshToken.objects.filter(
        token_hash=_hash(raw),
        revoked_at__isnull=True,
    ).update(revoked_at=timezone.now())


def sessions_from_cookies(cookies, door):
    now = timezone.now()
    found = []
    for name, raw in cookies.items():
        if not name.startswith("refresh_") or not raw:
            continue
        if door == DOOR_DEMO and name != "refresh_demo":
            continue
        if door != DOOR_DEMO and name == "refresh_demo":
            continue
        row = (
            RefreshToken.objects.select_related("user", "user__school")
            .filter(token_hash=_hash(raw))
            .first()
        )
        if row is None or not _usable(row, now):
            continue
        if cookie_name_for(row.user) != name:
            continue
        if door == DOOR_DEMO and account_kind(row.user) != DOOR_DEMO:
            continue
        if door != DOOR_DEMO and account_kind(row.user) == DOOR_DEMO:
            continue
        found.append(_session_entry(row))
    found.sort(key=lambda item: (item["account"], item["tenant_slug"], item["email"]))
    return found


def user_from_bearer(request, expected_scope):
    try:
        claims = read_bearer(request)
    except InvalidAccessToken:
        raise EnvelopeError(401, "authentication_failed", AUTH_FAILED) from None
    if claims is None:
        raise EnvelopeError(401, "authentication_failed", AUTH_MISSING)
    if claims.app_scope != expected_scope:
        raise EnvelopeError(403, "app_scope_rejected", SCOPE_REJECTED)
    try:
        user_id = uuid.UUID(claims.sub)
    except (ValueError, TypeError, AttributeError):
        raise EnvelopeError(401, "authentication_failed", AUTH_FAILED) from None
    user = (
        User.objects.select_related("school")
        .filter(pk=user_id, is_active=True)
        .first()
    )
    if user is None or user.app_scope != claims.app_scope:
        raise EnvelopeError(401, "authentication_failed", AUTH_FAILED)
    if tenant_slug_for(user) != claims.tenant_slug:
        raise EnvelopeError(401, "authentication_failed", AUTH_FAILED)
    return user


def public_user(user):
    return {
        "id": str(user.pk),
        "email": user.email,
        "phone": user.phone,
        "app_scope": user.app_scope,
        "tenant_slug": tenant_slug_for(user),
        "role": highest_role(user),
        "display_name": user.display_name,
        "account": account_kind(user),
    }


def access_payload(user, access):
    return {
        "access": access,
        "expires_in": ACCESS_MINUTES * 60,
        "token_type": "Bearer",
        "user": public_user(user),
    }


def public_config(csrf_token):
    region = settings.HOSTING_REGION
    controller = settings.DATA_CONTROLLER_NAME
    configured = bool(region and controller)
    return {
        "google_enabled": google_credentials_present(),
        "hosting_region": region,
        "data_controller_name": controller,
        "csrf_token": csrf_token,
        "privacy_notice": {
            "configured": configured,
            "warning": "" if configured else PRIVACY_WARNING,
            "hosting_region": region,
            "data_controller_name": controller,
            "sms_consent": SMS_CONSENT,
        },
    }


def account_kind(user):
    if user.app_scope == User.AppScope.DEMO:
        return DOOR_DEMO
    if user.school_id:
        return DOOR_SCHOOL
    return DOOR_PLATFORM


def tenant_slug_for(user):
    if user.school_id:
        return user.school.slug
    return ""


def cookie_name_for(user):
    kind = account_kind(user)
    if kind == DOOR_DEMO:
        return "refresh_demo"
    if kind == DOOR_PLATFORM:
        return "refresh_platform"
    return f"refresh_{user.school.slug}"


def cookie_path_for(user):
    if user.app_scope == User.AppScope.DEMO:
        return "/api/demo/v1/auth/"
    return "/api/v1/auth/"


def cookie_for_door(door, school):
    if door == DOOR_DEMO:
        return "refresh_demo", "/api/demo/v1/auth/"
    if door == DOOR_PLATFORM:
        return "refresh_platform", "/api/v1/auth/"
    return f"refresh_{school.slug}", "/api/v1/auth/"


def _guard_platform(request):
    header = request.headers.get("X-Tenant-Slug", "").strip()
    if header:
        raise EnvelopeError(
            400,
            "tenant_header_rejected",
            "Platform sign-in does not take a school slug.",
        )
    if settings.TENANCY_ROUTING_MODE != "subdomain":
        return
    slug = slug_from_host(request.get_host(), settings.TENANCY_BASE_DOMAIN)
    if slug != "platform":
        raise EnvelopeError(404, "tenant_not_found", "Tenant not found.")


def _resolve_school(request):
    if settings.TENANCY_ROUTING_MODE == "subdomain":
        slug = slug_from_host(request.get_host(), settings.TENANCY_BASE_DOMAIN)
        if not slug or slug in RESERVED_SLUGS:
            raise EnvelopeError(404, "tenant_not_found", "Tenant not found.")
    else:
        raw = request.headers.get("X-Tenant-Slug", "")
        if not raw.strip():
            raise EnvelopeError(
                400,
                "tenant_header_required",
                "X-Tenant-Slug is required.",
            )
        slug = raw.strip().lower()
        if slug in RESERVED_SLUGS:
            raise EnvelopeError(404, "tenant_not_found", "Tenant not found.")
    try:
        school = TenantSchool.objects.get(slug=slug)
    except TenantSchool.DoesNotExist:
        raise EnvelopeError(404, "tenant_not_found", "Tenant not found.") from None
    if school.status != TenantSchool.Status.ACTIVE:
        raise EnvelopeError(403, "tenant_unavailable", "Tenant is not available.")
    return school


def _match_login(door, login, school):
    text = login.strip()
    if "@" in text:
        return _match_email(door, text, school)
    try:
        phone = normalize_liberian_phone(text)
    except InvalidLiberianPhone:
        return None
    return _bucket(door, school).filter(phone=phone).select_related("school").first()


def _match_email(door, email, school):
    return (
        _bucket(door, school)
        .filter(email=email.strip().lower())
        .select_related("school")
        .first()
    )


def _bucket(door, school):
    if door == DOOR_DEMO:
        return User.objects.filter(app_scope=User.AppScope.DEMO, school__isnull=True)
    if door == DOOR_PLATFORM:
        return User.objects.filter(app_scope=User.AppScope.CORE, school__isnull=True)
    return User.objects.filter(app_scope=User.AppScope.CORE, school=school)


def _row_for_door(door, raw, school):
    if not raw:
        raise EnvelopeError(401, "authentication_failed", AUTH_FAILED)
    row = (
        RefreshToken.objects.select_related("user", "user__school")
        .filter(token_hash=_hash(raw))
        .first()
    )
    if row is None or not _door_matches(door, row.user, school):
        raise EnvelopeError(401, "authentication_failed", AUTH_FAILED)
    return row


def _door_matches(door, user, school):
    if door == DOOR_DEMO:
        return user.app_scope == User.AppScope.DEMO and user.school_id is None
    if door == DOOR_PLATFORM:
        return user.app_scope == User.AppScope.CORE and user.school_id is None
    return (
        school is not None
        and user.app_scope == User.AppScope.CORE
        and user.school_id == school.pk
    )


def _usable(row, now):
    return (
        row.revoked_at is None
        and row.replaced_by_id is None
        and row.expires_at > now
        and row.user.is_active
    )


def _revoke_all(user, now):
    RefreshToken.objects.filter(user=user, revoked_at__isnull=True).update(revoked_at=now)


def _cookie_row(name, raw, door, now):
    if not name.startswith("refresh_") or not raw:
        return None
    if door == DOOR_DEMO and name != "refresh_demo":
        return None
    if door != DOOR_DEMO and name == "refresh_demo":
        return None
    row = (
        RefreshToken.objects.select_related("user", "user__school")
        .filter(token_hash=_hash(raw))
        .first()
    )
    if row is None or not _usable(row, now):
        return None
    if cookie_name_for(row.user) != name:
        return None
    if door == DOOR_DEMO and account_kind(row.user) != DOOR_DEMO:
        return None
    if door != DOOR_DEMO and account_kind(row.user) == DOOR_DEMO:
        return None
    return row


def _session_entry(row):
    user = row.user
    return {
        "account": account_kind(user),
        "id": str(user.pk),
        "session_id": str(row.pk),
        "email": user.email,
        "tenant_slug": tenant_slug_for(user),
        "issued_at": row.created_at.isoformat(),
    }


def _hash(raw):
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()
