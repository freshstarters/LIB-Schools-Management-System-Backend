"""Students directory, welfare, pickups, and conduct notes."""

from rest_framework.exceptions import NotFound, PermissionDenied
from rest_framework.parsers import FormParser, JSONParser, MultiPartParser
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
from demo.service import student_service as students

READ_ROLES = frozenset({PRINCIPAL, REGISTRAR, BURSAR, TEACHER, SUPPORT})
WRITE_ROLES = frozenset({PRINCIPAL, REGISTRAR})
CONDUCT_WRITE_ROLES = frozenset({PRINCIPAL, REGISTRAR, TEACHER})
WELFARE_WRITE_ROLES = frozenset({PRINCIPAL, REGISTRAR, TEACHER})


class _StudentsView(APIView):
    authentication_classes = []
    permission_classes = [HasSchoolRole]
    app_scope = "demo"
    read_roles = READ_ROLES
    write_roles = WRITE_ROLES
    parser_classes = [JSONParser, MultiPartParser, FormParser]

    def idempotency_user(self, request):
        return user_from_bearer(request, "demo")


class StudentListView(_StudentsView):
    def get(self, request):
        queryset = students.filtered_students(request.memberships, request.query_params)
        summary = students.list_summary(queryset)
        paginator = DefaultPagination()
        page = paginator.paginate_queryset(queryset, request, view=self)
        rows = [students.student_payload(row) for row in page]
        response = paginator.get_paginated_response(rows)
        response.data = {
            **response.data,
            "summary": summary,
        }
        return response

    def post(self, request):
        def produce():
            data = _payload(request)
            photo = request.FILES.get("photo")
            return Response(students.enroll_student(data, photo=photo), status=201)

        return run_idempotent(self, request, produce)


class StudentDetailView(_StudentsView):
    def get(self, request, student_id):
        student = _visible_student(request, student_id)
        return Response(students.student_payload(student))

    def patch(self, request, student_id):
        def produce():
            student = _office_student(request, student_id)
            enforce_unmatched_since(request, student, students.student_payload(student))
            data = _payload(request)
            photo = request.FILES.get("photo")
            return Response(
                students.update_student(
                    student,
                    data,
                    photo=photo,
                    memberships=request.memberships,
                )
            )

        return run_idempotent(self, request, produce)


class StudentWithdrawView(_StudentsView):
    def post(self, request, student_id):
        def produce():
            student = _office_student(request, student_id)
            return Response(students.withdraw_student(student))

        return run_idempotent(self, request, produce)


class StudentWelfareView(_StudentsView):
    write_roles = WELFARE_WRITE_ROLES

    def get(self, request, student_id):
        student = _visible_student(request, student_id)
        return Response(students.get_welfare(student))

    def put(self, request, student_id):
        def produce():
            student = _visible_student(request, student_id)
            actor = user_from_bearer(request, "demo")
            return Response(
                students.save_welfare(
                    student,
                    request.data,
                    actor=actor,
                    memberships=request.memberships,
                )
            )

        return run_idempotent(self, request, produce)


class StudentPickupListView(_StudentsView):
    write_roles = WELFARE_WRITE_ROLES

    def get(self, request, student_id):
        student = _visible_student(request, student_id)
        return Response(students.list_pickups(student))

    def post(self, request, student_id):
        def produce():
            student = _visible_student(request, student_id)
            actor = user_from_bearer(request, "demo")
            return Response(
                students.create_pickup(
                    student,
                    request.data,
                    actor=actor,
                    memberships=request.memberships,
                ),
                status=201,
            )

        return run_idempotent(self, request, produce)


class StudentPickupDetailView(_StudentsView):
    write_roles = WELFARE_WRITE_ROLES

    def patch(self, request, student_id, pickup_id):
        def produce():
            student = _visible_student(request, student_id)
            actor = user_from_bearer(request, "demo")
            body = students.update_pickup(
                student,
                pickup_id,
                request.data,
                actor=actor,
                memberships=request.memberships,
            )
            if body is None:
                raise NotFound()
            return Response(body)

        return run_idempotent(self, request, produce)

    def delete(self, request, student_id, pickup_id):
        def produce():
            student = _visible_student(request, student_id)
            actor = user_from_bearer(request, "demo")
            ok = students.delete_pickup(
                student,
                pickup_id,
                actor=actor,
                memberships=request.memberships,
            )
            if not ok:
                raise NotFound()
            return Response(status=204)

        return run_idempotent(self, request, produce)


class StudentConductView(_StudentsView):
    write_roles = CONDUCT_WRITE_ROLES

    def get(self, request, student_id):
        student = _visible_student(request, student_id)
        return Response(students.list_conduct(student))

    def post(self, request, student_id):
        def produce():
            student = _student_for_conduct_write(request, student_id)
            actor = user_from_bearer(request, "demo")
            return Response(
                students.add_conduct(
                    student,
                    request.data,
                    actor=actor,
                    memberships=request.memberships,
                ),
                status=201,
            )

        return run_idempotent(self, request, produce)


def _visible_student(request, student_id):
    student = students.get_student(request.memberships, student_id)
    if student is None:
        raise NotFound()
    return student


def _student_for_conduct_write(request, student_id):
    """404 if missing; 403 if the actor cannot write conduct for this student."""
    from demo.models import Student

    student = (
        Student.objects.select_related(
            "campus",
            "current_classroom__grade_level__division",
            "current_classroom__homeroom_teacher",
        )
        .filter(pk=student_id)
        .first()
    )
    if student is None:
        raise NotFound()
    visible = students.get_student(request.memberships, student_id)
    roles = {row.role for row in request.memberships}
    if roles.intersection(WRITE_ROLES):
        return student
    if visible is None:
        raise PermissionDenied()
    return visible


def _office_student(request, student_id):
    if not {row.role for row in request.memberships}.intersection(WRITE_ROLES):
        raise PermissionDenied()
    from demo.models import Student

    student = (
        Student.objects.select_related(
            "campus",
            "current_classroom__grade_level__division",
            "current_classroom__homeroom_teacher",
            "last_homeroom_teacher",
        )
        .filter(pk=student_id)
        .first()
    )
    if student is None:
        raise NotFound()
    return student


def _payload(request):
    if hasattr(request.data, "dict"):
        data = request.data.dict()
    else:
        data = dict(request.data)
    data.pop("photo", None)
    return data
