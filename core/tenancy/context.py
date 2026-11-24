"""Request-local tenant and platform context."""

from contextlib import contextmanager
from contextvars import ContextVar

_tenant = ContextVar("current_tenant", default=None)
_platform = ContextVar("platform_context", default=False)


class TenantContextError(RuntimeError):
    """A tenant-scoped query ran without a resolved tenant."""


def get_current_tenant():
    return _tenant.get()


def platform_context_active():
    return bool(_platform.get())


def activate_tenant(tenant):
    _tenant.set(tenant)


def activate_platform():
    _platform.set(True)


def reset_context():
    _tenant.set(None)
    _platform.set(False)


@contextmanager
def tenant_context(tenant):
    token = _tenant.set(tenant)
    try:
        yield tenant
    finally:
        _tenant.reset(token)


@contextmanager
def platform_context():
    token = _platform.set(True)
    try:
        yield
    finally:
        _platform.reset(token)
