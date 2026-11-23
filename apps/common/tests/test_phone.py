"""Telefon normalizatsiyasi testlari.

Bu sof funksiya (DB kerak emas), shuning uchun testlar tez ishlaydi va
chegaraviy qiymatlar (BVA) to'liq qoplanadi.

**Nega bu test muhim.** Normalizatsiya buzilsa bitta mijoz uchun bir necha
`Customer` yozuvi paydo bo'ladi, buyurtma tarixi bo'linadi va AI "sizda
buyurtma yo'q" deb aytadi — mijoz esa kechagi buyurtmasini so'rayotgan bo'ladi.
"""

import pytest
from hypothesis import given
from hypothesis import strategies as st

from apps.common.phone import InvalidPhoneNumberError, mask_phone, normalize_phone

CANONICAL = "+998901234567"


class TestNormalizePhoneEquivalenceClasses:
    """Ekvivalentlik sinflari (EQP): bir xil raqamning turli yozilishi."""

    @pytest.mark.parametrize(
        "raw",
        [
            "+998901234567",  # kanonik
            "998901234567",  # plyussiz
            "901234567",  # faqat abonent qismi
            "8901234567",  # mahalliy "8" prefiksi bilan
            "+998 90 123 45 67",  # bo'sh joylar bilan
            "+998 (90) 123-45-67",  # qavs va chiziqcha bilan
            "  +998901234567  ",  # atrofida bo'sh joy
            "8 90 123 45 67",  # "8" va bo'sh joylar
        ],
    )
    def test_barcha_variantlar_bir_xil_natija_beradi(self, raw: str) -> None:
        assert normalize_phone(raw) == CANONICAL


class TestNormalizePhoneBoundaries:
    """Chegaraviy qiymatlar tahlili (BVA) va salbiy holatlar."""

    @pytest.mark.parametrize(
        "raw",
        [
            "",  # bo'sh
            "   ",  # faqat bo'sh joy
            "abc",  # raqamsiz
            "90123456",  # bir raqam kam (8)
            "9012345678",  # bir raqam ko'p (10)
            "+79001234567",  # boshqa davlat kodi (Rossiya)
            "+1234567890",  # noma'lum kod
            "0",  # bitta raqam
        ],
    )
    def test_notogri_format_xatolik_beradi(self, raw: str) -> None:
        with pytest.raises(InvalidPhoneNumberError):
            normalize_phone(raw)

    def test_xatolik_mijozga_tushunarli_xabar_beradi(self) -> None:
        """Xatolik AI uchun ovozda aytiladigan matnni o'z ichiga oladi."""
        with pytest.raises(InvalidPhoneNumberError) as exc_info:
            normalize_phone("abc")

        assert exc_info.value.user_message
        # Mijozga aytiladigan matnda ichki texnik detal bo'lmasligi shart (STRIDE-I)
        assert "abc" not in exc_info.value.user_message


class TestNormalizePhoneIdempotence:
    """Normalizatsiya idempotent bo'lishi shart (FP — sof funksiya xossasi)."""

    def test_ikki_marta_qollash_natijani_ozgartirmaydi(self) -> None:
        once = normalize_phone("901234567")
        twice = normalize_phone(once)
        assert once == twice

    @given(st.integers(min_value=100_000_000, max_value=999_999_999))
    def test_har_qanday_togri_abonent_raqami_uchun_idempotent(self, subscriber: int) -> None:
        """Property-based test: 9 raqamli har qanday abonent raqami uchun.

        Bu 900 million variantdan tasodifiy namuna oladi — qo'lda yozilgan
        parametrlar qoplamaydigan holatlarni topadi.
        """
        normalized = normalize_phone(str(subscriber))
        assert normalize_phone(normalized) == normalized
        assert normalized.startswith("+998")
        assert len(normalized) == len(CANONICAL)


class TestMaskPhone:
    """Maskalash: loglarda to'liq raqam saqlanmasligi kerak (maxfiylik)."""

    def test_orta_qism_yashiriladi(self) -> None:
        masked = mask_phone(CANONICAL)
        assert masked == "+99890***4567"

    def test_togri_raqam_toliq_korinmaydi(self) -> None:
        masked = mask_phone(CANONICAL)
        assert CANONICAL not in masked
        # Operator kodi va oxirgi 4 raqam qoladi — mijozni ajratish uchun yetarli,
        # raqamni qayta tiklash uchun yetarli emas.
        assert "1234" not in masked

    @pytest.mark.parametrize("short", ["", "123", "+998"])
    def test_qisqa_qiymatlar_toliq_yashiriladi(self, short: str) -> None:
        assert mask_phone(short) == "***"
