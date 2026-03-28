from __future__ import annotations

import os
import sys
from pathlib import Path
from urllib.parse import parse_qsl, urlparse

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")


def env(name: str, default: str | None = None) -> str | None:
    return os.getenv(name, default)


def env_bool(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def env_list(name: str, default: str = "") -> list[str]:
    value = os.getenv(name, default)
    return [item.strip() for item in value.split(",") if item.strip()]


def database_url_options(query: str) -> dict[str, str]:
    supported_keys = {
        "sslmode",
        "sslrootcert",
        "sslcert",
        "sslkey",
        "application_name",
        "options",
        "connect_timeout",
        "target_session_attrs",
        "passfile",
        "keepalives",
        "keepalives_idle",
        "keepalives_interval",
        "keepalives_count",
        "channel_binding",
        "gssencmode",
    }
    return {
        key: value
        for key, value in parse_qsl(query, keep_blank_values=True)
        if key in supported_keys and value
    }


def database_config() -> dict:
    if env_bool("DJANGO_USE_SQLITE", default=False) or (
        "test" in sys.argv and not env_bool("DJANGO_TEST_USE_POSTGRES", default=False)
    ):
        return {
            "ENGINE": "django.db.backends.sqlite3",
            "NAME": BASE_DIR / "db.sqlite3",
        }

    database_url = env("DATABASE_URL")
    if database_url:
        parsed = urlparse(database_url)
        engine = {
            "postgres": "django.db.backends.postgresql",
            "postgresql": "django.db.backends.postgresql",
            "sqlite": "django.db.backends.sqlite3",
        }.get(parsed.scheme, "django.db.backends.sqlite3")
        if engine == "django.db.backends.sqlite3":
            db_path = parsed.path.lstrip("/") or "db.sqlite3"
            return {"ENGINE": engine, "NAME": BASE_DIR / db_path}
        config = {
            "ENGINE": engine,
            "NAME": parsed.path.lstrip("/"),
            "USER": parsed.username or "",
            "PASSWORD": parsed.password or "",
            "HOST": parsed.hostname or "",
            "PORT": parsed.port or "",
        }
        options = database_url_options(parsed.query)
        if options:
            config["OPTIONS"] = options
        return config

    if env("POSTGRES_DB"):
        return {
            "ENGINE": "django.db.backends.postgresql",
            "NAME": env("POSTGRES_DB", "autodocker"),
            "USER": env("POSTGRES_USER", "autodocker"),
            "PASSWORD": env("POSTGRES_PASSWORD", "autodocker"),
            "HOST": env("POSTGRES_HOST", "127.0.0.1"),
            "PORT": env("POSTGRES_PORT", "5432"),
        }

    return {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": BASE_DIR / "db.sqlite3",
    }


def media_storage_config() -> dict:
    bucket_name = env("SUPABASE_STORAGE_BUCKET")
    endpoint_url = env("SUPABASE_STORAGE_S3_ENDPOINT_URL")
    access_key = env("SUPABASE_STORAGE_ACCESS_KEY_ID")
    secret_key = env("SUPABASE_STORAGE_SECRET_ACCESS_KEY")
    region_name = env("SUPABASE_STORAGE_S3_REGION")
    media_prefix = (env("SUPABASE_STORAGE_MEDIA_PATH_PREFIX", "") or "").strip("/")

    if not all([bucket_name, endpoint_url, access_key, secret_key, region_name]):
        return {"BACKEND": "django.core.files.storage.FileSystemStorage"}

    options = {
        "bucket_name": bucket_name,
        "endpoint_url": endpoint_url,
        "access_key": access_key,
        "secret_key": secret_key,
        "region_name": region_name,
        "default_acl": None,
        "querystring_auth": True,
        "file_overwrite": False,
        "signature_version": "s3v4",
        "addressing_style": "path",
    }
    if media_prefix:
        options["location"] = media_prefix

    return {
        "BACKEND": "storages.backends.s3.S3Storage",
        "OPTIONS": options,
    }


def staticfiles_storage_config() -> dict:
    return {
        "BACKEND": (
            "whitenoise.storage.CompressedManifestStaticFilesStorage"
            if not DEBUG
            else "django.contrib.staticfiles.storage.StaticFilesStorage"
        )
    }


SECRET_KEY = env("DJANGO_SECRET_KEY", "dev-only-secret-key")
DEBUG = env_bool("DJANGO_DEBUG", default=True)
ALLOWED_HOSTS = env_list("DJANGO_ALLOWED_HOSTS", "127.0.0.1,localhost")
CSRF_TRUSTED_ORIGINS = env_list("DJANGO_CSRF_TRUSTED_ORIGINS", "http://127.0.0.1:8000,http://localhost:8000")
RENDER_EXTERNAL_HOSTNAME = env("RENDER_EXTERNAL_HOSTNAME")
if RENDER_EXTERNAL_HOSTNAME and RENDER_EXTERNAL_HOSTNAME not in ALLOWED_HOSTS:
    ALLOWED_HOSTS.append(RENDER_EXTERNAL_HOSTNAME)
    render_origin = f"https://{RENDER_EXTERNAL_HOSTNAME}"
    if render_origin not in CSRF_TRUSTED_ORIGINS:
        CSRF_TRUSTED_ORIGINS.append(render_origin)

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "rest_framework",
    "core",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "config.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    }
]

