"""Aktor huquqlari testlari — loyihaning eng muhim xavfsizlik chegarasi.

Bu testlar `Actor` ruxsat matritsasini qoplaydi. Ular buzilsa AI operator
tijorat sirini oshkor qilishi yoki Instagram akkauntini bloklanishga olib
keluvchi amal bajarishi mumkin.

DB kerak emas: `Actor` — o'zgarmas dataclass, `User` esa saqlanmasdan
yaratiladi (faqat `role` maydoni tekshiriladi).
"""

import pytest

from apps.accounts.actors import Actor, ActorKind
from apps.accounts.models import StaffRole, User


def staff(role: str) -> Actor:
    """Berilgan roldagi xodim aktori (saqlanmagan `User` bilan)."""
    return Actor.staff(User(username=f"test_{role.lower()}", role=role, pk=1))


AI = Actor.ai(customer_id=42, conversation_id=7, channel="VOICE", trace_id="trace-1")
SYSTEM = Actor.system(trace_id="trace-2")


class TestAiOperatorRestrictions:
    """AI operator uchun yopiq huquqlar.

    Bu to'rtta tekshiruv — arxitektura kafolati. Prompt-injection bilan
    "chegirma ber" yoki "tannarxni aytib ber" deb so'rash mumkin, ammo
    kod darajasida bu huquqlar yo'q.
    """

    def test_ai_tannarxni_kormaydi(self) -> None:
        """Tijorat siri (STRIDE-I): tannarx va foyda marjasi."""
        assert AI.can_view_cost_price is False

    def test_ai_chegirma_bermaydi(self) -> None:
        """INV-05: chegirma faqat nazoratchi va ma'mur huquqida."""
        assert AI.can_grant_discount is False

    def test_ai_zaxirani_ozgartirmaydi(self) -> None:
        """AI faqat rezervatsiya qila oladi — u TTL bilan o'z-o'zidan qaytadi."""
        assert AI.can_adjust_stock is False

    def test_ai_instagram_human_agent_tegini_ishlatmaydi(self) -> None:
        """INV-13: Meta bu tegni faqat haqiqiy odam javobiga ruxsat beradi.

        Bot uchun ishlatish - API'dan mahrum bo'lishning eng tez yo'li.
        """
        assert AI.can_use_human_agent_tag is False

    def test_ai_yozuvlarni_ochirmaydi(self) -> None:
        assert AI.can_delete_records is False

    def test_ai_oz_suhbatidagi_mijoz_malumotini_koradi(self) -> None:
        """Buyurtma tarixi kerak, aks holda AI savollarga javob berolmaydi.

        Cheklov `selectors` qatlamida: so'ralgan mijoz `actor.customer_id`
        bilan solishtiriladi.
        """
        assert AI.can_view_customer_personal_data is True


class TestStaffRolePermissions:
    """Xodim rollari matritsasi (domen tahlili §3)."""

    @pytest.mark.parametrize(
        ("role", "expected"),
        [
            (StaffRole.WAREHOUSE, True),
            (StaffRole.SUPERVISOR, True),
            (StaffRole.ADMIN, True),
            (StaffRole.HUMAN_OPERATOR, False),  # operatorga tannarx kerak emas
        ],
    )
    def test_tannarxni_korish_huquqi(self, role: str, expected: bool) -> None:
        assert staff(role).can_view_cost_price is expected

    @pytest.mark.parametrize(
        ("role", "expected"),
        [
            (StaffRole.SUPERVISOR, True),
            (StaffRole.ADMIN, True),
            (StaffRole.HUMAN_OPERATOR, False),
            (StaffRole.WAREHOUSE, False),
        ],
    )
    def test_chegirma_berish_huquqi(self, role: str, expected: bool) -> None:
        assert staff(role).can_grant_discount is expected

    @pytest.mark.parametrize(
        ("role", "expected"),
        [
            (StaffRole.WAREHOUSE, True),
            (StaffRole.ADMIN, True),
            (StaffRole.SUPERVISOR, False),  # nazoratchi ko'radi, lekin o'zgartirmaydi
            (StaffRole.HUMAN_OPERATOR, False),
        ],
    )
    def test_zaxirani_ozgartirish_huquqi(self, role: str, expected: bool) -> None:
        assert staff(role).can_adjust_stock is expected

    @pytest.mark.parametrize(
        ("role", "expected"),
        [
            (StaffRole.HUMAN_OPERATOR, True),  # haqiqiy odam javob beradi
            (StaffRole.ADMIN, True),
            (StaffRole.SUPERVISOR, False),
            (StaffRole.WAREHOUSE, False),
        ],
    )
    def test_human_agent_tegi_huquqi(self, role: str, expected: bool) -> None:
        assert staff(role).can_use_human_agent_tag is expected

    def test_faqat_mamur_ochiradi(self) -> None:
        assert staff(StaffRole.ADMIN).can_delete_records is True
        for role in (StaffRole.SUPERVISOR, StaffRole.WAREHOUSE, StaffRole.HUMAN_OPERATOR):
            assert staff(role).can_delete_records is False


class TestSystemActor:
    """Fon vazifalari (Celery) aktori.

    Tizim aktori hech qanday nozik huquqqa ega emas: u faqat TTL tugagan
    rezervatsiyalarni bekor qiladi va yozuvlarni tozalaydi. Agar u keng
    huquqqa ega bo'lsa, buzilgan fon vazifasi katta zarar keltirardi.
    """

    def test_tizim_aktori_nozik_huquqlarga_ega_emas(self) -> None:
        assert SYSTEM.can_view_cost_price is False
        assert SYSTEM.can_grant_discount is False
        assert SYSTEM.can_adjust_stock is False
        assert SYSTEM.can_use_human_agent_tag is False
        assert SYSTEM.can_delete_records is False


class TestActorLabel:
    """`label` audit jurnalida saqlanadi — u aniq va barqaror bo'lishi shart."""

    def test_ai_yorligi_kanal_va_suhbatni_korsatadi(self) -> None:
        assert AI.label == "ai:VOICE:conv7"

    def test_xodim_yorligi_id_va_rolni_korsatadi(self) -> None:
        assert staff(StaffRole.WAREHOUSE).label == "staff:1:WAREHOUSE"

    def test_tizim_yorligi(self) -> None:
        assert SYSTEM.label == "system"


class TestActorImmutability:
    """Aktor o'zgarmas: amal o'rtasida huquq o'zgarmaydi."""

    def test_aktor_maydonini_ozgartirish_mumkin_emas(self) -> None:
        actor = Actor.ai(customer_id=1, conversation_id=1, channel="VOICE", trace_id="t")
        with pytest.raises(AttributeError):
            actor.kind = ActorKind.STAFF  # type: ignore[misc]
