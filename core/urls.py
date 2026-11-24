from django.urls import path

from core.service.auth import DOOR_PLATFORM, DOOR_SCHOOL
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
from core.views.catalog import (
    DivisionDetailView,
    DivisionListView,
    SubjectDetailView,
    SubjectListView,
)
from core.views.onboarding import (
    InviteAcceptView,
    InviteCreateView,
    InviteRevokeView,
    PasswordResetConfirmView,
    PasswordResetRequestView,
)
from core.views.platform import platform_ping

urlpatterns = [
    path("auth/config/", ConfigView.as_view(), name="auth-config"),
    path("auth/sessions/", SessionsView.as_view(door=DOOR_SCHOOL), name="auth-sessions"),
    path(
        "auth/sessions/<uuid:session_id>/",
        SessionDetailView.as_view(door=DOOR_SCHOOL),
        name="auth-session-detail",
    ),
    path("auth/login/", LoginView.as_view(door=DOOR_SCHOOL), name="auth-login"),
    path("auth/google/", GoogleView.as_view(door=DOOR_SCHOOL), name="auth-google"),
    path("auth/refresh/", RefreshView.as_view(door=DOOR_SCHOOL), name="auth-refresh"),
    path("auth/logout/", LogoutView.as_view(door=DOOR_SCHOOL), name="auth-logout"),
    path("auth/me/", MeView.as_view(door=DOOR_SCHOOL), name="auth-me"),
    path("auth/invites/", InviteCreateView.as_view(door=DOOR_SCHOOL), name="auth-invites"),
    path(
        "auth/invites/accept/",
        InviteAcceptView.as_view(door=DOOR_SCHOOL),
        name="auth-invite-accept",
    ),
    path(
        "auth/invites/<uuid:invite_id>/revoke/",
        InviteRevokeView.as_view(door=DOOR_SCHOOL),
        name="auth-invite-revoke",
    ),
    path(
        "auth/password-reset/request/",
        PasswordResetRequestView.as_view(door=DOOR_SCHOOL),
        name="auth-password-reset-request",
    ),
    path(
        "auth/password-reset/confirm/",
        PasswordResetConfirmView.as_view(door=DOOR_SCHOOL),
        name="auth-password-reset-confirm",
    ),
    path(
        "auth/platform/login/",
        LoginView.as_view(door=DOOR_PLATFORM),
        name="platform-auth-login",
    ),
    path(
        "auth/platform/google/",
        GoogleView.as_view(door=DOOR_PLATFORM),
        name="platform-auth-google",
    ),
    path(
        "auth/platform/refresh/",
        RefreshView.as_view(door=DOOR_PLATFORM),
        name="platform-auth-refresh",
    ),
    path(
        "auth/platform/logout/",
        LogoutView.as_view(door=DOOR_PLATFORM),
        name="platform-auth-logout",
    ),
    path(
        "auth/platform/sessions/<uuid:session_id>/",
        SessionDetailView.as_view(door=DOOR_PLATFORM),
        name="platform-auth-session-detail",
    ),
    path(
        "auth/platform/invites/",
        InviteCreateView.as_view(door=DOOR_PLATFORM),
        name="platform-auth-invites",
    ),
    path(
        "auth/platform/invites/accept/",
        InviteAcceptView.as_view(door=DOOR_PLATFORM),
        name="platform-auth-invite-accept",
    ),
    path(
        "auth/platform/invites/<uuid:invite_id>/revoke/",
        InviteRevokeView.as_view(door=DOOR_PLATFORM),
        name="platform-auth-invite-revoke",
    ),
    path(
        "auth/platform/password-reset/request/",
        PasswordResetRequestView.as_view(door=DOOR_PLATFORM),
        name="platform-auth-password-reset-request",
    ),
    path(
        "auth/platform/password-reset/confirm/",
        PasswordResetConfirmView.as_view(door=DOOR_PLATFORM),
        name="platform-auth-password-reset-confirm",
    ),
    path("platform/ping/", platform_ping, name="platform-ping"),
    path("divisions/", DivisionListView.as_view(), name="division-list"),
    path("divisions/<uuid:pk>/", DivisionDetailView.as_view(), name="division-detail"),
    path("subjects/", SubjectListView.as_view(), name="subject-list"),
    path("subjects/<uuid:pk>/", SubjectDetailView.as_view(), name="subject-detail"),
]
