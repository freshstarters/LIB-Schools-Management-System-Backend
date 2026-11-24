from django.urls import path

from core.service.auth import DOOR_DEMO
from demo.views.classes import (
    AbsenceView,
    ClassroomDetailView,
    ClassroomListView,
    ClassroomSubjectsView,
    SubjectListView,
    SubstitutionListView,
    TeacherOptionListView,
    TimetableDetailView,
    TimetableListView,
    UncoveredTimetableView,
)
from demo.views.settings import (
    AcademicYearDetailView,
    AcademicYearListView,
    CampusDetailView,
    CampusListView,
    GradeLevelListView,
    IntegrationStatusView,
    SettingsView,
)
from core.views.auth import (
    ConfigView,
    GoogleView,
    LoginView,
    LogoutView,
    MeView,
    RefreshView,
    SessionDetailView,
    SessionsView,
)
from core.views.onboarding import (
    InviteAcceptView,
    InviteCreateView,
    InviteRevokeView,
    PasswordResetConfirmView,
    PasswordResetRequestView,
)

urlpatterns = [
    path("settings/", SettingsView.as_view(), name="demo-settings"),
    path("campuses/", CampusListView.as_view(), name="demo-campuses"),
    path("campuses/<uuid:campus_id>/", CampusDetailView.as_view(), name="demo-campus-detail"),
    path("academic-years/", AcademicYearListView.as_view(), name="demo-academic-years"),
    path(
        "academic-years/<uuid:year_id>/",
        AcademicYearDetailView.as_view(),
        name="demo-academic-year-detail",
    ),
    path("grade-levels/", GradeLevelListView.as_view(), name="demo-grade-levels"),
    path("integrations/status/", IntegrationStatusView.as_view(), name="demo-integration-status"),
    path("subjects/", SubjectListView.as_view(), name="demo-subjects"),
    path("classes/", ClassroomListView.as_view(), name="demo-classes"),
    path("classes/teachers/", TeacherOptionListView.as_view(), name="demo-class-teachers"),
    path("classes/<uuid:classroom_id>/", ClassroomDetailView.as_view(), name="demo-class-detail"),
    path(
        "classes/<uuid:classroom_id>/subjects/",
        ClassroomSubjectsView.as_view(),
        name="demo-class-subjects",
    ),
    path("timetable/", TimetableListView.as_view(), name="demo-timetable"),
    path("timetable/uncovered/", UncoveredTimetableView.as_view(), name="demo-timetable-uncovered"),
    path("timetable/absences/", AbsenceView.as_view(), name="demo-timetable-absences"),
    path(
        "timetable/substitutions/",
        SubstitutionListView.as_view(),
        name="demo-timetable-substitutions",
    ),
    path("timetable/<uuid:slot_id>/", TimetableDetailView.as_view(), name="demo-timetable-detail"),
    path("auth/config/", ConfigView.as_view(), name="demo-auth-config"),
    path("auth/sessions/", SessionsView.as_view(door=DOOR_DEMO), name="demo-auth-sessions"),
    path(
        "auth/sessions/<uuid:session_id>/",
        SessionDetailView.as_view(door=DOOR_DEMO),
        name="demo-auth-session-detail",
    ),
    path("auth/login/", LoginView.as_view(door=DOOR_DEMO), name="demo-auth-login"),
    path("auth/google/", GoogleView.as_view(door=DOOR_DEMO), name="demo-auth-google"),
    path("auth/refresh/", RefreshView.as_view(door=DOOR_DEMO), name="demo-auth-refresh"),
    path("auth/logout/", LogoutView.as_view(door=DOOR_DEMO), name="demo-auth-logout"),
    path("auth/me/", MeView.as_view(door=DOOR_DEMO), name="demo-auth-me"),
    path("auth/invites/", InviteCreateView.as_view(door=DOOR_DEMO), name="demo-auth-invites"),
    path(
        "auth/invites/accept/",
        InviteAcceptView.as_view(door=DOOR_DEMO),
        name="demo-auth-invite-accept",
    ),
    path(
        "auth/invites/<uuid:invite_id>/revoke/",
        InviteRevokeView.as_view(door=DOOR_DEMO),
        name="demo-auth-invite-revoke",
    ),
    path(
        "auth/password-reset/request/",
        PasswordResetRequestView.as_view(door=DOOR_DEMO),
        name="demo-auth-password-reset-request",
    ),
    path(
        "auth/password-reset/confirm/",
        PasswordResetConfirmView.as_view(door=DOOR_DEMO),
        name="demo-auth-password-reset-confirm",
    ),
]
