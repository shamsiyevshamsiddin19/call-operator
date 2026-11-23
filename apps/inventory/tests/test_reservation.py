"""Zaxira rezervatsiyasi testlari — INV-01, INV-02, INV-07.

Bu fayldagi testlar loyihaning eng qimmat xatosini oldini oladi: **ortiqcha
sotuv**. Agar AI omborda yo'q tovarni sotsa, mijoz kutadi, keyin qo'ng'iroq
qilib janjal qiladi va ketadi.
"""

import threading

import pytest
from django.db import connection, transaction
from django.db.utils import IntegrityError
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from apps.accounts.actors import Actor
from apps.common.exceptions import ActorNotPermittedError, InsufficientStockError
from apps.inventory import selectors, services
from apps.inventory.models import MovementKind, Stock, StockMovement

pytestmark = pytest.mark.django_db


class TestReserveStock:
    """Oddiy rezervatsiya mantiqi."""

    def test_rezervatsiya_mavjud_miqdorni_kamaytiradi(self, stock: Stock, ai_actor: Actor) -> None:
        services.reserve_stock(ai_actor, stock_id=stock.pk, quantity=3)

        stock.refresh_from_db()
        assert stock.on_hand == 10, "jismoniy qoldiq o'zgarmasligi kerak"
        assert stock.reserved == 3
        assert stock.available == 7

    def test_rezervatsiya_daftarga_yozuv_qoshadi(self, stock: Stock, ai_actor: Actor) -> None:
        movement = services.reserve_stock(ai_actor, stock_id=stock.pk, quantity=3)

        assert movement.kind == MovementKind.RESERVE
        assert movement.on_hand_delta == 0, "rezervatsiya jismoniy qoldiqqa tegmaydi"
        assert movement.reserved_delta == 3
        assert movement.actor_label == ai_actor.label
        assert movement.trace_id == ai_actor.trace_id

    def test_zaxiradan_ortiq_sorov_rad_etiladi(self, stock: Stock, ai_actor: Actor) -> None:
        with pytest.raises(InsufficientStockError) as exc_info:
            services.reserve_stock(ai_actor, stock_id=stock.pk, quantity=11)

        # Xatolik mijozga aytiladigan matnni o'z ichiga oladi
        assert exc_info.value.user_message
        assert exc_info.value.available == 10

        stock.refresh_from_db()
        assert stock.reserved == 0, "muvaffaqiyatsiz urinish holatni o'zgartirmasligi kerak"

    def test_rezerv_qilingan_miqdor_qayta_sotilmaydi(self, stock: Stock, ai_actor: Actor) -> None:
        """INV-02: available = on_hand - reserved."""
        services.reserve_stock(ai_actor, stock_id=stock.pk, quantity=8)

        with pytest.raises(InsufficientStockError):
            services.reserve_stock(ai_actor, stock_id=stock.pk, quantity=3)

    @pytest.mark.parametrize("bad_quantity", [0, -1, -100])
    def test_nol_va_manfiy_miqdor_rad_etiladi(
        self, stock: Stock, ai_actor: Actor, bad_quantity: int
    ) -> None:
        with pytest.raises(ValueError, match="musbat"):
            services.reserve_stock(ai_actor, stock_id=stock.pk, quantity=bad_quantity)


class TestLedgerConsistency:
    """Daftar va joriy qoldiq mosligi — arxitektura invarianti.

    `Stock` qiymatlari daftar yig'indisidan ajralib ketsa tizim noto'g'ri
    qoldiq aytadi. Shu sababli har amal ketma-ketligidan keyin tekshiriladi.
    """

    def test_bir_qator_amallardan_keyin_daftar_mos_keladi(
        self, stock: Stock, ai_actor: Actor, warehouse_actor: Actor
    ) -> None:
        services.receive_stock(warehouse_actor, stock_id=stock.pk, quantity=5, reason="Kirim")
        services.reserve_stock(ai_actor, stock_id=stock.pk, quantity=4)
        services.release_reservation(ai_actor, stock_id=stock.pk, quantity=1)
        services.reserve_stock(ai_actor, stock_id=stock.pk, quantity=2)
        services.issue_stock(ai_actor, stock_id=stock.pk, quantity=3)

        result = selectors.verify_ledger_consistency(stock.pk)
        assert result.is_consistent, (
            f"daftar ajralib ketdi: qoldiq {result.stock_on_hand} vs {result.ledger_on_hand}, "
            f"rezerv {result.stock_reserved} vs {result.ledger_reserved}"
        )

    def test_boshlangich_qoldiq_daftarsiz_yaratilsa_moslik_buziladi(self, stock: Stock) -> None:
        """Fabrika `on_hand=10` ni daftarsiz yaratadi — bu ataylab.

        Shu test moslik tekshiruvining **ishlayotganini** isbotlaydi: agar u
        har holatda `True` qaytarsa, foydasiz bo'lardi.
        """
        result = selectors.verify_ledger_consistency(stock.pk)
        assert not result.is_consistent
        assert result.stock_on_hand == 10
        assert result.ledger_on_hand == 0


