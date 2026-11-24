"""Replay a mutating request when the client sends Idempotency-Key."""

import hashlib
import json
import uuid
from datetime import timedelta

from django.core.serializers.json import DjangoJSONEncoder
from django.db import transaction
from django.utils import timezone
from rest_framework.response import Response

from core.helpers.errors import EnvelopeError
from core.models import IdempotencyRecord, User

RETENTION = timedelta(days=7)


def run_idempotent(view, request, produce, *, stored_body=None, on_replay=None):
    """Return the stored response, or run produce and store it.

    Login-style calls should not use this. A missing key runs produce once.
    stored_body replaces the saved JSON when the live response must not be kept.
    on_replay runs instead of returning that JSON, so a session can be issued again.
    """
    raw = request.headers.get("Idempotency-Key", "").strip()
    if not raw:
        return produce()
    try:
        key = str(uuid.UUID(raw))
    except (ValueError, TypeError, AttributeError):
        raise EnvelopeError(
            400,
            "validation_error",
            "Idempotency-Key must be a UUID.",
        ) from None
    user = view.idempotency_user(request)
    if user is None:
        return produce()
    digest = _digest(request)
    with transaction.atomic():
        User.objects.select_for_update().get(pk=user.pk)
        cutoff = timezone.now() - RETENTION
        existing = IdempotencyRecord.objects.filter(
            user=user,
            app_scope=user.app_scope,
            key=key,
        ).first()
        if existing is not None and existing.created_at < cutoff:
            existing.delete()
            existing = None
        if existing is not None:
            if existing.request_hash != digest:
                raise EnvelopeError(
                    409,
                    "idempotency_conflict",
                    "This Idempotency-Key was already used with a different request.",
                )
            if on_replay is not None:
                return on_replay()
            return Response(existing.response_body, status=existing.status_code)
        response = produce()
        body = response.data if stored_body is None else stored_body
        IdempotencyRecord.objects.create(
            user=user,
            app_scope=user.app_scope,
            key=key,
            request_hash=digest,
            status_code=response.status_code,
            response_body=_plain(body),
        )
        return response


def enforce_unmatched_since(request, instance, server_record):
    """409 when the server row is newer than If-Unmatched-Since.

    An equal timestamp is not a conflict. A missing header skips the check.
    """
    raw = request.headers.get("If-Unmatched-Since", "").strip()
    if not raw:
        return
    parsed = parse_timestamp(raw)
    if parsed is None:
        raise EnvelopeError(
            400,
            "validation_error",
            "If-Unmatched-Since is not a valid time.",
        )
    server_time = instance.updated_at
    if timezone.is_naive(server_time):
        server_time = timezone.make_aware(server_time, timezone.get_current_timezone())
    if server_time > parsed:
        raise EnvelopeError(
            409,
            "SYNC_CONFLICT",
            "The record changed since you last read it.",
            server_record=server_record,
        )


def parse_timestamp(raw):
    from django.utils.dateparse import parse_datetime

    parsed = parse_datetime(raw)
    if parsed is None:
        return None
    if timezone.is_naive(parsed):
        return timezone.make_aware(parsed, timezone.get_current_timezone())
    return parsed


def _digest(request):
    body = request.body if isinstance(request.body, (bytes, bytearray)) else b""
    payload = f"{request.method}\n{request.path}\n".encode() + body
    return hashlib.sha256(payload).hexdigest()


def _plain(data):
    if data is None:
        return None
    return json.loads(json.dumps(data, cls=DjangoJSONEncoder))