WSGI_APPLICATION = "config.wsgi.application"
ASGI_APPLICATION = "config.asgi.application"

DATABASES = {"default": database_config()}

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

LANGUAGE_CODE = "es-ar"
TIME_ZONE = "America/Argentina/Buenos_Aires"
USE_I18N = True
USE_TZ = True

STATIC_URL = "/static/"
STATIC_ROOT = BASE_DIR / "staticfiles"

MEDIA_URL = "/media/"
MEDIA_ROOT = BASE_DIR / "media"
STORAGES = {
    "default": media_storage_config(),
    "staticfiles": staticfiles_storage_config(),
}

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

DATA_UPLOAD_MAX_MEMORY_SIZE = 50 * 1024 * 1024
FILE_UPLOAD_MAX_MEMORY_SIZE = 25 * 1024 * 1024

REST_FRAMEWORK = {
    "DEFAULT_RENDERER_CLASSES": [
        "rest_framework.renderers.JSONRenderer",
    ],
    "DEFAULT_AUTHENTICATION_CLASSES": [
        "rest_framework.authentication.SessionAuthentication",
    ],
    "DEFAULT_PERMISSION_CLASSES": [
        "rest_framework.permissions.IsAuthenticated",
    ],
    "DEFAULT_PARSER_CLASSES": [
        "rest_framework.parsers.JSONParser",
        "rest_framework.parsers.FormParser",
        "rest_framework.parsers.MultiPartParser",
    ],
}

LOGIN_URL = "login"
LOGIN_REDIRECT_URL = "core:dashboard"
LOGOUT_REDIRECT_URL = "login"
AUTODOCKER_APP_BASE_URL = env(
    "AUTODOCKER_APP_BASE_URL",
    env("RENDER_EXTERNAL_URL", "http://127.0.0.1:8000"),
)
EMAIL_BACKEND = env(
    "DJANGO_EMAIL_BACKEND",
    "django.core.mail.backends.console.EmailBackend" if DEBUG else "django.core.mail.backends.smtp.EmailBackend",
)
DEFAULT_FROM_EMAIL = env("DJANGO_DEFAULT_FROM_EMAIL", "autodocker@localhost")

SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
SECURE_SSL_REDIRECT = env_bool("DJANGO_SECURE_SSL_REDIRECT", default=not DEBUG)
CSRF_COOKIE_SECURE = env_bool("DJANGO_CSRF_COOKIE_SECURE", default=not DEBUG)
SESSION_COOKIE_SECURE = env_bool("DJANGO_SESSION_COOKIE_SECURE", default=not DEBUG)
SECURE_HSTS_SECONDS = int(env("DJANGO_SECURE_HSTS_SECONDS", "0" if DEBUG else "3600"))
SECURE_HSTS_INCLUDE_SUBDOMAINS = env_bool("DJANGO_SECURE_HSTS_INCLUDE_SUBDOMAINS", default=not DEBUG)
SECURE_HSTS_PRELOAD = env_bool("DJANGO_SECURE_HSTS_PRELOAD", default=False)
SECURE_REFERRER_POLICY = "same-origin"
X_FRAME_OPTIONS = "DENY"

AUTODOCKER_ASYNC_MODE = env(
    "AUTODOCKER_ASYNC_MODE",
    "thread" if DEBUG else "celery",
)
AUTODOCKER_ENABLE_RUNTIME_JOBS = env_bool(
    "AUTODOCKER_ENABLE_RUNTIME_JOBS",
    default=DEBUG,
)
AUTODOCKER_TOKEN_ENCRYPTION_KEY = env(
    "AUTODOCKER_TOKEN_ENCRYPTION_KEY",
    SECRET_KEY,
)
AUTODOCKER_TOKEN_ENCRYPTION_FALLBACK_KEYS = env_list(
    "AUTODOCKER_TOKEN_ENCRYPTION_FALLBACK_KEYS",
    "",
)
CELERY_BROKER_URL = env("CELERY_BROKER_URL", "redis://redis:6379/0")
CELERY_RESULT_BACKEND = env("CELERY_RESULT_BACKEND", CELERY_BROKER_URL)
CELERY_TASK_ALWAYS_EAGER = env_bool("CELERY_TASK_ALWAYS_EAGER", default=False)
CELERY_TASK_TIME_LIMIT = int(env("CELERY_TASK_TIME_LIMIT", "180"))
CELERY_TASK_SOFT_TIME_LIMIT = int(env("CELERY_TASK_SOFT_TIME_LIMIT", "150"))

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "handlers": {
        "console": {"class": "logging.StreamHandler"},
    },
    "root": {
        "handlers": ["console"],
        "level": env("DJANGO_LOG_LEVEL", "INFO"),
    },
}
