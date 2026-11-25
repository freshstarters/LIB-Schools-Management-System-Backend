"""Parents and guardians directory endpoints."""

from rest_framework.exceptions import NotFound, PermissionDenied
from rest_framework.response import Response
from rest_framework.views import APIView

from core.helpers.idempotency import enforce_unmatched_since, run_idempotent
from core.helpers.pagination import DefaultPagination
from core.helpers.permissions import (
    BURSAR,
    PARENT,
    PRINCIPAL,
    REGISTRAR,
    SUPPORT,
    TEACHER,
    HasSchoolRole,
)
from core.service.auth import user_from_bearer
from demo.service import guardian_service as guardians

READ_ROLES = frozenset({PRINCIPAL, REGISTRAR, BURSAR, TEACHER, SUPPORT, PARENT})
WRITE_ROLES = frozenset({PRINCIPAL, REGISTRAR})
SELF_WRITE_ROLES = frozenset({PRINCIPAL, REGISTRAR, PARENT})


class _ParentsView(APIView):
    authentication_classes = []
    permission_classes = [HasSchoolRole]
    app_scope = "demo"
    read_roles = READ_ROLES
    write_roles = WRITE_ROLES

    def idempotency_user(self, request):
        return user_from_bearer(request, "demo")


class ParentListView(_ParentsView):
    def get(self, request):
        actor = user_from_bearer(request, "demo")
        queryset = guardians.filtered_guardians(
            request.memberships,
            request.query_params,
            actor=actor,
        )
        paginator = DefaultPagination()
        page = paginator.paginate_queryset(queryset, request, view=self)
        rows = [
            guardians.guardian_payload(row, memberships=request.memberships)
            for row in page
        ]
        return paginator.get_paginated_response(rows)

    def post(self, request):
        def produce():
            return Response(guardians.create_guardian(request.data), status=201)

        return run_idempotent(self, request, produce)


class ParentDetailView(_ParentsView):
    write_roles = SELF_WRITE_ROLES

    def get(self, request, parent_id):
        profile = _visible_guardian(request, parent_id)
        return Response(
            guardians.guardian_payload(profile, memberships=request.memberships)
        )

    def patch(self, request, parent_id):
        def produce():
            profile = _editable_guardian(request, parent_id)
            enforce_unmatched_since(
                request,
                profile,
                guardians.guardian_payload(profile, memberships=request.memberships),
            )
            return Response(
                guardians.update_guardian(
                    profile,
                    request.data,
                    memberships=request.memberships,
                )
            )

        return run_idempotent(self, request, produce)


class ParentInviteView(_ParentsView):
    def post(self, request, parent_id):
        def produce():
            profile = _office_guardian(request, parent_id)
            actor = user_from_bearer(request, "demo")
            return Response(
                guardians.send_guardian_invite(
                    actor=actor,
                    profile=profile,
                    memberships=request.memberships,
                )
            )

        return run_idempotent(self, request, produce)


class ParentDeactivateView(_ParentsView):
    def post(self, request, parent_id):
        def produce():
            profile = _office_guardian(request, parent_id)
            return Response(
                guardians.deactivate_guardian(
                    profile,
                    memberships=request.memberships,
                )
            )

        return run_idempotent(self, request, produce)


class ParentReactivateView(_ParentsView):
    def post(self, request, parent_id):
        def produce():
            profile = _office_guardian(request, parent_id)
            return Response(
                guardians.reactivate_guardian(
                    profile,
                    memberships=request.memberships,
                )
            )

        return run_idempotent(self, request, produce)


class ParentChildrenView(_ParentsView):
    def post(self, request, parent_id):
        def produce():
            profile = _office_guardian(request, parent_id)
            return Response(
                guardians.link_child(
                    profile,
                    request.data,
                    memberships=request.memberships,
                ),
                status=201,
            )

        return run_idempotent(self, request, produce)


class ParentChildDetailView(_ParentsView):
    def delete(self, request, parent_id, link_id):
        def produce():
            profile = _office_guardian(request, parent_id)
            ok = guardians.unlink_child(profile, link_id)
            if not ok:
                raise NotFound()
            return Response(status=204)

        return run_idempotent(self, request, produce)


def _visible_guardian(request, parent_id):
    actor = user_from_bearer(request, "demo")
    profile = guardians.get_guardian(
        request.memberships,
        parent_id,
        actor=actor,
    )
    if profile is None:
        raise NotFound()
    return profile


def _office_guardian(request, parent_id):
    if not {row.role for row in request.memberships}.intersection(WRITE_ROLES):
        raise PermissionDenied()
    from demo.models import GuardianProfile

    profile = (
        GuardianProfile.objects.select_related("user")
        .prefetch_related("child_links__student")
        .filter(pk=parent_id)
        .first()
    )
    if profile is None:
        raise NotFound()
    return profile


def _editable_guardian(request, parent_id):
    roles = {row.role for row in request.memberships}
    if roles.intersection(WRITE_ROLES):
        return _office_guardian(request, parent_id)
    if PARENT not in roles:
        raise PermissionDenied()
    actor = user_from_bearer(request, "demo")
    from demo.models import GuardianProfile

    profile = (
        GuardianProfile.objects.select_related("user")
        .filter(pk=parent_id, user_id=actor.pk)
        .first()
    )
    if profile is None:
        raise NotFound()
    return profile
