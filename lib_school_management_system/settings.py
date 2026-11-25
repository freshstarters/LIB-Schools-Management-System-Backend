"""Django settings for the Liberian School Management System."""

import os
from pathlib import Path

from core.helpers.environment import env, postgres_database
from core.tenancy.config import load_tenancy

BASE_DIR = Path(__file__).resolve().parent.parent

_env_file = os.environ.get("ENV_FILE", BASE_DIR / ".env")
if Path(_env_file).is_file():
    env.read_env(str(_env_file), overwrite=False)

SECRET_KEY = env("SECRET_KEY")
DEBUG = env.bool("DEBUG", default=False)
ALLOWED_HOSTS = env.list("ALLOWED_HOSTS", default=[])

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "corsheaders",
    "rest_framework",
    "drf_spectacular",
    "core.apps.CoreConfig",
    "demo.apps.DemoConfig",
]

MIDDLEWARE = [
    "core.helpers.errors.ApiNotFoundMiddleware",
    "django.middleware.security.SecurityMiddleware",
    "corsheaders.middleware.CorsMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "core.tenancy.middleware.TenantResolutionMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "lib_school_management_system.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

WSGI_APPLICATION = "lib_school_management_system.wsgi.application"

DATABASES = {
    "default": postgres_database(
        os.environ.get("DATABASE_URL"),
        label="DATABASE_URL",
        required=True,
    )
}

_admin_url = os.environ.get("TENANCY_PG_ADMIN_URL", "")
if str(_admin_url).strip():
    postgres_database(
        _admin_url,
        label="TENANCY_PG_ADMIN_URL",
        required=True,
    )
    TENANCY_PG_ADMIN_URL = str(_admin_url).strip()
else:
    TENANCY_PG_ADMIN_URL = ""

(
    TENANCY_DB_MODE,
    TENANCY_ROUTING_MODE,
    TENANCY_BASE_DOMAIN,
    TENANCY_CREDENTIAL_KEY,
) = load_tenancy(os.environ, debug=DEBUG)

if TENANCY_ROUTING_MODE == "subdomain" and TENANCY_BASE_DOMAIN:
    _wildcard = f".{TENANCY_BASE_DOMAIN}"
    if _wildcard not in ALLOWED_HOSTS:
        ALLOWED_HOSTS.append(_wildcard)

DATABASE_ROUTERS = ["core.tenancy.router.TenantDatabaseRouter"]

REDIS_URL = os.environ.get("REDIS_URL", "").strip()
CELERY_BROKER_URL = os.environ.get("CELERY_BROKER_URL", "").strip() or REDIS_URL
_result_backend = os.environ.get("CELERY_RESULT_BACKEND", "").strip()
if _result_backend:
    CELERY_RESULT_BACKEND = _result_backend

AUTH_USER_MODEL = "core.User"

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

LANGUAGE_CODE = "en-us"
TIME_ZONE = "Africa/Monrovia"
USE_I18N = True
USE_TZ = True

STATIC_URL = "static/"
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

EMAIL_HOST = os.environ.get("EMAIL_HOST", "").strip()
_email_port = os.environ.get("EMAIL_PORT", "").strip()
EMAIL_PORT = int(_email_port) if _email_port.isdigit() else 25
EMAIL_HOST_USER = os.environ.get("EMAIL_HOST_USER", "").strip()
EMAIL_HOST_PASSWORD = os.environ.get("EMAIL_HOST_PASSWORD", "").strip()
DEFAULT_FROM_EMAIL = os.environ.get("DEFAULT_FROM_EMAIL", "").strip()
EMAIL_USE_SSL = EMAIL_PORT == 465
EMAIL_USE_TLS = EMAIL_PORT == 587
EMAIL_CONFIGURED = bool(
    EMAIL_HOST
    and _email_port
    and EMAIL_HOST_USER
    and EMAIL_HOST_PASSWORD
    and DEFAULT_FROM_EMAIL
)
if DEBUG:
    EMAIL_BACKEND = "django.core.mail.backends.console.EmailBackend"
elif EMAIL_CONFIGURED:
    EMAIL_BACKEND = "django.core.mail.backends.smtp.EmailBackend"
else:
    EMAIL_BACKEND = "django.core.mail.backends.dummy.EmailBackend"

CORS_ALLOWED_ORIGINS = env.list("CORS_ALLOWED_ORIGINS", default=[])
CORS_ALLOW_ALL_ORIGINS = False
CORS_ALLOW_CREDENTIALS = True
CSRF_TRUSTED_ORIGINS = list(CORS_ALLOWED_ORIGINS)
CSRF_FAILURE_VIEW = "core.helpers.errors.csrf_failure"
CSRF_COOKIE_SECURE = not DEBUG

HOSTING_REGION = os.environ.get("HOSTING_REGION", "").strip()
DATA_CONTROLLER_NAME = os.environ.get("DATA_CONTROLLER_NAME", "").strip()
GOJO_APP_ORIGIN = os.environ.get("GOJO_APP_ORIGIN", "").strip().rstrip("/")
GOOGLE_OAUTH_CLIENT_ID = os.environ.get("GOOGLE_OAUTH_CLIENT_ID", "").strip()
GOOGLE_OAUTH_CLIENT_SECRET = os.environ.get("GOOGLE_OAUTH_CLIENT_SECRET", "").strip()

SMS_PROVIDER = os.environ.get("SMS_PROVIDER", "").strip()
SMS_API_KEY = os.environ.get("SMS_API_KEY", "").strip()
SMS_USERNAME = os.environ.get("SMS_USERNAME", "").strip()
SMS_ACCOUNT_SID = os.environ.get("SMS_ACCOUNT_SID", "").strip()
SMS_SENDER_ID = os.environ.get("SMS_SENDER_ID", "").strip()

WHATSAPP_PHONE_NUMBER_ID = os.environ.get("WHATSAPP_PHONE_NUMBER_ID", "").strip()
WHATSAPP_ACCESS_TOKEN = os.environ.get("WHATSAPP_ACCESS_TOKEN", "").strip()
WHATSAPP_VERIFY_TOKEN = os.environ.get("WHATSAPP_VERIFY_TOKEN", "").strip()

AI_PROVIDER = os.environ.get("AI_PROVIDER", "").strip()
AI_API_KEY = os.environ.get("AI_API_KEY", "").strip()
AI_MODEL = os.environ.get("AI_MODEL", "").strip()

MTN_MOMO_ENV = os.environ.get("MTN_MOMO_ENV", "").strip()
MTN_MOMO_SUBSCRIPTION_KEY = os.environ.get("MTN_MOMO_SUBSCRIPTION_KEY", "").strip()
MTN_MOMO_API_USER = os.environ.get("MTN_MOMO_API_USER", "").strip()
MTN_MOMO_API_KEY = os.environ.get("MTN_MOMO_API_KEY", "").strip()
MTN_MOMO_WEBHOOK_SECRET = os.environ.get("MTN_MOMO_WEBHOOK_SECRET", "").strip()

ORANGE_MONEY_ENV = os.environ.get("ORANGE_MONEY_ENV", "").strip()
ORANGE_MONEY_CLIENT_ID = os.environ.get("ORANGE_MONEY_CLIENT_ID", "").strip()
ORANGE_MONEY_CLIENT_SECRET = os.environ.get("ORANGE_MONEY_CLIENT_SECRET", "").strip()
ORANGE_MONEY_MERCHANT_KEY = os.environ.get("ORANGE_MONEY_MERCHANT_KEY", "").strip()
ORANGE_MONEY_WEBHOOK_SECRET = os.environ.get("ORANGE_MONEY_WEBHOOK_SECRET", "").strip()

STORAGE_BACKEND = os.environ.get("STORAGE_BACKEND", "").strip().lower()
AWS_ACCESS_KEY_ID = os.environ.get("AWS_ACCESS_KEY_ID", "").strip()
AWS_SECRET_ACCESS_KEY = os.environ.get("AWS_SECRET_ACCESS_KEY", "").strip()
AWS_STORAGE_BUCKET_NAME = os.environ.get("AWS_STORAGE_BUCKET_NAME", "").strip()
AWS_S3_ENDPOINT_URL = os.environ.get("AWS_S3_ENDPOINT_URL", "").strip()
MEDIA_URL = "/media/"
MEDIA_ROOT = BASE_DIR / "media"

from core.integrations.storage import assert_storage_configured  # noqa: E402

_storage_backend = assert_storage_configured()
if _storage_backend == "local":
    STORAGES = {
        "default": {
            "BACKEND": "django.core.files.storage.FileSystemStorage",
            "OPTIONS": {
                "location": str(MEDIA_ROOT),
                "base_url": MEDIA_URL,
            },
        },
        "staticfiles": {
            "BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage",
        },
    }
else:
    STORAGES = {
        "default": {
            "BACKEND": "core.integrations.storage.PrivateS3Storage",
        },
        "staticfiles": {
            "BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage",
        },
    }


if REDIS_URL:
    CACHES = {
        "default": {
            "BACKEND": "django.core.cache.backends.redis.RedisCache",
            "LOCATION": REDIS_URL,
        }
    }
else:
    CACHES = {
        "default": {
            "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
            "LOCATION": "sms",
        }
    }

REST_FRAMEWORK = {
    "DEFAULT_PAGINATION_CLASS": "core.helpers.pagination.DefaultPagination",
    "PAGE_SIZE": 50,
    "EXCEPTION_HANDLER": "core.helpers.errors.envelope_exception_handler",
    "DEFAULT_SCHEMA_CLASS": "drf_spectacular.openapi.AutoSchema",
    "DEFAULT_THROTTLE_RATES": {
        "auth_login": "5/min",
        "auth_recovery": "5/min",
    },
}

SPECTACULAR_SETTINGS = {
    "TITLE": "Liberian School Management System API",
    "DESCRIPTION": (
        "OpenAPI schema for the general SMS at /api/v1/ "
        "and Gojo School at /api/demo/v1/."
    ),
    "VERSION": "0.1.0",
}
