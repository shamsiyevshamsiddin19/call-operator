"""Umumiy sozlamalar — 12-Factor App (barcha muhit qiymatlari .env dan oladi).

Bu modulda hech qanday maxfiy qiymat qattiq kodlanmaydi (hardcode taqiqlanadi — Hooker 5).
"""

from pathlib import Path
from typing import Any

import environ

BASE_DIR = Path(__file__).resolve().parent.parent.parent

env = environ.Env()
environ.Env.read_env(BASE_DIR / ".env")

# ─────────────────────────────────────────────────────────────────────────────
# Asosiy
# ─────────────────────────────────────────────────────────────────────────────
SECRET_KEY: str = env("DJANGO_SECRET_KEY")
DEBUG: bool = env.bool("DJANGO_DEBUG", default=False)
ALLOWED_HOSTS: list[str] = env.list("DJANGO_ALLOWED_HOSTS", default=[])

# ─────────────────────────────────────────────────────────────────────────────
# Ilovalar
# ─────────────────────────────────────────────────────────────────────────────
DJANGO_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "django.contrib.postgres",
]

THIRD_PARTY_APPS = [
    "rest_framework",
    "django_celery_beat",
]

LOCAL_APPS = [
    "apps.common",
    "apps.accounts",
    "apps.customers",
    "apps.catalog",
    "apps.inventory",
    "apps.orders",
    "apps.audit",
]

INSTALLED_APPS = DJANGO_APPS + THIRD_PARTY_APPS + LOCAL_APPS

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "config.urls"
WSGI_APPLICATION = "config.wsgi.application"
ASGI_APPLICATION = "config.asgi.application"

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
    },
]

# ─────────────────────────────────────────────────────────────────────────────
# Ma'lumotlar bazasi — PostgreSQL majburiy
#
# SQLite ishlatilmaydi: loyiha CheckConstraint, PL/pgSQL triggerlari,
# pg_trgm va SELECT FOR UPDATE ga tayanadi (INV-01 … INV-04).
# ─────────────────────────────────────────────────────────────────────────────
DATABASES = {
    "default": {
        **env.db_url("DATABASE_URL"),
        "ATOMIC_REQUESTS": False,  # tranzaksiya chegarasi `services` qatlamida aniq belgilanadi
        "CONN_MAX_AGE": env.int("DB_CONN_MAX_AGE", default=60),
        "OPTIONS": {
            # Qulf kutish cheksiz bo'lmasligi kerak: poyga holatida so'rov tez xato bersin,
            # aks holda ovoz suhbati kechikish byudjetidan chiqib ketadi.
            "options": f"-c lock_timeout={env.int('DB_LOCK_TIMEOUT_MS', default=3000)}ms"
        },
    }
}

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# ─────────────────────────────────────────────────────────────────────────────
# Autentifikatsiya va parollar (STRIDE-S)
# ─────────────────────────────────────────────────────────────────────────────
AUTH_USER_MODEL = "accounts.User"

# Argon2 birinchi o'rinda — konstitutsiya talabi (3-bosqich, 1-band)
PASSWORD_HASHERS = [
    "django.contrib.auth.hashers.Argon2PasswordHasher",
    "django.contrib.auth.hashers.PBKDF2PasswordHasher",
]

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {
        "NAME": "django.contrib.auth.password_validation.MinimumLengthValidator",
        "OPTIONS": {"min_length": 12},
    },
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

# ─────────────────────────────────────────────────────────────────────────────
# Lokalizatsiya — asosiy til o'zbek, vaqt zonasi Toshkent
# ─────────────────────────────────────────────────────────────────────────────
LANGUAGE_CODE = "uz"
TIME_ZONE = "Asia/Tashkent"
USE_I18N = True
USE_TZ = True  # naive datetime taqiqlanadi (ruff DTZ)

STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "static_root"
MEDIA_URL = "media/"
MEDIA_ROOT = BASE_DIR / "media"

