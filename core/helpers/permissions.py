"""Section 2.2 permission matrix and the shared role helpers."""

from rest_framework.permissions import BasePermission, SAFE_METHODS

from core.models.access import RoleMembership

PLATFORM = RoleMembership.Role.PLATFORM_SUPER_ADMIN
PRINCIPAL = RoleMembership.Role.PRINCIPAL
REGISTRAR = RoleMembership.Role.REGISTRAR
BURSAR = RoleMembership.Role.BURSAR
TEACHER = RoleMembership.Role.TEACHER
SUPPORT = RoleMembership.Role.SUPPORT_STAFF
PARENT = RoleMembership.Role.PARENT
STUDENT = RoleMembership.Role.STUDENT

ROLE_ORDER = (
    PLATFORM,
    PRINCIPAL,
    REGISTRAR,
    BURSAR,
    TEACHER,
    SUPPORT,
    PARENT,
    STUDENT,
)
SCHOOL_ROLES = frozenset(role for role in ROLE_ORDER if role != PLATFORM)
REGISTRAR_GRANT = frozenset({TEACHER, SUPPORT, PARENT, STUDENT})

READ = "read"
CREATE = "create"
UPDATE = "update"
DELETE = "delete"
LOCK = "lock"
APPROVE = "approve"
TOPUP = "topup"
PAY = "pay"
ACTIONS = (READ, CREATE, UPDATE, DELETE, LOCK, APPROVE, TOPUP, PAY)

CATALOG_READ = frozenset({PRINCIPAL, REGISTRAR, BURSAR, TEACHER})
CATALOG_WRITE = frozenset({PRINCIPAL, REGISTRAR})


def _plain(*actions):
    return frozenset(actions)


def _scoped(*actions):
    return frozenset(f"scoped:{action}" for action in actions)


def _cell(*parts):
    granted = set()
    for part in parts:
        granted.update(part)
    return frozenset(granted)


_CRUD = _plain(CREATE, READ, UPDATE, DELETE)
_CRU = _plain(CREATE, READ, UPDATE)
_READ = _plain(READ)
_RW = _plain(READ, UPDATE)

# module -> role -> granted action names. Scoped names start with "scoped:".
MATRIX = {
    "tenant_provisioning": {PLATFORM: _CRUD},
    "dashboard": {
        PLATFORM: _READ,
        PRINCIPAL: _READ,
        REGISTRAR: _READ,
        BURSAR: _READ,
        TEACHER: _scoped(READ),
        SUPPORT: _scoped(READ),
    },
    "students": {
        PRINCIPAL: _CRUD,
        REGISTRAR: _CRUD,
        BURSAR: _READ,
        TEACHER: _scoped(READ, UPDATE),
        SUPPORT: _READ,
        PARENT: _scoped(READ),
        STUDENT: _scoped(READ),
    },
    "admissions": {
        PRINCIPAL: _CRUD,
        REGISTRAR: _CRUD,
        BURSAR: _READ,
    },
    "fees": {
        PRINCIPAL: _cell(_READ, _plain(APPROVE)),
        REGISTRAR: _READ,
        BURSAR: _CRUD,
        PARENT: _scoped(READ, PAY),
    },
    "classes": {
        PRINCIPAL: _CRUD,
        REGISTRAR: _CRUD,
        BURSAR: _READ,
        TEACHER: _scoped(READ),
        PARENT: _scoped(READ),
        STUDENT: _scoped(READ),
    },
    "attendance": {
        PRINCIPAL: _CRUD,
        REGISTRAR: _CRUD,
        TEACHER: _scoped(READ, UPDATE),
        PARENT: _scoped(READ),
        STUDENT: _scoped(READ),
    },
    "gradebook": {
        PRINCIPAL: _cell(_CRUD, _plain(LOCK)),
        REGISTRAR: _cell(_CRUD, _plain(LOCK)),
        TEACHER: _scoped(READ, UPDATE),
        PARENT: _scoped(READ),
        STUDENT: _scoped(READ),
    },
    "waec": {
        PRINCIPAL: _CRUD,
        REGISTRAR: _CRUD,
        BURSAR: _RW,
        TEACHER: _scoped(READ),
        PARENT: _scoped(READ),
        STUDENT: _scoped(READ),
    },
    "report_cards": {
        PRINCIPAL: _CRUD,
        REGISTRAR: _CRUD,
        TEACHER: _scoped(READ),
        PARENT: _scoped(READ),
        STUDENT: _scoped(READ),
    },
    "staff": {
        PRINCIPAL: _CRUD,
        REGISTRAR: _READ,
        BURSAR: _READ,
        TEACHER: _READ,
        SUPPORT: _READ,
    },
    "guardians": {
        PRINCIPAL: _CRUD,
        REGISTRAR: _CRUD,
        BURSAR: _READ,
        TEACHER: _scoped(READ),
        SUPPORT: _READ,
        PARENT: _scoped(READ, UPDATE),
    },
    "library": {
        PRINCIPAL: _CRUD,
        REGISTRAR: _READ,
        TEACHER: _READ,
        SUPPORT: _CRUD,
        PARENT: _scoped(READ),
        STUDENT: _scoped(READ),
    },
    "transport": {
        PRINCIPAL: _CRUD,
        REGISTRAR: _READ,
        TEACHER: _READ,
        SUPPORT: _CRUD,
        PARENT: _scoped(READ),
        STUDENT: _scoped(READ),
    },
    "announcements": {
        PRINCIPAL: _cell(_CRUD, _plain(TOPUP)),
        REGISTRAR: _CRU,
        BURSAR: _cell(_READ, _plain(TOPUP)),
        TEACHER: _READ,
        SUPPORT: _READ,
        PARENT: _READ,
        STUDENT: _READ,
    },
    "ai": {
        PRINCIPAL: _READ,
        REGISTRAR: _READ,
        BURSAR: _READ,
        TEACHER: _scoped(READ),
    },
    "settings": {
        PRINCIPAL: _CRUD,
        REGISTRAR: _READ,
        BURSAR: _RW,
    },
    "audit": {
        PLATFORM: _READ,
        PRINCIPAL: _READ,
        REGISTRAR: _READ,
        BURSAR: _READ,
    },
}


