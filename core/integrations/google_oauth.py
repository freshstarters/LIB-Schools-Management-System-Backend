"""Google ID token checks. A missing client id or secret fails closed."""

from django.conf import settings

GOOGLE_ISSUERS = frozenset(
    {
        "accounts.google.com",
        "https://accounts.google.com",
    }
)


class GoogleSignInUnavailable(Exception):
    """The client id or secret is blank."""


class GoogleTokenInvalid(Exception):
    """The token is not a verified Google ID token for this client."""


def google_credentials_present():
    return bool(_client_id() and _client_secret())


def verify_id_token(token):
    """Return the verified email. Never calls Google when credentials are blank."""
    if not google_credentials_present():
        raise GoogleSignInUnavailable()
    if not isinstance(token, str) or not token.strip():
        raise GoogleTokenInvalid()

    from google.auth.transport import requests as google_requests
    from google.oauth2 import id_token

    try:
        claims = id_token.verify_oauth2_token(
            token.strip(),
            google_requests.Request(),
            _client_id(),
        )
    except Exception as exc:
        raise GoogleTokenInvalid() from exc

    verified = claims.get("email_verified")
    email = claims.get("email")
    if claims.get("iss") not in GOOGLE_ISSUERS:
        raise GoogleTokenInvalid()
    if verified is not True and verified != "true":
        raise GoogleTokenInvalid()
    if not isinstance(email, str) or "@" not in email:
        raise GoogleTokenInvalid()
    return email.strip().lower()


def _client_id():
    return getattr(settings, "GOOGLE_OAUTH_CLIENT_ID", "").strip()


def _client_secret():
    return getattr(settings, "GOOGLE_OAUTH_CLIENT_SECRET", "").strip()
