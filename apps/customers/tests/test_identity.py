"""Identity Resolution testlari — INV-15, STRIDE-S.

Markaziy domen muammosi: bitta odam uch kanalda uch xil identifikatorga ega.
Agar ular bitta `Customer` ga bog'lanmasa, mijoz Instagram'da so'ragan
savolni telefonda qaytadan tushuntiradi va buyurtma tarixi bo'linib ketadi.
"""

import pytest
from django.db import transaction
from django.db.utils import IntegrityError

from apps.accounts.actors import Actor
from apps.audit.models import AuditEvent, AuditEventType, AuditSeverity
from apps.common.constants import ChannelKind
from apps.common.phone import InvalidPhoneNumberError
from apps.customers import services
from apps.customers.models import Customer, CustomerIdentity
from tests.factories import CustomerFactory

pytestmark = pytest.mark.django_db

SYSTEM = Actor.system(trace_id="test")


class TestResolveOrCreateByPhone:
    def test_yangi_raqam_mijoz_yaratadi(self) -> None:
        customer, created = services.resolve_or_create_by_phone(
            SYSTEM, raw_phone="901234567", display_name="Ali"
        )

        assert created is True
        assert customer.primary_phone == "+998901234567"
        assert customer.full_name == "Ali"

    def test_caller_id_darhol_tasdiqlanadi(self) -> None:
        """Telefon raqami operator tarmog'idan keladi — ishonchli manba."""
        customer, _ = services.resolve_or_create_by_phone(SYSTEM, raw_phone="901234567")

        identity = customer.identities.get(channel=ChannelKind.VOICE)
        assert identity.is_verified is True
        assert identity.verified_at is not None

    def test_mavjud_raqam_yangi_mijoz_yaratmaydi(self) -> None:
        first, created_first = services.resolve_or_create_by_phone(SYSTEM, raw_phone="901234567")
        second, created_second = services.resolve_or_create_by_phone(SYSTEM, raw_phone="901234567")

        assert created_first is True
        assert created_second is False
        assert first.pk == second.pk
        assert Customer.objects.count() == 1

    def test_turli_formatdagi_bir_xil_raqam_bitta_mijozga_boglanadi(self) -> None:
        """Normalizatsiya buzilsa bitta odam uchun uchta mijoz paydo bo'lardi."""
        services.resolve_or_create_by_phone(SYSTEM, raw_phone="+998901234567")
        services.resolve_or_create_by_phone(SYSTEM, raw_phone="901234567")
        services.resolve_or_create_by_phone(SYSTEM, raw_phone="8 90 123 45 67")

        assert Customer.objects.count() == 1
        assert CustomerIdentity.objects.filter(channel=ChannelKind.VOICE).count() == 1

    def test_notogri_raqam_rad_etiladi(self) -> None:
        with pytest.raises(InvalidPhoneNumberError):
            services.resolve_or_create_by_phone(SYSTEM, raw_phone="abc")

    def test_mijoz_yaratilishi_auditga_tushadi(self) -> None:
        customer, _ = services.resolve_or_create_by_phone(SYSTEM, raw_phone="901234567")

        event = AuditEvent.objects.filter(
            event_type=AuditEventType.IDENTITY_VERIFIED, customer_ref=customer.pk
        ).first()
        assert event is not None
        # Maxfiylik: auditda to'liq raqam saqlanmaydi
        assert "+998901234567" not in event.summary
        assert event.payload["phone_masked"] == "+99890***4567"


class TestLinkIdentity:
    def test_telegram_optin_xabar_yuborishga_ruxsat_beradi(self, customer: Customer) -> None:
        """Telegram bot foydalanuvchiga birinchi yoza olmaydi (INV-11).

        `can_receive_messages=True` faqat mijoz botni ishga tushirgandan keyin
        qo'yiladi — aks holda `403 Forbidden` olinadi.
        """
        identity = services.link_identity(
            SYSTEM,
            customer=customer,
            channel=ChannelKind.TELEGRAM,
            external_id="123456789",
            display_name="Ali",
            is_verified=True,
            can_receive_messages=True,
        )

        assert identity.can_receive_messages is True
        assert identity.is_verified is True
        assert identity.verified_at is not None

    def test_instagram_identity_tasdiqlanmagan_holatda_boshlanadi(self, customer: Customer) -> None:
        """STRIDE-S: Instagram akkaunt nomi hech narsani isbotlamaydi."""
        identity = services.link_identity(
            SYSTEM,
            customer=customer,
            channel=ChannelKind.INSTAGRAM,
            external_id="17841400000",
            display_name="@ali_uz",
        )

        assert identity.is_verified is False
        assert identity.verified_at is None

    def test_takroriy_boglash_yangi_yozuv_yaratmaydi(self, customer: Customer) -> None:
        first = services.link_identity(
            SYSTEM, customer=customer, channel=ChannelKind.TELEGRAM, external_id="111"
        )
        second = services.link_identity(
            SYSTEM, customer=customer, channel=ChannelKind.TELEGRAM, external_id="111"
        )

        assert first.pk == second.pk
        assert CustomerIdentity.objects.filter(external_id="111").count() == 1

    def test_boshqa_mijozning_identityisini_olish_urinishi_qayd_etiladi(self) -> None:
        """Identity o'g'irlash urinishi xavfsizlik jurnaliga tushadi."""
        owner = CustomerFactory()
        attacker = CustomerFactory()
        services.link_identity(
            SYSTEM, customer=owner, channel=ChannelKind.TELEGRAM, external_id="999"
        )

        result = services.link_identity(
            SYSTEM, customer=attacker, channel=ChannelKind.TELEGRAM, external_id="999"
        )

        # Identity ko'chirilmaydi — asl egasida qoladi
        assert result.customer_id == owner.pk

        event = AuditEvent.objects.filter(
            event_type=AuditEventType.SUSPICIOUS_INPUT, severity=AuditSeverity.SECURITY
        ).first()
        assert event is not None
        assert event.payload["existing_customer_ref"] == owner.pk


