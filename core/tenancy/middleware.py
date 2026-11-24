"""Resolve the core tenant from the host or X-Tenant-Slug header."""

import logging

from django.conf import settings
from django.http import JsonResponse

from core.models import TenantSchool
from core.models.platform import RESERVED_SLUGS
from core.tenancy.claims import InvalidAccessToken, read_bearer
from core.tenancy.context import activate_platform, activate_tenant, reset_context

logger = logging.getLogger(__name__)

_BYPASS = ("/api/demo/v1", "/api/health", "/api/ready", "/api/schema", "/admin")
_PUBLIC_DEMO_AUTH = (
    "/api/demo/v1/auth/config",
    "/api/demo/v1/auth/login",
    "/api/demo/v1/auth/google",
    "/api/demo/v1/auth/refresh",
    "/api/demo/v1/auth/logout",
    "/api/demo/v1/auth/sessions",
    "/api/demo/v1/auth/invites/accept",
    "/api/demo/v1/auth/password-reset/request",
    "/api/demo/v1/auth/password-reset/confirm",
)


class TenantResolutionMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        reset_context()
        try:
            blocked = self._prepare(request)
            if blocked is not None:
                return blocked
            return self.get_response(request)
        finally:
            reset_context()

    def _prepare(self, request):
        path = request.path
        if _under(path, "/api/demo/v1"):
            return _reject_foreign_demo_token(request)
        if any(_under(path, prefix) for prefix in _BYPASS):
            return None
        if _under(path, "/api/v1/auth"):
            return None
        if not _under(path, "/api/v1"):
            return None
        if _under(path, "/api/v1/platform"):
            return self._prepare_platform(request)
        return self._prepare_school(request)

    def _prepare_platform(self, request):
        if settings.TENANCY_ROUTING_MODE == "subdomain":
            slug = slug_from_host(request.get_host(), settings.TENANCY_BASE_DOMAIN)
            if slug not in RESERVED_SLUGS:
                return _error(404, "tenant_not_found", "Tenant not found.")
        elif request.headers.get("X-Tenant-Slug", "").strip():
            return _error(
                403,
                "tenant_mismatch",
                "Platform requests do not take a school slug.",
            )
        claims = _claims_or_response(request)
        if isinstance(claims, JsonResponse):
            return claims
        if claims.app_scope != "core" or claims.tenant_slug:
            code = "app_scope_rejected" if claims.app_scope != "core" else "tenant_mismatch"
            return _error(403, code, "This token cannot be used on this application.")
        activate_platform()
        return None

    def _prepare_school(self, request):
        if settings.TENANCY_ROUTING_MODE == "subdomain":
            slug = slug_from_host(request.get_host(), settings.TENANCY_BASE_DOMAIN)
            if not slug or slug in RESERVED_SLUGS:
                return _error(404, "tenant_not_found", "Tenant not found.")
        else:
            raw = request.headers.get("X-Tenant-Slug", "")
            if not raw.strip():
                return _error(400, "tenant_header_required", "X-Tenant-Slug is required.")
            slug = raw.strip().lower()
            if slug in RESERVED_SLUGS:
                return _error(404, "tenant_not_found", "Tenant not found.")
        try:
            school = TenantSchool.objects.get(slug=slug)
        except TenantSchool.DoesNotExist:
            return _error(404, "tenant_not_found", "Tenant not found.")
        if school.status != TenantSchool.Status.ACTIVE:
            return _error(403, "tenant_unavailable", "Tenant is not available.")
        claims = _claims_or_response(request)
        if isinstance(claims, JsonResponse):
            return claims
        if claims.app_scope != "core":
            return _error(403, "app_scope_rejected", "This token cannot be used on this application.")
        if claims.tenant_slug != school.slug:
            logger.warning("tenant jwt mismatch path=%s slug=%s", request.path, school.slug)
            return _error(403, "tenant_mismatch", "Token tenant does not match the request.")
        activate_tenant(school)
        return None


def slug_from_host(host, base_domain):
    hostname = host.split(":")[0].lower().rstrip(".")
    base = (base_domain or "").lower().rstrip(".")
    if not base or hostname == base or not hostname.endswith("." + base):
        return None
    label = hostname[: -(len(base) + 1)]
    if not label or "." in label:
        return None
    return label


def _reject_foreign_demo_token(request):
    ignore_invalid = any(_under(request.path, root) for root in _PUBLIC_DEMO_AUTH)
    claims = _optional_claims(request, ignore_invalid=ignore_invalid)
    if isinstance(claims, JsonResponse):
        return claims
    if claims is not None and claims.app_scope != "demo":
        return _error(403, "app_scope_rejected", "This token cannot be used on this application.")
    return None


def _optional_claims(request, ignore_invalid=False):
    try:
        return read_bearer(request)
    except InvalidAccessToken:
        if ignore_invalid:
            return None
        return _error(401, "authentication_failed", "Authentication credentials were not valid.")


def _claims_or_response(request):
    claims = _optional_claims(request)
    if isinstance(claims, JsonResponse):
        return claims
    if claims is None:
        return _error(
            401,
            "authentication_failed",
            "Authentication credentials were not provided.",
        )
    return claims


def _under(path, root):
    if path.rstrip("/") == root.rstrip("/"):
        return True
    prefix = root if root.endswith("/") else root + "/"
    return path.startswith(prefix)


def _error(status, code, detail):
    return JsonResponse(
        {"code": code, "detail": detail, "fields": {}},
        status=status,
    )
