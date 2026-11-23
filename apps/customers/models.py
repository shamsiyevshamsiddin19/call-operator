"""Mijozlar va kanal identifikatorlari (Identity Resolution).

Markaziy domen muammosi: **bitta jismoniy odam uch kanalda uch xil
identifikatorga ega.** Telefonda u `+998901234567`, Telegramda `chat_id=123456789`,
Instagramda `17841400000`. Agar bu uchta narsa bitta `Customer` ga bog'lanmasa:

* mijoz Instagram'da so'ragan savolni telefonda qaytadan tushuntiradi (INV-15 buzilishi);
* buyurtma tarixi bo'linib ketadi;
* AI "sizda 3 ta buyurtma bor" deganda yarmini ko'rmaydi.
"""

from django.db import models
from django.db.models import Q
from django.utils.translation import gettext_lazy as _

from apps.common.constants import ChannelKind
from apps.common.models import SoftDeletableModel, TimestampedModel
from apps.common.phone import mask_phone


class Customer(TimestampedModel, SoftDeletableModel):
    """Mijoz. Tizimga kirmaydi — telefonda gaplashadi yoki messenjerda yozadi.

    `primary_phone` bo'sh bo'lishi mumkin: Instagram izohidan kelgan lead'da
    hali raqam yo'q. Raqam paydo bo'lganda va SMS kodi bilan tasdiqlanganda
    to'ldiriladi.
    """

    full_name = models.CharField(_("to'liq ism"), max_length=200, blank=True)
    primary_phone = models.CharField(
        _("asosiy telefon"),
        max_length=20,
        blank=True,
        db_index=True,
        help_text=_("E.164 formatda: +998901234567"),
    )
    is_blacklisted = models.BooleanField(
        _("qora ro'yxatda"),
        default=False,
        db_index=True,
        help_text=_("Spam yoki haqoratli xatti-harakat. AI bu mijoz bilan suhbatlashmaydi."),
    )
    blacklist_reason = models.TextField(_("qora ro'yxat sababi"), blank=True)
    notes = models.TextField(_("izohlar"), blank=True)

    class Meta:
        verbose_name = _("mijoz")
        verbose_name_plural = _("mijozlar")
        constraints = [
            # Bo'sh bo'lmagan raqam yagona bo'lishi shart. Bo'sh qiymatlar
            # cheklovga tushmaydi — Instagram lead'larida raqam hali yo'q.
            models.UniqueConstraint(
                fields=["primary_phone"],
                condition=~Q(primary_phone=""),
                name="customer_primary_phone_unique_when_set",
            ),
            # Qora ro'yxatga olingan mijozda sabab ko'rsatilishi shart —
            # "shunchaki bloklab qo'yish" taqiqlanadi, qaror asoslanishi kerak.
            models.CheckConstraint(
                condition=Q(is_blacklisted=False) | ~Q(blacklist_reason=""),
                name="customer_blacklist_requires_reason",
            ),
        ]
        indexes = [
            models.Index(fields=["is_active", "is_blacklisted"], name="customer_status_idx"),
        ]

    def __str__(self) -> str:
        name = self.full_name or _("ismi noma'lum")
        phone = mask_phone(self.primary_phone) if self.primary_phone else "—"
        return f"{name} ({phone})"


class CustomerIdentity(TimestampedModel):
    """Mijozning muayyan kanaldagi identifikatori.

    **Tasdiqlash (`is_verified`) nima uchun kerak.** Instagram akkaunt nomi
    hech narsani isbotlamaydi — istalgan odam o'zini boshqa mijoz deb
    ko'rsatishi mumkin (STRIDE-S, Spoofing). Shuning uchun tasdiqlanmagan
    identity bo'yicha **shaxsiy ma'lumot berilmaydi**: buyurtma tarixi,
    manzil, telefon raqami. Faqat umumiy katalog ma'lumoti.

    Tasdiqlash yo'llari:
      * `VOICE`    — caller ID operator tarmog'idan keladi, ishonchli;
      * `TELEGRAM` — `request_contact` tugmasi yoki imzolangan deep-link token (INV-17);
      * `INSTAGRAM`— mijoz DM'da raqam beradi va SMS kodi tasdiqlanadi.
    """

    customer = models.ForeignKey(
        Customer,
        on_delete=models.PROTECT,  # INV-09: identity bilan birga tarix yo'qolmaydi
        related_name="identities",
        verbose_name=_("mijoz"),
    )
    channel = models.CharField(
        _("kanal"),
        max_length=20,
        choices=ChannelKind.choices,
        db_index=True,
    )
    external_id = models.CharField(
        _("tashqi identifikator"),
        max_length=128,
        help_text=_("VOICE: E.164 raqam · TELEGRAM: chat_id · INSTAGRAM: IGSID"),
    )
    display_name = models.CharField(
        _("ko'rinadigan nom"),
        max_length=200,
        blank=True,
        help_text=_("Telegram ismi yoki Instagram username — faqat ma'lumot uchun."),
    )
    is_verified = models.BooleanField(_("tasdiqlangan"), default=False, db_index=True)
    verified_at = models.DateTimeField(_("tasdiqlangan vaqti"), null=True, blank=True)

    # Telegram uchun hayotiy maydon: bot foydalanuvchiga **birinchi yoza olmaydi**.
    # Mijoz botni ishga tushirgan bo'lsagina bu `True` bo'ladi va xabar yuborish mumkin (INV-11).
    can_receive_messages = models.BooleanField(
        _("xabar qabul qila oladi"),
        default=False,
        help_text=_(
            "Telegram: mijoz botni ishga tushirgan. "
            "Instagram: javob oynasi ochiq. False bo'lsa xabar yuborilmaydi."
        ),
    )

    class Meta:
        verbose_name = _("mijoz identifikatori")
        verbose_name_plural = _("mijoz identifikatorlari")
        constraints = [
            # Bir kanalda bir tashqi ID faqat bitta mijozga tegishli bo'ladi.
            # Bu Identity Resolution ning poydevori: ikkita mijoz bir xil
            # chat_id ga ega bo'lsa, suhbat qaysi mijozga tegishliligi noaniq bo'lardi.
            models.UniqueConstraint(
                fields=["channel", "external_id"],
                name="identity_channel_external_unique",
            ),
            # Tasdiqlangan identity da tasdiqlash vaqti bo'lishi shart —
            # "kim, qachon tasdiqladi" savoliga javob bo'lmasa audit ma'nosiz (STRIDE-R).
            models.CheckConstraint(
                condition=Q(is_verified=False, verified_at__isnull=True)
                | Q(is_verified=True, verified_at__isnull=False),
                name="identity_verified_requires_timestamp",
            ),
        ]
        indexes = [
            models.Index(
                fields=["customer", "channel"],
                name="identity_customer_channel_idx",
            ),
        ]

    def __str__(self) -> str:
        shown = (
            mask_phone(self.external_id)
            if self.channel == ChannelKind.VOICE
            else self.display_name or self.external_id
        )
        mark = "✓" if self.is_verified else "?"
        return f"{self.get_channel_display()}: {shown} {mark}"
