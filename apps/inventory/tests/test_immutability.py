"""Append-only kafolati testlari — INV-04, INV-10 (STRIDE-T).

**Nega bu testlar hayotiy.** Daftar tahrirlansa, u daftar bo'lmaydi: huquqni
suiiste'mol qilgan odam o'z izini o'chiradi, nizoda esa hech narsani
isbotlab bo'lmaydi.

Himoya ikki qatlamli va testlar **ikkisini alohida** tekshiradi:

1. Python (`ImmutableModel`) — `save()` / `delete()` chaqirilganda;
2. PostgreSQL trigger — ORM ni aylanib o'tganda (`QuerySet.update()`,
   `QuerySet.delete()`, xom SQL).

Faqat birinchi qatlamni tekshirish yetarli emas: aynan ikkinchi qatlam
haqiqiy kafolat beradi.
"""

import pytest
from django.db import DatabaseError, connection, transaction

from apps.accounts.actors import Actor
from apps.audit.models import AuditEvent, AuditEventType
from apps.common.models import ImmutableRecordError
from apps.inventory.models import MovementKind, Stock, StockMovement

pytestmark = pytest.mark.django_db


@pytest.fixture
def movement(stock: Stock) -> StockMovement:
    return StockMovement.objects.create(
        stock=stock,
        kind=MovementKind.RECEIPT,
        on_hand_delta=5,
        reserved_delta=0,
        actor_label="staff:1:WAREHOUSE",
        reason="Boshlang'ich kirim",
    )


class TestPythonLayerProtection:
    """Birinchi qatlam: `ImmutableModel`."""

    def test_mavjud_yozuvni_saqlash_taqiqlanadi(self, movement: StockMovement) -> None:
        movement.reason = "o'zgartirilgan sabab"

        with pytest.raises(ImmutableRecordError):
            movement.save()

    def test_yozuvni_ochirish_taqiqlanadi(self, movement: StockMovement) -> None:
        with pytest.raises(ImmutableRecordError):
            movement.delete()

    def test_xatolik_nima_qilish_kerakligini_tushuntiradi(self, movement: StockMovement) -> None:
        """Xatolik xabari yo'l ko'rsatishi kerak, shunchaki rad etmasligi."""
        with pytest.raises(ImmutableRecordError) as exc_info:
            movement.delete()

        message = str(exc_info.value)
        assert "append-only" in message
        assert "teskari yozuv" in message


class TestDatabaseTriggerProtection:
    """Ikkinchi qatlam: PL/pgSQL trigger — haqiqiy kafolat.

    Bu testlar ORM ni ataylab aylanib o'tadi. Agar trigger bo'lmasa,
    quyidagi amallar **jimgina muvaffaqiyatli** bajarilib, daftarni buzardi.
    """

    def test_queryset_update_trigger_bilan_toxtatiladi(self, movement: StockMovement) -> None:
        """`QuerySet.update()` `save()` ni chaqirmaydi — Python himoyasi ishlamaydi."""
        with pytest.raises(DatabaseError, match="APPEND_ONLY"), transaction.atomic():
            StockMovement.objects.filter(pk=movement.pk).update(on_hand_delta=9999)

    def test_queryset_delete_trigger_bilan_toxtatiladi(self, movement: StockMovement) -> None:
        with pytest.raises(DatabaseError, match="APPEND_ONLY"), transaction.atomic():
            StockMovement.objects.filter(pk=movement.pk).delete()

    def test_xom_sql_ham_toxtatiladi(self, movement: StockMovement) -> None:
        """Eng muhim test: to'g'ridan-to'g'ri SQL ham aylanib o'tolmaydi."""
        with (
            pytest.raises(DatabaseError, match="APPEND_ONLY"),
            transaction.atomic(),
            connection.cursor() as cursor,
        ):
            cursor.execute(
                "UPDATE inventory_stockmovement SET on_hand_delta = 9999 WHERE id = %s",
                [movement.pk],
            )

    def test_yozuv_qoshish_erkin_ishlaydi(self, stock: Stock) -> None:
        """Trigger faqat UPDATE va DELETE ni to'sadi — INSERT erkin."""
        StockMovement.objects.create(
            stock=stock,
            kind=MovementKind.RESERVE,
            on_hand_delta=0,
            reserved_delta=1,
            actor_label="test",
        )
        assert StockMovement.objects.filter(stock=stock).count() == 1

    def test_malumot_ozgarmagan_holida_qoladi(self, movement: StockMovement) -> None:
        """Muvaffaqiyatsiz urinishdan keyin yozuv asl holida bo'lishi shart."""
        with pytest.raises(DatabaseError), transaction.atomic():
            StockMovement.objects.filter(pk=movement.pk).update(on_hand_delta=9999)

        movement.refresh_from_db()
        assert movement.on_hand_delta == 5


class TestAuditLogImmutability:
    """Audit jurnali ham o'zgarmas (STRIDE-T).

    Agar audit jurnalini tahrirlash mumkin bo'lsa, u audit emas.
    """

    @pytest.fixture
    def event(self, ai_actor: Actor) -> AuditEvent:
        return AuditEvent.objects.create(
            event_type=AuditEventType.ORDER_CREATED,
            actor_label=ai_actor.label,
            summary="Test hodisasi",
        )

    def test_audit_yozuvini_tahrirlash_taqiqlanadi(self, event: AuditEvent) -> None:
        with pytest.raises(DatabaseError, match="APPEND_ONLY"), transaction.atomic():
            AuditEvent.objects.filter(pk=event.pk).update(summary="izni yashirish")

    def test_audit_yozuvini_ochirish_taqiqlanadi(self, event: AuditEvent) -> None:
        with pytest.raises(DatabaseError, match="APPEND_ONLY"), transaction.atomic():
            AuditEvent.objects.filter(pk=event.pk).delete()


class TestReversalPattern:
    """Tuzatish yo'li: teskari yozuv (storno).

    Xato yozuvni o'chirish o'rniga teskari yo'nalishdagi yangi yozuv
    qo'shiladi va `reversal_of` orqali aslga bog'lanadi. Shunda "kim, qachon,
    nega tuzatdi" savoliga javob qoladi.
    """

    def test_teskari_yozuv_aslga_boglanadi(self, movement: StockMovement, stock: Stock) -> None:
        reversal = StockMovement.objects.create(
            stock=stock,
            kind=MovementKind.ADJUSTMENT,
            on_hand_delta=-movement.on_hand_delta,
            reserved_delta=-movement.reserved_delta,
            actor_label="staff:1:WAREHOUSE",
            reason="Xato kirim tuzatildi",
            reversal_of=movement,
        )

        assert reversal.reversal_of_id == movement.pk
        assert movement.reversals.count() == 1
        # Ikki yozuvning yig'indisi nol — ta'sir bekor qilindi
        assert movement.on_hand_delta + reversal.on_hand_delta == 0

    def test_asl_yozuv_saqlanib_qoladi(self, movement: StockMovement, stock: Stock) -> None:
        """Tuzatishdan keyin ham asl yozuv daftarda qoladi — bu audit talabi."""
        StockMovement.objects.create(
            stock=stock,
            kind=MovementKind.ADJUSTMENT,
            on_hand_delta=-5,
            reserved_delta=0,
            actor_label="test",
            reason="tuzatish",
            reversal_of=movement,
        )

        assert StockMovement.objects.filter(pk=movement.pk).exists()
        assert StockMovement.objects.count() == 2