class TestDatabaseConstraints:
    """Baza darajasidagi kafolatlar (INV-01, INV-02).

    Bu testlar dastur mantig'ini emas, **bazaning o'zini** tekshiradi:
    kodda xato bo'lsa ham qoldiq manfiyga tushmasligi kerak.
    """

    def test_manfiy_qoldiq_baza_darajasida_taqiqlanadi(self, stock: Stock) -> None:
        with pytest.raises(IntegrityError), transaction.atomic():
            Stock.objects.filter(pk=stock.pk).update(on_hand=-1)

    def test_rezerv_qoldiqdan_oshmasligi_taqiqlanadi(self, stock: Stock) -> None:
        with pytest.raises(IntegrityError), transaction.atomic():
            Stock.objects.filter(pk=stock.pk).update(reserved=11)

    def test_hech_narsani_ozgartirmaydigan_daftar_yozuvi_taqiqlanadi(self, stock: Stock) -> None:
        with pytest.raises(IntegrityError), transaction.atomic():
            StockMovement.objects.create(
                stock=stock,
                kind=MovementKind.ADJUSTMENT,
                on_hand_delta=0,
                reserved_delta=0,
                actor_label="test",
            )


class TestPermissions:
    """AI uchun yopiq amallar (STRIDE-E)."""

    def test_ai_zaxira_kirim_qilolmaydi(self, stock: Stock, ai_actor: Actor) -> None:
        with pytest.raises(ActorNotPermittedError):
            services.receive_stock(ai_actor, stock_id=stock.pk, quantity=5, reason="urinish")

    def test_ai_inventarizatsiya_tuzatishi_qilolmaydi(self, stock: Stock, ai_actor: Actor) -> None:
        with pytest.raises(ActorNotPermittedError):
            services.adjust_stock(ai_actor, stock_id=stock.pk, delta=100, reason="urinish")

    def test_ombor_xodimi_kirim_qilishi_mumkin(self, stock: Stock, warehouse_actor: Actor) -> None:
        services.receive_stock(warehouse_actor, stock_id=stock.pk, quantity=5, reason="Kirim")

        stock.refresh_from_db()
        assert stock.on_hand == 15

    def test_tuzatishda_sabab_majburiy(self, stock: Stock, warehouse_actor: Actor) -> None:
        with pytest.raises(ValueError, match="sabab"):
            services.adjust_stock(warehouse_actor, stock_id=stock.pk, delta=5, reason="")


class TestIssueStock:
    """Chiqim: faqat rezerv qilingan tovar beriladi."""

    def test_rezervlanmagan_tovar_chiqarilmaydi(self, stock: Stock, ai_actor: Actor) -> None:
        """Aks holda bir tovar ikki mijozga sotilgan bo'lardi."""
        with pytest.raises(InsufficientStockError):
            services.issue_stock(ai_actor, stock_id=stock.pk, quantity=1)

    def test_chiqim_ikki_maydonni_kamaytiradi(self, stock: Stock, ai_actor: Actor) -> None:
        services.reserve_stock(ai_actor, stock_id=stock.pk, quantity=4)
        services.issue_stock(ai_actor, stock_id=stock.pk, quantity=4)

        stock.refresh_from_db()
        assert stock.on_hand == 6
        assert stock.reserved == 0