# ─────────────────────────────────────────────────────────────────────────────
# Kesh va Redis
#
# Global o'zgaruvchan holat (`global _cache`) taqiqlanadi — kesh faqat shu
# thread-safe infratuzilma orqali yuritiladi (konstitutsiya 3.3).
# ─────────────────────────────────────────────────────────────────────────────
REDIS_URL: str = env("REDIS_URL")

CACHES = {
    "default": {
        "BACKEND": "django.core.cache.backends.redis.RedisCache",
        "LOCATION": REDIS_URL,
    }
}

CELERY_BROKER_URL = REDIS_URL
CELERY_RESULT_BACKEND = REDIS_URL
CELERY_TASK_ALWAYS_EAGER = False
CELERY_TIMEZONE = TIME_ZONE
CELERY_TASK_ACKS_LATE = True
CELERY_TASK_REJECT_ON_WORKER_LOST = True

# ─────────────────────────────────────────────────────────────────────────────
# Biznes qoidalari — barchasi sozlanadigan (hardcode taqiqlanadi, Hooker 5)
# ─────────────────────────────────────────────────────────────────────────────
# INV-07: rezervatsiya muddati
RESERVATION_TTL_MINUTES: int = env.int("RESERVATION_TTL_MINUTES", default=15)

# §6: qoldiqni oshkor qilish siyosati — shu chegaradan kam bo'lsa aniq son aytiladi
LOW_STOCK_DISCLOSURE_THRESHOLD: int = env.int("LOW_STOCK_DISCLOSURE_THRESHOLD", default=5)

# INV-06: bir telefon raqamdan qo'ng'iroq chastotasi chegarasi
CALL_RATE_LIMIT_PER_MINUTE: int = env.int("CALL_RATE_LIMIT_PER_MINUTE", default=3)

# Maxfiylik: qo'ng'iroq yozuvi va transkript saqlanish muddati
RECORDING_RETENTION_DAYS: int = env.int("RECORDING_RETENTION_DAYS", default=90)

# ─────────────────────────────────────────────────────────────────────────────
# Loglar — ikkita alohida oqim: umumiy va xavfsizlik (STRIDE-R)
#
# `call_operator.security` logeri hech qachon o'chirilmaydi: ruxsatsiz kirish
# urinishlari va zaxiradan ortiqcha chiqim so'rovlari shu yerga tushadi.
# ─────────────────────────────────────────────────────────────────────────────
LOGGING: dict[str, Any] = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "json": {
            "()": "structlog.stdlib.ProcessorFormatter",
            "processor": "structlog.processors.JSONRenderer",
        },
        "console": {
            "format": "{levelname} {asctime} {name} {message}",
            "style": "{",
        },
    },
    "handlers": {
        "console": {
            "class": "logging.StreamHandler",
            "formatter": "console",
        },
    },
    "loggers": {
        "django": {"handlers": ["console"], "level": "INFO", "propagate": False},
        "call_operator": {"handlers": ["console"], "level": "INFO", "propagate": False},
        "call_operator.security": {
            "handlers": ["console"],
            "level": "WARNING",
            "propagate": False,
        },
    },
}

# ─────────────────────────────────────────────────────────────────────────────
# DRF — ichki API (ovoz bridge shu orqali biznes amallarini chaqiradi)
# ─────────────────────────────────────────────────────────────────────────────
REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": [
        "rest_framework.authentication.SessionAuthentication",
    ],
    # Ruxsat berilmagan kirish standart holat: har view o'z ruxsatini aniq e'lon qiladi
    "DEFAULT_PERMISSION_CLASSES": ["rest_framework.permissions.IsAuthenticated"],
    "DEFAULT_THROTTLE_RATES": {"anon": "20/min", "user": "600/min"},
    "DEFAULT_RENDERER_CLASSES": ["rest_framework.renderers.JSONRenderer"],
}
