"""Teachers and staff directory endpoints."""

from rest_framework.exceptions import NotFound, PermissionDenied
from rest_framework.response import Response
from rest_framework.views import APIView

from core.helpers.idempotency import enforce_unmatched_since, run_idempotent
from core.helpers.pagination import DefaultPagination
from core.helpers.permissions import (
    BURSAR,
    PRINCIPAL,
    REGISTRAR,
    SUPPORT,
    TEACHER,
    HasSchoolRole,
)
from core.service.auth import user_from_bearer
from demo.service import staff_service as staff

READ_ROLES = frozenset({PRINCIPAL, REGISTRAR, BURSAR, TEACHER, SUPPORT})
WRITE_ROLES = frozenset({PRINCIPAL})


class _TeachersView(APIView):
    authentication_classes = []
    permission_classes = [HasSchoolRole]
    app_scope = "demo"
    read_roles = READ_ROLES
    write_roles = WRITE_ROLES

    def idempotency_user(self, request):
        return user_from_bearer(request, "demo")


class TeacherListView(_TeachersView):
    def get(self, request):
        queryset = staff.filtered_staff(request.memberships, request.query_params)
        paginator = DefaultPagination()
        page = paginator.paginate_queryset(queryset, request, view=self)
        rows = [staff.staff_payload(row) for row in page]
        return paginator.get_paginated_response(rows)

    def post(self, request):
        def produce():
            return Response(staff.create_staff(request.data), status=201)

        return run_idempotent(self, request, produce)


class TeacherDetailView(_TeachersView):
    def get(self, request, staff_id):
        profile = _visible_staff(request, staff_id)
        return Response(staff.staff_payload(profile))

    def patch(self, request, staff_id):
        def produce():
            profile = _office_staff(request, staff_id)
            enforce_unmatched_since(request, profile, staff.staff_payload(profile))
            return Response(staff.update_staff(profile, request.data))

        return run_idempotent(self, request, produce)


class TeacherInviteView(_TeachersView):
    def post(self, request, staff_id):
        def produce():
            profile = _office_staff(request, staff_id)
            actor = user_from_bearer(request, "demo")
            return Response(staff.send_staff_invite(actor=actor, profile=profile))

        return run_idempotent(self, request, produce)


class TeacherDeactivateView(_TeachersView):
    def post(self, request, staff_id):
        def produce():
            profile = _office_staff(request, staff_id)
            return Response(staff.deactivate_staff(profile))

        return run_idempotent(self, request, produce)


class TeacherReactivateView(_TeachersView):
    def post(self, request, staff_id):
        def produce():
            profile = _office_staff(request, staff_id)
            return Response(staff.reactivate_staff(profile))

        return run_idempotent(self, request, produce)


def _visible_staff(request, staff_id):
    profile = staff.get_staff(request.memberships, staff_id)
    if profile is None:
        raise NotFound()
    return profile


def _office_staff(request, staff_id):
    if not {row.role for row in request.memberships}.intersection(WRITE_ROLES):
        raise PermissionDenied()
    from demo.models import StaffProfile

    profile = (
        StaffProfile.objects.select_related("campus", "division", "user")
        .filter(pk=staff_id)
        .first()
    )
    if profile is None:
        raise NotFound()
    return profile
