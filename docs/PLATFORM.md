# Platform scaffold

S1.1 wires PostgreSQL, Redis, and Celery, and leaves the multi-tenancy engine for S1.2. `core` does not import `demo`. Requests for Gojo School will live under `/api/demo/v1/`. The general SMS will live under `/api/v1/`.

## Environment

`lib_school_management_system/settings.py` reads `.env` from the backend root. `ENV_FILE` selects a different file. Variables already set in the process environment win.

`DATABASE_URL` is required. The scheme must be `postgres` or `postgresql`. Any other scheme raises `ImproperlyConfigured` and the process does not start.

`TENANCY_PG_ADMIN_URL` is optional at startup. When it is set, it must use the same PostgreSQL schemes. `/api/ready/` does not connect to it. Later per-tenant provisioning uses it to create databases.

Unset `TENANCY_DB_MODE` means `shared`. Unset `TENANCY_ROUTING_MODE` means `header_path` in every environment. Production that wants subdomain routing sets `TENANCY_ROUTING_MODE=subdomain` and `TENANCY_BASE_DOMAIN`. An empty string or any other value raises `ImproperlyConfigured`. Subdomain mode without `TENANCY_BASE_DOMAIN` also raises `ImproperlyConfigured`.

`TENANCY_CREDENTIAL_KEY` is a Fernet key. Shared mode may leave it empty. Per-tenant mode requires it. A non-empty value that is not a Fernet key raises `ImproperlyConfigured` in every mode. Generate one with:

```bash
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

The admin URL's role needs `CREATEDB` and `CREATEROLE`. Local `DATABASE_URL` and `TENANCY_PG_ADMIN_URL` both use the PostgreSQL 16 server on `127.0.0.1:5433`, because the host login `demiz` can create databases and cannot create roles, and it cannot create tables in the existing `lib_sms` database. Per-tenant provisioning creates `sms_tenant_<slug>` owned by a login role `sms_<slug>` that cannot create databases. Only the password is encrypted. The role name, host, port, and database name are stored in plaintext on `TenantSchool`. `python manage.py migrate_all_tenants` migrates active tenant databases and records each result. It raises `CommandError` in shared mode. One tenant's failure does not stop the others.

These checks live in `core/helpers/environment.py` and `core/tenancy/config.py`.

## Tenancy requests

`/api/demo/v1/`, `/api/health/`, `/api/ready/`, `/api/schema/`, and `/admin/` skip tenant resolution. `/api/v1/auth/` also skips it until login arrives. A `core` bearer presented to `/api/demo/v1/` returns `403` with `app_scope_rejected`.

School routes under `/api/v1/` require a verified HS256 bearer (`app_scope` and `exp`, signed with `SECRET_KEY`). `header_path` also requires `X-Tenant-Slug`. `subdomain` reads `<slug>.<TENANCY_BASE_DOMAIN>`. Reserved labels are `www`, `api`, `admin`, and `platform`.

| Situation | Status | `code` |
|---|---|---|
| Missing `X-Tenant-Slug` in header mode | 400 | `tenant_header_required` |
| Unknown slug | 404 | `tenant_not_found` |
| `PROVISIONING` or `SUSPENDED` | 403 | `tenant_unavailable` |
| Missing or invalid bearer | 401 | `authentication_failed` |
| Wrong `app_scope` | 403 | `app_scope_rejected` |
| Bearer `tenant_slug` does not match | 403 | `tenant_mismatch` |

`/api/v1/platform/` is platform context in both routing modes. It takes a `core` bearer whose `tenant_slug` is empty. In subdomain mode the host must be a reserved label. A school host or a school slug header is rejected.

Shared mode stores every school in the default database and filters tenant queries by the active school. Per-tenant mode sends those queries to that school's database. A query with no tenant raises `TenantContextError`.

Unset `SECRET_KEY` raises `ImproperlyConfigured`. Unset `DEBUG` means false. Unset `CORS_ALLOWED_ORIGINS` allows no cross-origin browser calls.

Copy `.env.example` when provisioning a new environment. Leave integration secrets empty until the story that calls that provider. Those later stories fail closed on their own missing keys.

## Redis and Celery

`CELERY_BROKER_URL` is the broker. When it is blank, the broker is `REDIS_URL`. Django still starts when both are blank or Redis is down.

Publishing a task with neither URL set raises `ImproperlyConfigured` from `PlatformCelery.send_task` before Celery opens a socket. `before_task_publish` raises the same error for other publish paths. Neither hook pings Redis. A URL that Redis refuses surfaces Celery's own connection error.

`CELERY_RESULT_BACKEND` is set only when that variable is present.

`/api/ready/` pings Redis at the resolved broker URL:

| Check value | Meaning |
|---|---|
| `ok` | `SELECT 1` or `PING` succeeded |
| `unconfigured` | The URL is missing |
| `unavailable` | The URL is set and the ping failed |

A running process cannot report PostgreSQL as `unconfigured`, because a missing `DATABASE_URL` never finishes startup.

## HTTP

`GET /api/health/` returns `200` and `{"status": "ok"}`. It does not touch PostgreSQL or Redis.

`GET /api/ready/` returns `200` only when both checks are `ok`:

```json
{"status": "ok", "checks": {"postgresql": "ok", "redis": "ok"}}
```

Any other check value returns `503` and `"status": "degraded"`.

`GET /api/schema/` serves the OpenAPI document. It is public until authentication arrives in S1.3. This story does not mount Swagger or ReDoc.

DRF errors use this envelope. `fields` is always an object. Non-field validation messages sit under `non_field_errors`.

```json
{"code": "validation_error", "detail": "Invalid input.", "fields": {"email": ["This field is required."]}}
```

`code` is one of `validation_error`, `parse_error`, `not_found`, `permission_denied`, `authentication_failed`, `method_not_allowed`, `throttled`, or `error`.

List endpoints page 50 rows at a time. Clients may pass `page_size` up to 100.

## Local checks

```bash
python manage.py check
python manage.py test
```

Both run against PostgreSQL. `python manage.py test` creates `test_lib_sms`, which requires `CREATEDB` on the database role.
