"""Telefon raqamini normallashtirish.

**Nega alohida modul.** Bir xil mijoz raqami kanal bo'ylab turlicha keladi:
caller ID `+998901234567`, Telegram `request_contact` `998901234567`,
mijoz ovozda `90 123 45 67` deydi. Normallashtirilmasa bitta odam uchun
uchta `Customer` yozuvi paydo bo'ladi va buyurtma tarixi bo'linib ketadi.

Saqlash formati — **E.164** (`+998901234567`): bitta kanonik ko'rinish.
Bu `UniqueConstraint` ning ishlashi uchun majburiy shart.

Bu sof funksiya (Pure Function — FP): bir xil kiruvchi qiymatga har doim
bir xil natija, tashqi holatga tegmaydi.
"""

import re

from apps.common.exceptions import BusinessRuleViolation

# O'zbekiston: +998 + 9 raqam (operator kodi 2 + abonent 7)
_UZ_COUNTRY_CODE = "998"
_UZ_NATIONAL_LENGTH = 9

_NON_DIGIT = re.compile(r"\D")

# Mahalliy odat: shahar/mobil raqam oldidan "8" terish (8 90 123 45 67)
_LEGACY_TRUNK_PREFIX = "8"

# Maskalashda ko'rinadigan qismlar: +99890***4567
_MASK_PREFIX_LEN = 6
_MASK_SUFFIX_LEN = 4


class InvalidPhoneNumberError(BusinessRuleViolation):
    """Telefon raqami tanib bo'lmaydigan formatda."""

    code = "invalid_phone_number"
    user_message = "Telefon raqamini qayta aytib bera olasizmi?"


def normalize_phone(raw: str) -> str:
    """Raqamni E.164 formatiga keltiradi.

    Qabul qiladi:
        ``+998901234567``, ``998901234567``, ``901234567``,
        ``+998 90 123-45-67``, ``8 90 123 45 67``

    Qaytaradi:
        ``+998901234567``

    Xatolik:
        `InvalidPhoneNumberError` — uzunlik yoki kod mos kelmasa.
    """
    digits = _NON_DIGIT.sub("", raw)

    if not digits:
        raise InvalidPhoneNumberError(f"bo'sh raqam: {raw!r}")

    if len(digits) == _UZ_NATIONAL_LENGTH + 1 and digits.startswith(_LEGACY_TRUNK_PREFIX):
        digits = digits[len(_LEGACY_TRUNK_PREFIX) :]

    # Faqat abonent qismi aytilgan: 901234567
    if len(digits) == _UZ_NATIONAL_LENGTH:
        digits = _UZ_COUNTRY_CODE + digits

    if not digits.startswith(_UZ_COUNTRY_CODE):
        raise InvalidPhoneNumberError(f"O'zbekiston kodi emas: {raw!r}")

    if len(digits) != len(_UZ_COUNTRY_CODE) + _UZ_NATIONAL_LENGTH:
        raise InvalidPhoneNumberError(f"uzunlik mos emas: {raw!r} ({len(digits)} raqam)")

    return f"+{digits}"


def mask_phone(e164: str) -> str:
    """Logda ko'rsatish uchun raqamni yashiradi: ``+998901234567`` → ``+99890***4567``.

    Qo'ng'iroq loglari va audit yozuvlarida to'liq raqam saqlanmaydi —
    maxfiylik talabi (domen tahlili §7, Security).
    """
    if len(e164) < _MASK_PREFIX_LEN + _MASK_SUFFIX_LEN:
        return "***"
    return f"{e164[:_MASK_PREFIX_LEN]}***{e164[-_MASK_SUFFIX_LEN:]}"
