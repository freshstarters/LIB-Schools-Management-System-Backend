"""Platform registry models. These rows always live on the default database."""

import re
import uuid

from django.contrib.auth.base_user import AbstractBaseUser, BaseUserManager
from django.contrib.auth.models import PermissionsMixin
from django.core.exceptions import ValidationError
from django.db import models

from core.helpers.phone import InvalidLiberianPhone, normalize_liberian_phone

from core.tenancy.context import (
    TenantContextError,
    get_current_tenant,
    platform_context_active,
)

RESERVED_SLUGS = frozenset({"www", "api", "admin", "platform"})
_SLUG = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")


def validate_school_slug(value):
    if (
        not isinstance(value, str)
        or not _SLUG.fullmatch(value)
        or len(value) > 40
        or value in RESERVED_SLUGS
    ):
        raise ValidationError("Enter a valid school slug.")


class TenantSchool(models.Model):
    """Registry row for one core school. Credentials for its database stay encrypted."""

    class Status(models.TextChoices):
        PROVISIONING = "PROVISIONING"
        ACTIVE = "ACTIVE"
        SUSPENDED = "SUSPENDED"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    slug = models.SlugField(max_length=40, unique=True, validators=[validate_school_slug])
    name = models.CharField(max_length=200)
    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.PROVISIONING,
    )
    db_name = models.CharField(max_length=63, blank=True)
    db_alias = models.CharField(max_length=63, blank=True)
    db_user = models.CharField(max_length=63, blank=True)
    db_host = models.CharField(max_length=255, blank=True)
    db_port = models.PositiveIntegerField(null=True, blank=True)
    db_password_encrypted = models.TextField(blank=True)
    schema_version = models.CharField(max_length=64, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["slug"]

    def __str__(self):
        return self.slug

    def __repr__(self):
        return f"<TenantSchool {self.slug} {self.status}>"

    def clean(self):
        super().clean()
        validate_school_slug(self.slug)

    def save(self, *args, **kwargs):
        self.full_clean()
        return super().save(*args, **kwargs)


class TenantMigrationRecord(models.Model):
    """One fleet-migration attempt for one tenant database."""

    class Status(models.TextChoices):
        SUCCEEDED = "succeeded"
        FAILED = "failed"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    tenant = models.ForeignKey(
        TenantSchool,
        on_delete=models.PROTECT,
        related_name="migration_records",
    )
    schema_hash = models.CharField(max_length=64, blank=True)
    applied_at = models.DateTimeField(auto_now_add=True)
    status = models.CharField(max_length=20, choices=Status.choices)
    error = models.TextField(blank=True)

    class Meta:
        ordering = ["-applied_at"]

    def __str__(self):
        return f"{self.tenant_id} {self.status}"


class TenantManager(models.Manager):
    """Fail closed unless a tenant, or an explicit platform escape, is active."""

    def get_queryset(self):
        queryset = super().get_queryset()
        if platform_context_active():
            return queryset
        tenant = get_current_tenant()
        if tenant is None:
            raise TenantContextError("Query outside tenant context.")
        return queryset.filter(school_id=tenant.pk)


class TenantScopedModel(models.Model):
    """School-owned row. Shared mode filters by school. Per-tenant mode uses its database."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    school = models.ForeignKey(
        TenantSchool,
        on_delete=models.PROTECT,
        db_index=True,
        db_constraint=False,
        related_name="+",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    objects = TenantManager()

    class Meta:
        abstract = True
        # Django internals use the automatic base manager. The tenant filter
        # stays on `objects`, so collector and admin queries can still run.

    def save(self, *args, **kwargs):
        self._guard_tenant()
        self.full_clean()
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        self._guard_tenant()
        return super().delete(*args, **kwargs)

    def _guard_tenant(self):
        if platform_context_active():
            return
        tenant = get_current_tenant()
        if tenant is None:
            raise TenantContextError("Query outside tenant context.")
        if self.school_id != tenant.pk:
            raise TenantContextError("Cross-tenant write.")


def account_username(email, app_scope, school):
    """Globally unique login key. Callers never type this value."""
    address = email.strip().lower()
    if app_scope == User.AppScope.DEMO:
        return f"demo:{address}"
    if school is None:
        return f"platform:{address}"
    return f"school:{school.slug}:{address}"


class UserManager(BaseUserManager):
    def create_user(self, email, password=None, *, app_scope, school=None, phone=None, **extra):
        user = self.model(
            email=email,
            username="pending",
            app_scope=app_scope,
            school=school,
            phone=phone or None,
            **extra,
        )
        user.set_password(password)
        user.save(using=self._db)
        return user

    def create_superuser(self, username, email, password=None, **extra):
        extra.setdefault("is_staff", True)
        extra.setdefault("is_superuser", True)
        extra.setdefault("is_active", True)
        return self.create_user(
            email,
            password,
            app_scope=User.AppScope.CORE,
            school=None,
            **extra,
        )


class User(AbstractBaseUser, PermissionsMixin):
    """One account in Gojo, on the platform, or at a single core school.

    The same email may exist once in each bucket. ``username`` is the
    internal key Django requires. Sign-in uses email or phone.
    """

    class AppScope(models.TextChoices):
        DEMO = "demo", "Gojo"
        CORE = "core", "Core"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    username = models.CharField(max_length=320, unique=True)
    email = models.EmailField()
    display_name = models.CharField(max_length=200, blank=True)
    phone = models.CharField(max_length=13, null=True, blank=True)
    app_scope = models.CharField(max_length=8, choices=AppScope.choices)
    school = models.ForeignKey(
        TenantSchool,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="accounts",
    )
    is_active = models.BooleanField(default=True)
    is_staff = models.BooleanField(default=False)
    date_joined = models.DateTimeField(auto_now_add=True)

    objects = UserManager()

    USERNAME_FIELD = "username"
    REQUIRED_FIELDS = ["email"]

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=models.Q(app_scope="demo", school__isnull=True)
                | models.Q(app_scope="core"),
                name="user_demo_has_no_school",
            ),
            models.CheckConstraint(
                condition=models.Q(phone__isnull=True)
                | models.Q(phone__regex=r"^\+231[0-9]{9}$"),
                name="user_phone_e164",
            ),
            models.UniqueConstraint(
                fields=["email"],
                condition=models.Q(app_scope="demo"),
                name="user_demo_email_unique",
            ),
            models.UniqueConstraint(
                fields=["email"],
                condition=models.Q(app_scope="core", school__isnull=True),
                name="user_platform_email_unique",
            ),
            models.UniqueConstraint(
                fields=["school", "email"],
                condition=models.Q(app_scope="core", school__isnull=False),
                name="user_school_email_unique",
            ),
            models.UniqueConstraint(
                fields=["phone"],
                condition=models.Q(app_scope="demo", phone__isnull=False),
                name="user_demo_phone_unique",
            ),
            models.UniqueConstraint(
                fields=["phone"],
                condition=models.Q(
                    app_scope="core",
                    school__isnull=True,
                    phone__isnull=False,
                ),
                name="user_platform_phone_unique",
            ),
            models.UniqueConstraint(
                fields=["school", "phone"],
                condition=models.Q(app_scope="core", school__isnull=False, phone__isnull=False),
                name="user_school_phone_unique",
            ),
        ]

    def __str__(self):
        return self.email

    def clean(self):
        super().clean()
        self.email = (self.email or "").strip().lower()
        if not self.email:
            raise ValidationError({"email": "Email is required."})
        if self.phone:
            try:
                self.phone = normalize_liberian_phone(self.phone)
            except InvalidLiberianPhone as exc:
                raise ValidationError({"phone": "Enter a Liberian phone number."}) from exc
        else:
            self.phone = None
        if self.app_scope == self.AppScope.DEMO and self.school_id:
            raise ValidationError({"school": "A Gojo account cannot belong to a core school."})
        self.username = account_username(self.email, self.app_scope, self.school)

    def save(self, *args, **kwargs):
        self.full_clean()
        return super().save(*args, **kwargs)

    def get_short_name(self):
        return self.email


class RefreshToken(models.Model):
    """Stored hash of one refresh cookie. The raw token is never saved."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name="refresh_tokens",
    )
    token_hash = models.CharField(max_length=64, unique=True)
    expires_at = models.DateTimeField()
    revoked_at = models.DateTimeField(null=True, blank=True)
    replaced_by = models.ForeignKey(
        "self",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="replaced",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.user_id} {self.created_at}"
