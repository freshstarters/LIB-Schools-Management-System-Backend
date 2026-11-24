"""Transactional email. SMS stays unconfigured until its gateway story."""

import logging

from django.conf import settings
from django.core.mail import send_mail

from core.models.access import NotificationLog

logger = logging.getLogger(__name__)


def deliver(*, recipient, subject, body, channel="email", user=None):
    """Send one message and record the status. The body is not logged."""
    if channel != NotificationLog.Channel.EMAIL:
        return _record(
            user=user,
            recipient=recipient,
            subject=subject,
            channel=channel,
            status=NotificationLog.Status.UNCONFIGURED,
        )
    if not settings.DEBUG and not settings.EMAIL_CONFIGURED:
        return _record(
            user=user,
            recipient=recipient,
            subject=subject,
            channel=channel,
            status=NotificationLog.Status.UNCONFIGURED,
        )
    try:
        send_mail(
            subject,
            body,
            settings.DEFAULT_FROM_EMAIL or "noreply@localhost",
            [recipient],
            fail_silently=False,
        )
    except Exception:
        logger.warning("transactional email failed recipient=%s", recipient)
        return _record(
            user=user,
            recipient=recipient,
            subject=subject,
            channel=channel,
            status=NotificationLog.Status.FAILED,
        )
    return _record(
        user=user,
        recipient=recipient,
        subject=subject,
        channel=channel,
        status=NotificationLog.Status.SENT,
    )


def _record(*, user, recipient, subject, channel, status):
    NotificationLog.objects.create(
        user=user,
        recipient=recipient or "",
        subject=subject,
        channel=channel,
        status=status,
    )
    return status
