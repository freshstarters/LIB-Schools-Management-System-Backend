"""Classrooms, subjects, weekly timetable, absences, and substitutions."""

from rest_framework.exceptions import NotFound, PermissionDenied
from rest_framework.response import Response
from rest_framework.views import APIView

from core.helpers.idempotency import enforce_unmatched_since, run_idempotent
from core.helpers.permissions import (
    BURSAR,
    PARENT,
    PRINCIPAL,
    REGISTRAR,
    STUDENT,
    TEACHER,
    HasSchoolRole,
)
from core.service.auth import user_from_bearer
from demo.models import Classroom, TimetableSlot
from demo.service import class_service
from demo.service import timetable_service as timetable
from demo.service.timetable_service import monrovia_today
from django.utils.dateparse import parse_date

READ_ROLES = frozenset({PRINCIPAL, REGISTRAR, BURSAR, TEACHER, PARENT, STUDENT})
WRITE_ROLES = frozenset({PRINCIPAL, REGISTRAR})


class _AcademicsView(APIView):
    authentication_classes = []
    permission_classes = [HasSchoolRole]
    app_scope = "demo"
    read_roles = READ_ROLES
    write_roles = WRITE_ROLES

    def idempotency_user(self, request):
        return user_from_bearer(request, "demo")


class SubjectListView(_AcademicsView):
    write_roles = frozenset()

    def get(self, request):
        return Response(class_service.list_subjects())


class TeacherOptionListView(_AcademicsView):
    write_roles = frozenset()

    def get(self, request):
        campus_id = request.query_params.get("campus") or None
        return Response(class_service.teacher_options(campus_id))


class ClassroomListView(_AcademicsView):
    def get(self, request):
        return Response(class_service.list_classrooms(request.memberships))

    def post(self, request):
        def produce():
            return Response(class_service.create_classroom(request.data), status=201)

        return run_idempotent(self, request, produce)


class ClassroomDetailView(_AcademicsView):
    def get(self, request, classroom_id):
        body = class_service.get_classroom(request.memberships, classroom_id)
        if body is None:
            raise NotFound()
        return Response(body)

    def patch(self, request, classroom_id):
        def produce():
            classroom = _writable_classroom(request, classroom_id)
            enforce_unmatched_since(request, classroom, timetable.classroom_payload(classroom))
            return Response(class_service.update_classroom(classroom, request.data))

        return run_idempotent(self, request, produce)

    def delete(self, request, classroom_id):
        def produce():
            class_service.delete_classroom(_writable_classroom(request, classroom_id))
            return Response(status=204)

        return run_idempotent(self, request, produce)


class ClassroomSubjectsView(_AcademicsView):
    def get(self, request, classroom_id):
        body = class_service.get_classroom(request.memberships, classroom_id)
        if body is None:
            raise NotFound()
        return Response(body["subjects"])

    def put(self, request, classroom_id):
        def produce():
            classroom = _writable_classroom(request, classroom_id)
            return Response(class_service.replace_subjects(classroom, request.data))

        return run_idempotent(self, request, produce)


class TimetableListView(_AcademicsView):
    def get(self, request):
        classroom_id = request.query_params.get("classroom") or None
        teacher_id = request.query_params.get("teacher") or None
        rows = timetable.slots_for_actor(
            request.memberships,
            classroom_id=classroom_id,
            teacher_id=teacher_id,
        )
        return Response([timetable.slot_payload(row) for row in rows])

    def post(self, request):
        def produce():
            return Response(timetable.slot_payload(timetable.create_slot(request.data)), status=201)

        return run_idempotent(self, request, produce)


class TimetableDetailView(_AcademicsView):
    def patch(self, request, slot_id):
        def produce():
            slot = _writable_slot(request, slot_id)
            enforce_unmatched_since(request, slot, timetable.slot_payload(slot))
            return Response(timetable.slot_payload(timetable.update_slot(slot, request.data)))

        return run_idempotent(self, request, produce)

    def delete(self, request, slot_id):
        def produce():
            timetable.delete_slot(_writable_slot(request, slot_id))
            return Response(status=204)

        return run_idempotent(self, request, produce)


class UncoveredTimetableView(_AcademicsView):
    write_roles = frozenset()

    def get(self, request):
        on_date = _query_date(request.query_params.get("date"))
        rows, count = timetable.uncovered_slots(request.memberships, on_date)
        return Response({"date": on_date.isoformat(), "count": count, "results": rows})


class AbsenceView(_AcademicsView):
    def post(self, request):
        def produce():
            return Response(
                timetable.upsert_absence(request.data, request.memberships),
                status=201,
            )

        return run_idempotent(self, request, produce)

    def delete(self, request):
        def produce():
            return Response(
                timetable.delete_absence(
                    memberships=request.memberships,
                    absence_id=request.query_params.get("id"),
                    teacher_id=request.query_params.get("teacher"),
                    on_date=request.query_params.get("date"),
                )
            )

        return run_idempotent(self, request, produce)


class SubstitutionListView(_AcademicsView):
    def get(self, request):
        on_date = request.query_params.get("date")
        parsed = _query_date(on_date) if on_date else None
        slot_id = request.query_params.get("slot") or None
        return Response(
            timetable.list_substitutions(
                request.memberships,
                on_date=parsed,
                slot_id=slot_id,
            )
        )

    def post(self, request):
        def produce():
            return Response(
                timetable.create_substitution(request.data, request.memberships),
                status=201,
            )

        return run_idempotent(self, request, produce)


def _writable_classroom(request, classroom_id):
    if not {row.role for row in request.memberships}.intersection(WRITE_ROLES):
        raise PermissionDenied()
    classroom = (
        Classroom.objects.select_related(
            "campus",
            "academic_year",
            "grade_level__division",
            "homeroom_teacher",
        )
        .filter(pk=classroom_id)
        .first()
    )
    if classroom is None:
        raise NotFound()
    return classroom


def _writable_slot(request, slot_id):
    if not {row.role for row in request.memberships}.intersection(WRITE_ROLES):
        raise PermissionDenied()
    slot = (
        TimetableSlot.objects.select_related(
            "classroom__campus",
            "classroom__grade_level",
            "subject__division",
            "teacher",
        )
        .filter(pk=slot_id)
        .first()
    )
    if slot is None:
        raise NotFound()
    return slot


def _query_date(raw):
    if raw in (None, ""):
        return monrovia_today()
    parsed = parse_date(str(raw))
    if parsed is None:
        from rest_framework.exceptions import ValidationError

        raise ValidationError({"date": ["Enter a valid date."]})
    return parsed
