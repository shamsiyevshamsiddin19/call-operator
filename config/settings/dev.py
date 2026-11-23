"""Ishlab chiqish muhiti."""

from .base import *

DEBUG = True
ALLOWED_HOSTS = ["localhost", "127.0.0.1", "[::1]"]

# N+1 so'rovlarni erta aniqlash uchun SQL loglarini DB_LOG_LEVEL=DEBUG bilan yoqish mumkin
LOGGING["loggers"]["django.db.backends"] = {
    "handlers": ["console"],
    "level": env("DB_LOG_LEVEL", default="INFO"),
    "propagate": False,
}
