"""Umumiy abstrakt modellar (DRY — Hooker 6).

`ImmutableModel` konstitutsiyaning 4-bandini (Immutability & Append-only Ledger)
dastur darajasida qo'llaydi. Ammo bu **yakka himoya emas** — haqiqiy kafolat
ma'lumotlar bazasi triggerida (`apps/*/migrations/*_ledger_trigger.py`), chunki
dastur kodini aylanib o'tish mumkin (masalan `QuerySet.update()` yoki to'g'ridan-to'g'ri SQL).
"""

from typing import Any, Never

from django.db import models
from django.utils.translation import gettext_lazy as _


class TimestampedModel(models.Model):
    """Yaratilgan va o'zgartirilgan vaqtni kuzatuvchi baza model."""

    created_at = models.DateTimeField(_("yaratilgan vaqti"), auto_now_add=True, db_index=True)
    updated_at = models.DateTimeField(_("o'zgartirilgan vaqti"), auto_now=True)

    class Meta:
        abstract = True


class ImmutableModel(models.Model):
    """O'zgarmas yozuv: yaratilgandan keyin tahrirlash va o'chirish taqiqlanadi.

    Moliyaviy va ombor harakatlari, transkriptlar va audit yozuvlari uchun.
    Tuzatish kiritish kerak bo'lsa — teskari yo'nalishdagi **yangi yozuv** qo'shiladi
    (storno), mavjud yozuv o'zgartirilmaydi.
    """

    created_at = models.DateTimeField(_("yaratilgan vaqti"), auto_now_add=True, db_index=True)

    class Meta:
        abstract = True

    def save(self, *args: Any, **kwargs: Any) -> None:
        if self.pk is not None:
            raise ImmutableRecordError(self.__class__.__name__)
        super().save(*args, **kwargs)

    def delete(self, *args: Any, **kwargs: Any) -> Never:  # noqa: ARG002
        # Argumentlar ishlatilmaydi, ammo imzo Django'ning `Model.delete()` bilan
        # mos bo'lishi shart (Liskov Substitution — SOLID L).
        raise ImmutableRecordError(self.__class__.__name__)


class ImmutableRecordError(Exception):
    """O'zgarmas yozuvni tahrirlash yoki o'chirishga urinish."""

    def __init__(self, model_name: str) -> None:
        super().__init__(
            f"{model_name} — o'zgarmas (append-only) yozuv: "
            f"tahrirlash va o'chirish taqiqlanadi. Tuzatish uchun teskari yozuv qo'shing."
        )


class SoftDeletableModel(models.Model):
    """O'chirish o'rniga arxivlash.

    Katalog va mijoz yozuvlari jismonan o'chirilmaydi: ularga buyurtma tarixi
    bog'langan (INV-09, `on_delete=PROTECT`). Arxivlangan yozuv yangi
    buyurtmalarda ko'rinmaydi, ammo tarix buzilmaydi.

    Bu yerda maxsus manager (`.active()` kabi metodlar bilan) **ataylab yo'q**.
    Birinchi variantda u yozilgan edi, ammo hech qayerda chaqirilmadi:
    `selectors` qatlami baribir `filter(is_active=True)` ni boshqa shartlar
    bilan birga yozadi. Ishlatilmaydigan abstraksiya esa zarar keltiradi —
    abstract modelda e'lon qilingan manager vorislarda tip ma'lumotini
    yo'qotadi va butun kod bazasida tip tekshiruvini buzadi (KISS, Hooker 2).
    """

    is_active = models.BooleanField(_("faol"), default=True, db_index=True)

    class Meta:
        abstract = True