class TestFindByChannel:
    def test_kanal_identifikatori_boyicha_topiladi(self, customer: Customer) -> None:
        services.link_identity(
            SYSTEM, customer=customer, channel=ChannelKind.INSTAGRAM, external_id="ig-1"
        )

        found = services.find_by_channel(ChannelKind.INSTAGRAM, "ig-1")

        assert found is not None
        assert found.pk == customer.pk

    def test_mavjud_bolmagan_identifikator_none_qaytaradi(self) -> None:
        assert services.find_by_channel(ChannelKind.TELEGRAM, "yoq") is None

    def test_kanallar_aralashmaydi(self, customer: Customer) -> None:
        """Bir xil `external_id` turli kanallarda turli odam bo'lishi mumkin."""
        services.link_identity(
            SYSTEM, customer=customer, channel=ChannelKind.TELEGRAM, external_id="555"
        )

        assert services.find_by_channel(ChannelKind.INSTAGRAM, "555") is None


class TestMarkIdentityVerified:
    def test_tasdiqlash_vaqt_tamgasini_qoyadi(self, customer: Customer) -> None:
        identity = services.link_identity(
            SYSTEM, customer=customer, channel=ChannelKind.INSTAGRAM, external_id="ig-2"
        )

        verified = services.mark_identity_verified(SYSTEM, identity_id=identity.pk)

        assert verified.is_verified is True
        assert verified.verified_at is not None

    def test_qayta_tasdiqlash_vaqtni_ozgartirmaydi(self, customer: Customer) -> None:
        identity = services.link_identity(
            SYSTEM,
            customer=customer,
            channel=ChannelKind.TELEGRAM,
            external_id="777",
            is_verified=True,
        )
        original_time = identity.verified_at

        again = services.mark_identity_verified(SYSTEM, identity_id=identity.pk)

        assert again.verified_at == original_time


class TestCustomerConstraints:
    """Baza darajasidagi kafolatlar."""

    def test_bir_kanalda_bir_identifikator_yagona(self, customer: Customer) -> None:
        """Identity Resolution poydevori: aks holda suhbat kimga tegishli noaniq."""
        CustomerIdentity.objects.create(
            customer=customer, channel=ChannelKind.TELEGRAM, external_id="dup"
        )

        with pytest.raises(IntegrityError), transaction.atomic():
            CustomerIdentity.objects.create(
                customer=CustomerFactory(), channel=ChannelKind.TELEGRAM, external_id="dup"
            )

    def test_tasdiqlangan_identity_vaqt_tamgasisiz_bolmaydi(self, customer: Customer) -> None:
        """STRIDE-R: "kim, qachon tasdiqladi" savoliga javob bo'lishi shart."""
        with pytest.raises(IntegrityError), transaction.atomic():
            CustomerIdentity.objects.create(
                customer=customer,
                channel=ChannelKind.TELEGRAM,
                external_id="x",
                is_verified=True,
                verified_at=None,
            )

    def test_qora_royxatda_sabab_majburiy(self) -> None:
        """Asossiz bloklash taqiqlanadi — qaror asoslanishi kerak."""
        with pytest.raises(IntegrityError), transaction.atomic():
            Customer.objects.create(full_name="Test", is_blacklisted=True, blacklist_reason="")

    def test_bosh_raqamlar_unique_cheklovga_tushmaydi(self) -> None:
        """Instagram lead'ida raqam hali yo'q — ikkita bo'sh raqam mumkin."""
        Customer.objects.create(full_name="Lead 1", primary_phone="")
        Customer.objects.create(full_name="Lead 2", primary_phone="")

        assert Customer.objects.filter(primary_phone="").count() == 2
