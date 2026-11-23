"""Xodimlar va rollar.

Muhim domen farqi: **mijoz (`Customer`) foydalanuvchi (`User`) emas.**
Mijoz tizimga kirmaydi — u telefon orqali gaplashadi yoki Telegram/Instagram'da
yozadi. `User` faqat xodimlar uchun: operator, nazoratchi, ombor, ma'mur.
"""

from django.contrib.auth.models import AbstractUser
from django.db import models
from django.utils.translation import gettext_lazy as _


class StaffRole(models.TextChoices):
    """Xodim rollari (RBAC — domen tahlili §3)."""

    HUMAN_OPERATOR = "HUMAN_OPERATOR", _("Operator")
    SUPERVISOR = "SUPERVISOR", _("Nazoratchi")
    WAREHOUSE = "WAREHOUSE", _("Ombor xodimi")
    ADMIN = "ADMIN", _("Ma'mur")


class User(AbstractUser):
    """Xodim hisobi.

    Yangi hisob `is_approved=False` holatda yaratiladi va ma'mur tasdig'ini
    kutadi (STRIDE-S — Approval Gate, konstitutsiya 3-bosqich 2-bandi).
    """

    role = models.CharField(
        _("rol"),
        max_length=20,
        choices=StaffRole.choices,
        db_index=True,
    )
    is_approved = models.BooleanField(
        _("tasdiqlangan"),
        default=False,
        help_text=_("Ma'mur tasdiqlamaguncha xodim tizimdan foydalana olmaydi."),
    )
    phone = models.CharField(_("telefon"), max_length=20, blank=True)

    class Meta:
        verbose_name = _("xodim")
        verbose_name_plural = _("xodimlar")

    def __str__(self) -> str:
        return f"{self.get_username()} ({StaffRole(self.role).label if self.role else '—'})"
