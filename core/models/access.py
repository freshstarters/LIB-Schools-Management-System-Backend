"""Roles, invite and reset tokens, idempotency, and mail delivery status.

These rows stay on the default database. They are not school-domain records.
"""

import uuid

from django.core.exceptions import ValidationError
from django.db import models

from core.models.platform import User


class RoleMembership(models.Model):
    """One granted role. An account may hold several. Campus is optional."""

    class Role(models.TextChoices):
        PLATFORM_SUPER_ADMIN = "platform_super_admin"
        PRINCIPAL = "principal"
        REGISTRAR = "registrar"
        BURSAR = "bursar"
        TEACHER = "teacher"
        SUPPORT_STAFF = "support_staff"
        PARENT = "parent"
        STUDENT = "student"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name="memberships",
    )
    role = models.CharField(max_length=32, choices=Role.choices)
    campus_id = models.UUIDField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["user", "role"],
                name="one_role_membership_per_user",
            )
        ]
        ordering = ["role"]

    def __str__(self):
        return f"{self.user_id} {self.role}"

    def clean(self):
        super().clean()
        user = self.user
        platform = self.role == self.Role.PLATFORM_SUPER_ADMIN
        if platform:
            if user.app_scope != User.AppScope.CORE or user.school_id:
                raise ValidationError(
                    {"role": "Platform super admin belongs on a platform account."}
                )
            return
        if user.app_scope == User.AppScope.CORE and user.school_id is None:
            raise ValidationError(
                {"role": "A platform account cannot hold a school role."}
            )

    def save(self, *args, **kwargs):
        self.full_clean()
        return super().save(*args, **kwargs)


class InviteToken(models.Model):
    """Single-use invite. Only the SHA-256 hash is stored."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name="invite_tokens",
    )
    role = models.CharField(max_length=32, choices=RoleMembership.Role.choices)
    token_hash = models.CharField(max_length=64, unique=True)
    expires_at = models.DateTimeField()
    used_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.user_id} {self.role}"


class PasswordResetToken(models.Model):
    """Single-use password reset. Only the SHA-256 hash is stored."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name="password_reset_tokens",
    )
    token_hash = models.CharField(max_length=64, unique=True)
    expires_at = models.DateTimeField()
    used_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.user_id} {self.created_at}"


class IdempotencyRecord(models.Model):
    """Stored response for one Idempotency-Key. Kept for seven days."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name="idempotency_records",
    )
    app_scope = models.CharField(max_length=8)
    key = models.CharField(max_length=64)
    request_hash = models.CharField(max_length=64)
    status_code = models.PositiveSmallIntegerField()
    response_body = models.JSONField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["user", "app_scope", "key"],
                name="one_idempotency_key_per_user",
            )
        ]

    def __str__(self):
        return f"{self.user_id} {self.key}"


class NotificationLog(models.Model):
    """Delivery status for one transactional message. The body is not stored."""

    class Channel(models.TextChoices):
        EMAIL = "email"
        SMS = "sms"

    class Status(models.TextChoices):
        SENT = "SENT"
        FAILED = "FAILED"
        UNCONFIGURED = "UNCONFIGURED"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(
        User,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="notification_logs",
    )
    channel = models.CharField(max_length=8, choices=Channel.choices)
    status = models.CharField(max_length=16, choices=Status.choices)
    recipient = models.CharField(max_length=254, blank=True)
    subject = models.CharField(max_length=200, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.channel} {self.status}"
