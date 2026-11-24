from django.urls import path

from core.service.auth import DOOR_DEMO
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