@pytest.mark.concurrency
class TestConcurrency:
    """Poyga holatlari (race conditions) — konstitutsiya 8-band.

    `transaction=True` majburiy: testlar haqiqiy parallel tranzaksiyalarni
    ishlatadi, shuning uchun odatdagi test rollback'i ishlamaydi.
    """

    @pytest.mark.django_db(transaction=True)
    def test_parallel_rezervatsiya_ortiqcha_sotuvga_yol_qoymaydi(
        self, stock: Stock, ai_actor: Actor
    ) -> None:
        """Ikki mijoz bir vaqtda 3 dona so'raydi, zaxirada esa 5 dona.

        To'g'ri natija: **bittasi** oladi, ikkinchisi rad etiladi.
        Agar `select_for_update()` olib tashlansa, ikkisi ham `available=5`
        ni o'qib, jami 6 dona rezerv qiladi — bu ortiqcha sotuv.
        """
        Stock.objects.filter(pk=stock.pk).update(on_hand=5, reserved=0)

        outcomes: list[str] = []
        barrier = threading.Barrier(2)

        def worker() -> None:
            try:
                barrier.wait(timeout=5)  # ikki thread aynan bir vaqtda boshlanadi
                services.reserve_stock(ai_actor, stock_id=stock.pk, quantity=3)
                outcomes.append("ok")
            except InsufficientStockError:
                outcomes.append("rad etildi")
            finally:
                connection.close()

        threads = [threading.Thread(target=worker) for _ in range(2)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=15)

        assert outcomes.count("ok") == 1, f"aynan bitta rezervatsiya o'tishi kerak: {outcomes}"
        assert outcomes.count("rad etildi") == 1

        stock.refresh_from_db()
        assert stock.reserved == 3
        assert stock.available == 2, "ortiqcha sotuv sodir bo'lmasligi kerak"

    @pytest.mark.django_db(transaction=True)
    def test_parallel_rezervatsiyada_daftar_buzilmaydi(self, stock: Stock, ai_actor: Actor) -> None:
        """Besh parallel rezervatsiyadan keyin daftar yig'indisi mos kelishi shart."""
        Stock.objects.filter(pk=stock.pk).update(on_hand=10, reserved=0)

        def worker() -> None:
            try:
                services.reserve_stock(ai_actor, stock_id=stock.pk, quantity=2)
            except InsufficientStockError:
                pass
            finally:
                connection.close()

        threads = [threading.Thread(target=worker) for _ in range(5)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=15)

        stock.refresh_from_db()
        ledger_reserved = sum(
            StockMovement.objects.filter(stock_id=stock.pk).values_list("reserved_delta", flat=True)
        )
        assert stock.reserved == ledger_reserved
        assert stock.reserved <= 10


class TestAvailabilityDisclosure:
    """Qoldiqni oshkor qilish siyosati (§6, STRIDE-I).

    Aniq qoldiq faqat kam qolganda aytiladi — raqobatchi ombor hajmini
    bilib olmasligi uchun.
    """

    def test_kop_qoldiqda_aniq_son_aytilmaydi(self) -> None:
        result = selectors.describe_availability(100)

        assert result.is_available is True
        assert result.exact_quantity is None, "AI aniq sonni bilmasligi kerak"
        assert "100" not in result.message

    def test_kam_qoldiqda_aniq_son_aytiladi(self) -> None:
        result = selectors.describe_availability(3)

        assert result.is_available is True
        assert result.exact_quantity == 3
        assert "3" in result.message

    @pytest.mark.parametrize("available", [0, -1, -50])
    def test_qoldiq_yoq_holati(self, available: int) -> None:
        result = selectors.describe_availability(available)

        assert result.is_available is False
        assert result.exact_quantity is None

    @settings(max_examples=50, suppress_health_check=[HealthCheck.function_scoped_fixture])
    @given(st.integers(min_value=6, max_value=100_000))
    def test_chegaradan_yuqori_har_qanday_qoldiqda_son_sir_qoladi(self, available: int) -> None:
        """Property-based: chegaradan yuqori **har qanday** qiymatda aniq son chiqmaydi."""
        result = selectors.describe_availability(available)

        assert result.exact_quantity is None
        assert str(available) not in result.message
