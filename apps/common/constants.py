"""Bir necha modulda ishlatiladigan umumiy konstantalar."""

from django.db import models
from django.utils.translation import gettext_lazy as _


class ChannelKind(models.TextChoices):
    """Mijoz bilan aloqa kanali.

    `dialog` moduli qaysi kanalda ishlayotganini bilmaydi (kanal-agnostiklik),
    ammo `customers` va `audit` kanalni ajratishi kerak: har kanalning o'z
    identifikatori va o'z platforma cheklovi bor.
    """

    VOICE = "VOICE", _("Telefon qo'ng'iroq")
    TELEGRAM = "TELEGRAM", _("Telegram")
    INSTAGRAM = "INSTAGRAM", _("Instagram")
    SMS = "SMS", _("SMS")