def allows(role, module, action, *, scope_proven=False):
    """Return whether one role may perform the action.

    A scoped grant counts only when the caller has proven the record is theirs.
    """
    granted = MATRIX.get(module, {}).get(role, frozenset())
    if action in granted:
        return True
    return scope_proven and f"scoped:{action}" in granted


def highest_role(user):
    """The label stored on the access token. Checks still use every membership."""
    held = set(user.memberships.values_list("role", flat=True))
    for role in ROLE_ORDER:
        if role in held:
            return role
    return ""


def actor_can_grant(actor, role):
    held = set(actor.memberships.values_list("role", flat=True))
    if role == PLATFORM:
        return (
            PLATFORM in held
            and actor.school_id is None
            and actor.app_scope == "core"
        )
    if role not in SCHOOL_ROLES:
        return False
    if PLATFORM in held or PRINCIPAL in held:
        return True
    return REGISTRAR in held and role in REGISTRAR_GRANT


def campus_matches(memberships, allowed_roles, campus_id):
    """True when a granting membership is school-wide or covers this campus."""
    if campus_id is None:
        return True
    for membership in memberships:
        if membership.role not in allowed_roles:
            continue
        if membership.campus_id is None or membership.campus_id == campus_id:
            return True
    return False


class HasSchoolRole(BasePermission):
    """Pass when any membership may read or write the view's role sets."""

    def has_permission(self, request, view):
        from core.service.auth import user_from_bearer

        scope = getattr(view, "app_scope", "core")
        user = user_from_bearer(request, scope)
        memberships = list(user.memberships.all())
        request.school_user = user
        request.memberships = memberships
        allowed = view.read_roles if request.method in SAFE_METHODS else view.write_roles
        roles = {membership.role for membership in memberships}
        if not roles.intersection(allowed):
            return False
        campus = None
        resolver = getattr(view, "resource_campus_id", None)
        if resolver is not None:
            campus = resolver(request)
        return campus_matches(memberships, allowed, campus)


class IsCampusScoped(BasePermission):
    """Pass when a granting membership covers the view's campus."""

    def has_permission(self, request, view):
        resolver = getattr(view, "resource_campus_id", None)
        if resolver is None:
            return True
        campus = resolver(request)
        memberships = getattr(request, "memberships", None)
        if memberships is None:
            return campus is None
        allowed = view.read_roles if request.method in SAFE_METHODS else view.write_roles
        return campus_matches(memberships, allowed, campus)


class IsPlatformSuperAdmin(BasePermission):
    """Platform door only. A school account never passes."""

    def has_permission(self, request, view):
        from core.service.auth import user_from_bearer

        user = user_from_bearer(request, "core")
        if user.school_id is not None:
            return False
        return user.memberships.filter(role=PLATFORM).exists()
