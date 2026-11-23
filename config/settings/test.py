"""Test muhiti.

PostgreSQL majburiy: testlar CheckConstraint, PL/pgSQL trigger va
SELECT FOR UPDATE xatti-harakatini tekshiradi — SQLite ularni simulyatsiya
qilolmaydi, shuning uchun bu yerda baza almashtirilmaydi.
"""

from .base import *

DEBUG = False
ALLOWED_HOSTS = ["testserver"]

# Testlar tez ketishi uchun: parol xeshlash qimmat algoritmdan yengilga almashtiriladi.
# Bu faqat test muhitida — Argon2 ishlashini alohida test tekshiradi.
PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]

CELERY_TASK_ALWAYS_EAGER = True
CELERY_TASK_EAGER_PROPAGATES = True

# Testlarda kesh izolyatsiyasi: Redis holati testlar orasida oqib ketmasligi uchun
CACHES = {
    "default": {
        "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
        "LOCATION": "test-cache",
    }
}
