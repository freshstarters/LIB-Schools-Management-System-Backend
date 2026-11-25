"""School settings, campuses, the calendar, and integration health."""

from django.conf import settings
from django.middleware.csrf import get_token
from rest_framework.response import Response
from rest_framework.views import APIView

from core.helpers.idempotency import enforce_unmatched_since, run_idempotent
from core.helpers.permissions import BURSAR, PRINCIPAL, REGISTRAR, HasSchoolRole
from core.service.auth import public_config, user_from_bearer
from demo.models import AcademicYear, Campus, GradeLevel, SchoolConfig
from demo.service.school_service import (
    campus_counts,
    campus_payload,
    config_payload,
    create_campus,
    create_year,
    delete_campus,
    delete_year,
    ensure_school_profile,
    grade_payload,
    update_campus,
    update_settings,
    update_year,
    year_payload,
)

READ_ROLES = frozenset({PRINCIPAL, REGISTRAR, BURSAR})
PRINCIPAL_ONLY = frozenset({PRINCIPAL})
SETTINGS_WRITE = frozenset({PRINCIPAL, BURSAR})


class _SchoolView(APIView):
    authentication_classes = []
    permission_classes = [HasSchoolRole]
    app_scope = "demo"
    read_roles = READ_ROLES

    def idempotency_user(self, request):
        return user_from_bearer(request, "demo")


def _config():
    ensure_school_profile()
    return SchoolConfig.objects.select_related(
        "academic_year",
        "active_marking_period",
    ).get(singleton=True)


class DemoConfigView(APIView):
    """Public auth config plus the Gojo school name for the shell and sign-in card."""

    authentication_classes = []
    permission_classes = []

    def get(self, request):
        django_request = getattr(request, "_request", request)
        body = public_config(get_token(django_request))
        ensure_school_profile()
        body["school_name"] = SchoolConfig.objects.get(singleton=True).school_name
        return Response(body)


class SettingsView(_SchoolView):
    write_roles = SETTINGS_WRITE

    def get(self, request):
        return Response(config_payload(_config()))

    def patch(self, request):
        def produce():
            config = _config()
            enforce_unmatched_since(request, config, config_payload(config))
            update_settings(request.memberships, request.data, config)
            config.refresh_from_db()
            return Response(config_payload(config))

        return run_idempotent(self, request, produce)


class CampusListView(_SchoolView):
    write_roles = PRINCIPAL_ONLY

    def get(self, request):
        return Response(_campus_rows())

    def post(self, request):
        def produce():
            campus = create_campus(request.data)
            return Response(_one_campus(campus), status=201)

        return run_idempotent(self, request, produce)


class CampusDetailView(_SchoolView):
    write_roles = PRINCIPAL_ONLY

    def get(self, request, campus_id):
        return Response(_one_campus(_campus(campus_id)))

    def patch(self, request, campus_id):
        def produce():
            campus = _campus(campus_id)
            enforce_unmatched_since(request, campus, _one_campus(campus))
            update_campus(campus, request.data)
            return Response(_one_campus(campus))

        return run_idempotent(self, request, produce)

    def delete(self, request, campus_id):
        def produce():
            delete_campus(_campus(campus_id))
            return Response(status=204)

        return run_idempotent(self, request, produce)


class AcademicYearListView(_SchoolView):
    write_roles = PRINCIPAL_ONLY

    def get(self, request):
        return Response(_year_rows())

    def post(self, request):
        def produce():
            year = create_year(request.data)
            return Response(_one_year(year), status=201)

        return run_idempotent(self, request, produce)


class AcademicYearDetailView(_SchoolView):
    write_roles = PRINCIPAL_ONLY

    def get(self, request, year_id):
        return Response(_one_year(_year(year_id)))

    def patch(self, request, year_id):
        def produce():
            year = _year(year_id)
            enforce_unmatched_since(request, year, _one_year(year))
            update_year(year, request.data)
            return Response(_one_year(year))

        return run_idempotent(self, request, produce)

    def delete(self, request, year_id):
        def produce():
            delete_year(_year(year_id))
            return Response(status=204)

        return run_idempotent(self, request, produce)


class GradeLevelListView(_SchoolView):
    write_roles = PRINCIPAL_ONLY

    def get(self, request):
        grades = GradeLevel.objects.select_related("division")
        return Response([grade_payload(grade) for grade in grades])


class IntegrationStatusView(_SchoolView):
    write_roles = frozenset()

    def get(self, request):
        return Response(integration_status())


def integration_status():
    sender = _text("SMS_SENDER_ID")
    provider = _text("AI_PROVIDER")
    return {
        "email_smtp": {"status": _flag(bool(getattr(settings, "EMAIL_CONFIGURED", False)))},
        "sms": {
            "status": _flag(
                _text("SMS_PROVIDER")
                and _text("SMS_API_KEY")
                and (_text("SMS_USERNAME") or _text("SMS_ACCOUNT_SID"))
                and sender
            ),
            "sender_id": sender or None,
        },
        "whatsapp": {
            "status": _flag(
                _text("WHATSAPP_PHONE_NUMBER_ID")
                and _text("WHATSAPP_ACCESS_TOKEN")
                and _text("WHATSAPP_VERIFY_TOKEN")
            )
        },
        "google": {
            "status": _flag(
                _text("GOOGLE_OAUTH_CLIENT_ID") and _text("GOOGLE_OAUTH_CLIENT_SECRET")
            )
        },
        "ai": {
            "status": _flag(provider and _text("AI_API_KEY") and _text("AI_MODEL")),
            "provider": provider or None,
        },
        "mobile_money": [
            {
                "provider": "mtn_momo",
                "status": _flag(
                    _text("MTN_MOMO_ENV")
                    and _text("MTN_MOMO_SUBSCRIPTION_KEY")
                    and _text("MTN_MOMO_API_USER")
                    and _text("MTN_MOMO_API_KEY")
                    and _text("MTN_MOMO_WEBHOOK_SECRET")
                ),
            },
            {
                "provider": "orange_money",
                "status": _flag(
                    _text("ORANGE_MONEY_ENV")
                    and _text("ORANGE_MONEY_CLIENT_ID")
                    and _text("ORANGE_MONEY_CLIENT_SECRET")
                    and _text("ORANGE_MONEY_MERCHANT_KEY")
                    and _text("ORANGE_MONEY_WEBHOOK_SECRET")
                ),
            },
        ],
    }


def _text(name):
    return str(getattr(settings, name, "") or "").strip()


def _flag(ready):
    return "configured" if ready else "unconfigured"


def _campus_rows():
    counts = campus_counts()
    campuses = Campus.objects.prefetch_related("grades_served__division")
    return [campus_payload(campus, list(campus.grades_served.all()), counts) for campus in campuses]


def _one_campus(campus):
    grades = list(campus.grades_served.select_related("division"))
    return campus_payload(campus, grades, campus_counts())


def _campus(campus_id):
    campus = Campus.objects.filter(pk=campus_id).first()
    if campus is None:
        from rest_framework.exceptions import NotFound

        raise NotFound()
    return campus


def _year_rows():
    years = AcademicYear.objects.prefetch_related("marking_periods")
    return [year_payload(year, list(year.marking_periods.all())) for year in years]


def _one_year(year):
    return year_payload(year, list(year.marking_periods.all()))


def _year(year_id):
    year = AcademicYear.objects.filter(pk=year_id).first()
    if year is None:
        from rest_framework.exceptions import NotFound

        raise NotFound()
    return year
