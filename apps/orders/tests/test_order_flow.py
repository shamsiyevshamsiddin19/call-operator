"""Buyurtma oqimi va holat mashinasi testlari.

Bu fayl mijoz bilan suhbatning to'liq yo'lini qoplaydi: qoralama -> qator
qo'shish -> rezervatsiya -> yetkazib berish -> tasdiqlash, hamda bekor qilish.

**Eng muhim tekshiruv — atomarlik.** Ko'p qatorli buyurtmada bitta qatorga
zaxira yetmasa, butun rezervatsiya bekor bo'lishi shart. Yarim rezerv qilingan
buyurtma qolsa, AI mijozga "yarmini berolaman" degan chalkash holatga tushadi
va ombor qismi behuda bloklanadi.
"""

from decimal import Decimal

import pytest
from django.db import transaction
from django.db.utils import IntegrityError

from apps.accounts.actors import Actor
from apps.common.constants import ChannelKind
from apps.common.exceptions import (
    EmptyOrderError,
    InsufficientStockError,
    OrderStateError,
)
from apps.customers.models import Customer
from apps.inventory import selectors as inventory_selectors
from apps.inventory import services as inventory_services
from apps.inventory.models import Stock
from apps.orders import services
from apps.orders.models import Order, OrderLine, OrderStatus, PaymentMethod
from tests.factories import DeliverySlotFactory, ProductFactory, StockFactory, WarehouseFactory

pytestmark = pytest.mark.django_db


@pytest.fixture
def draft(ai_actor: Actor, customer: Customer) -> Order:
    return services.create_draft(ai_actor, customer=customer, source_channel=ChannelKind.VOICE)


class TestCreateDraft:
    def test_qoralama_bosh_va_zaxirani_ushlamaydi(self, draft: Order) -> None:
        """Mijoz fikrini o'zgartirsa ombor bloklanib qolmasligi kerak."""
        assert draft.status == OrderStatus.DRAFT
        assert draft.total_amount == Decimal("0")
        assert draft.lines.count() == 0

    def test_qoralama_kim_yaratganini_eslab_qoladi(self, draft: Order, ai_actor: Actor) -> None:
        """STRIDE-R: har amal aktorga bog'lanadi."""
        assert draft.created_by_label == ai_actor.label
        assert draft.conversation_ref == ai_actor.conversation_id
        assert draft.trace_id == ai_actor.trace_id


class TestAddLine:
    def test_narx_katalogdan_nusxa_olinadi(self, draft: Order, ai_actor: Actor) -> None:
        """Keyin katalog narxi o'zgarsa ham buyurtma summasi o'zgarmasligi shart."""
        product = ProductFactory(price=Decimal("150000.00"))

        line = services.add_line(ai_actor, order_id=draft.pk, product_id=product.pk, quantity=2)

        assert line.unit_price == Decimal("150000.00")

        # Katalog narxini oshiramiz
        product.price = Decimal("200000.00")
        product.save(update_fields=["price"])

        line.refresh_from_db()
        assert line.unit_price == Decimal("150000.00"), "nusxa o'zgarmasligi kerak"

    def test_qator_summasi_bazada_hisoblanadi(self, draft: Order, ai_actor: Actor) -> None:
        """`GeneratedField`: `quantity * unit_price` DB darajasida."""
        product = ProductFactory(price=Decimal("120000.00"))

        line = services.add_line(ai_actor, order_id=draft.pk, product_id=product.pk, quantity=3)

        line.refresh_from_db()
        assert line.line_total == Decimal("360000.00")

    def test_takroriy_mahsulot_yangi_qator_yaratmaydi(self, draft: Order, ai_actor: Actor) -> None:
        """INV-08: aks holda AI bir mahsulotni ikki marta sanab, noto'g'ri summa aytardi."""
        product = ProductFactory(price=Decimal("100000.00"))

        services.add_line(ai_actor, order_id=draft.pk, product_id=product.pk, quantity=2)
        services.add_line(ai_actor, order_id=draft.pk, product_id=product.pk, quantity=3)

        assert draft.lines.count() == 1
        line = draft.lines.get()
        assert line.quantity == 5

    def test_buyurtma_summasi_sql_agregatsiyada_yangilanadi(
        self, draft: Order, ai_actor: Actor
    ) -> None:
        """INV-03: summa qatorlar yig'indisiga teng."""
        services.add_line(
            ai_actor,
            order_id=draft.pk,
            product_id=ProductFactory(price=Decimal("100000.00")).pk,
            quantity=2,
        )
        services.add_line(
            ai_actor,
            order_id=draft.pk,
            product_id=ProductFactory(price=Decimal("50000.00")).pk,
            quantity=1,
        )

        draft.refresh_from_db()
        assert draft.total_amount == Decimal("250000.00")

    def test_arxivlangan_mahsulotga_buyurtma_olinmaydi(self, draft: Order, ai_actor: Actor) -> None:
        product = ProductFactory(is_active=False)

        with pytest.raises(ValueError, match="arxivlangan"):
            services.add_line(ai_actor, order_id=draft.pk, product_id=product.pk, quantity=1)

    @pytest.mark.parametrize("bad_quantity", [0, -1])
    def test_nol_va_manfiy_miqdor_rad_etiladi(
        self, draft: Order, ai_actor: Actor, bad_quantity: int
    ) -> None:
        with pytest.raises(ValueError, match="musbat"):
            services.add_line(
                ai_actor,
                order_id=draft.pk,
                product_id=ProductFactory().pk,
                quantity=bad_quantity,
            )

    def test_qoralama_bolmagan_buyurtmaga_qator_qoshilmaydi(
        self, draft: Order, ai_actor: Actor, warehouse: object
    ) -> None:
        product = ProductFactory()
        StockFactory(product=product, warehouse=warehouse, on_hand=10)
        services.add_line(ai_actor, order_id=draft.pk, product_id=product.pk, quantity=1)
        services.reserve_order(ai_actor, order_id=draft.pk, warehouse_id=warehouse.pk)  # type: ignore[attr-defined]

        with pytest.raises(OrderStateError):
            services.add_line(
                ai_actor, order_id=draft.pk, product_id=ProductFactory().pk, quantity=1
            )


class TestReserveOrder:
    def test_barcha_qatorlar_rezerv_qilinadi(self, draft: Order, ai_actor: Actor) -> None:
        warehouse = WarehouseFactory()
        products = [ProductFactory() for _ in range(3)]
        for product in products:
            StockFactory(product=product, warehouse=warehouse, on_hand=10)
            services.add_line(ai_actor, order_id=draft.pk, product_id=product.pk, quantity=2)

        order = services.reserve_order(ai_actor, order_id=draft.pk, warehouse_id=warehouse.pk)

        assert order.status == OrderStatus.RESERVED
        for line in order.lines.all():
            assert line.reserved_quantity == 2
            assert line.reservation_expires_at is not None
            assert line.reserved_stock_id is not None, "qaysi zaxiradan olingani saqlanishi kerak"

    def test_bir_qator_yetmasa_butun_rezervatsiya_bekor_boladi(
        self, draft: Order, ai_actor: Actor
    ) -> None:
        """**Atomarlik testi** — eng muhim tekshiruv.

        Birinchi mahsulotda zaxira yetarli, ikkinchisida yo'q. Natijada
        hech qanday rezervatsiya qolmasligi va birinchi mahsulot zaxirasi
        tegilmagan bo'lishi shart (tranzaksiya rollback).
        """
        warehouse = WarehouseFactory()
        rich_product = ProductFactory()
        poor_product = ProductFactory()
        rich_stock = StockFactory(product=rich_product, warehouse=warehouse, on_hand=10)
        poor_stock = StockFactory(product=poor_product, warehouse=warehouse, on_hand=1)

        services.add_line(ai_actor, order_id=draft.pk, product_id=rich_product.pk, quantity=2)
        services.add_line(ai_actor, order_id=draft.pk, product_id=poor_product.pk, quantity=5)

        with pytest.raises(InsufficientStockError):
            services.reserve_order(ai_actor, order_id=draft.pk, warehouse_id=warehouse.pk)

        draft.refresh_from_db()
        rich_stock.refresh_from_db()
        poor_stock.refresh_from_db()

        assert draft.status == OrderStatus.DRAFT, "holat o'zgarmasligi kerak"
        assert rich_stock.reserved == 0, "birinchi mahsulot zaxirasi tegilmasligi kerak"
        assert poor_stock.reserved == 0
        assert not draft.lines.filter(reserved_quantity__gt=0).exists()

    def test_bosh_buyurtma_rezerv_qilinmaydi(self, draft: Order, ai_actor: Actor) -> None:
        warehouse = WarehouseFactory()

        with pytest.raises(EmptyOrderError):
            services.reserve_order(ai_actor, order_id=draft.pk, warehouse_id=warehouse.pk)

    def test_zaxira_qatori_yoq_mahsulot_rad_etiladi(self, draft: Order, ai_actor: Actor) -> None:
        """Mahsulot katalogda bor, lekin omborga hech qachon kirmagan."""
        warehouse = WarehouseFactory()
        product = ProductFactory()
        services.add_line(ai_actor, order_id=draft.pk, product_id=product.pk, quantity=1)

        with pytest.raises(InsufficientStockError):
            services.reserve_order(ai_actor, order_id=draft.pk, warehouse_id=warehouse.pk)

    def test_rezervatsiya_daftar_mosligini_buzmaydi(
        self, draft: Order, ai_actor: Actor, warehouse_actor: Actor
    ) -> None:
        warehouse = WarehouseFactory()
        product = ProductFactory()
        stock = StockFactory(product=product, warehouse=warehouse, on_hand=0)

        # Zaxira daftar orqali to'ldiriladi, shunda moslik tekshirilishi mumkin.
        # Kirim ombor xodimi nomidan bajariladi: tizim aktori (Celery) zaxira
        # kiritish huquqiga ega emas - buzilgan fon vazifasi katta zarar
        # keltirmasligi uchun.
        inventory_services.receive_stock(
            warehouse_actor, stock_id=stock.pk, quantity=10, reason="Kirim"
        )
        services.add_line(ai_actor, order_id=draft.pk, product_id=product.pk, quantity=4)
        services.reserve_order(ai_actor, order_id=draft.pk, warehouse_id=warehouse.pk)

        result = inventory_selectors.verify_ledger_consistency(stock.pk)
        assert result.is_consistent


class TestOrderStateMachine:
    """Holat mashinasi: ruxsat etilmagan o'tishlar to'siladi."""

    @pytest.fixture
    def reserved_order(self, draft: Order, ai_actor: Actor) -> Order:
        warehouse = WarehouseFactory()
        product = ProductFactory(price=Decimal("100000.00"))
        StockFactory(product=product, warehouse=warehouse, on_hand=10)
        services.add_line(ai_actor, order_id=draft.pk, product_id=product.pk, quantity=2)
        return services.reserve_order(ai_actor, order_id=draft.pk, warehouse_id=warehouse.pk)

    def test_qoralamani_togridan_togri_tasdiqlash_mumkin_emas(
        self, draft: Order, ai_actor: Actor
    ) -> None:
        """Zaxira ushlanmagan buyurtmani tasdiqlash — ortiqcha sotuv yo'li."""
        with pytest.raises(OrderStateError):
            services.confirm_order(ai_actor, order_id=draft.pk, payment_method=PaymentMethod.CASH)

    def test_rezervdan_tasdiqlashga_otish_mumkin(
        self, reserved_order: Order, ai_actor: Actor
    ) -> None:
        order = services.confirm_order(
            ai_actor, order_id=reserved_order.pk, payment_method=PaymentMethod.CASH
        )

        assert order.status == OrderStatus.CONFIRMED
        assert order.payment_method == PaymentMethod.CASH

    def test_tasdiqlashda_tolov_usuli_majburiy(
        self, reserved_order: Order, ai_actor: Actor
    ) -> None:
        with pytest.raises(ValueError, match="to'lov usuli"):
            services.confirm_order(ai_actor, order_id=reserved_order.pk, payment_method="")

    def test_bekor_qilingan_buyurtma_qayta_tasdiqlanmaydi(
        self, reserved_order: Order, ai_actor: Actor
    ) -> None:
        services.cancel_order(ai_actor, order_id=reserved_order.pk, reason="Mijoz voz kechdi")

        with pytest.raises(OrderStateError):
            services.confirm_order(
                ai_actor, order_id=reserved_order.pk, payment_method=PaymentMethod.CASH
            )

    def test_holat_mashinasi_modelda_hamda_servisda_bir_xil(self, draft: Order) -> None:
        assert draft.can_transition_to(OrderStatus.RESERVED) is True
        assert draft.can_transition_to(OrderStatus.CONFIRMED) is False
        assert draft.can_transition_to(OrderStatus.CANCELLED) is True


class TestCancelOrder:
    @pytest.fixture
    def reserved_with_stock(self, draft: Order, ai_actor: Actor) -> tuple[Order, Stock]:
        warehouse = WarehouseFactory()
        product = ProductFactory()
        stock = StockFactory(product=product, warehouse=warehouse, on_hand=10)
        services.add_line(ai_actor, order_id=draft.pk, product_id=product.pk, quantity=4)
        order = services.reserve_order(ai_actor, order_id=draft.pk, warehouse_id=warehouse.pk)
        return order, stock

    def test_bekor_qilish_zaxirani_qaytaradi(
        self, reserved_with_stock: tuple[Order, Stock], ai_actor: Actor
    ) -> None:
        order, stock = reserved_with_stock
        stock.refresh_from_db()
        assert stock.reserved == 4

        services.cancel_order(ai_actor, order_id=order.pk, reason="Mijoz voz kechdi")

        stock.refresh_from_db()
        assert stock.reserved == 0, "zaxira sotuvga qaytishi kerak"
        assert stock.available == 10

    def test_bekor_qilishda_sabab_majburiy(
        self, reserved_with_stock: tuple[Order, Stock], ai_actor: Actor
    ) -> None:
        """Sababsiz yozuv nizoda hech narsani isbotlamaydi."""
        order, _ = reserved_with_stock

        with pytest.raises(ValueError, match="sabab"):
            services.cancel_order(ai_actor, order_id=order.pk, reason="")

    def test_bekor_qilish_sabab_va_vaqtni_yozadi(
        self, reserved_with_stock: tuple[Order, Stock], ai_actor: Actor
    ) -> None:
        order, _ = reserved_with_stock

        cancelled = services.cancel_order(ai_actor, order_id=order.pk, reason="Narx qimmat")

        assert cancelled.status == OrderStatus.CANCELLED
        assert cancelled.cancellation_reason == "Narx qimmat"
        assert cancelled.cancelled_at is not None

    def test_bekor_qilingan_buyurtmada_rezerv_qolmaydi(
        self, reserved_with_stock: tuple[Order, Stock], ai_actor: Actor
    ) -> None:
        order, _ = reserved_with_stock

        services.cancel_order(ai_actor, order_id=order.pk, reason="Test")

        for line in order.lines.all():
            assert line.reserved_quantity == 0
            assert line.reservation_expires_at is None
            assert line.reserved_stock_id is None


class TestRemoveLine:
    def test_qatorni_olib_tashlash_rezervni_qaytaradi(self, draft: Order, ai_actor: Actor) -> None:
        warehouse = WarehouseFactory()
        product = ProductFactory()
        stock = StockFactory(product=product, warehouse=warehouse, on_hand=10)
        services.add_line(ai_actor, order_id=draft.pk, product_id=product.pk, quantity=3)
        services.reserve_order(ai_actor, order_id=draft.pk, warehouse_id=warehouse.pk)

        line = draft.lines.get()
        services.remove_line(ai_actor, order_id=draft.pk, line_id=line.pk)

        stock.refresh_from_db()
        assert stock.reserved == 0
        assert draft.lines.count() == 0

    def test_qator_olingandan_keyin_summa_qayta_hisoblanadi(
        self, draft: Order, ai_actor: Actor
    ) -> None:
        product_a = ProductFactory(price=Decimal("100000.00"))
        product_b = ProductFactory(price=Decimal("50000.00"))
        services.add_line(ai_actor, order_id=draft.pk, product_id=product_a.pk, quantity=1)
        line_b = services.add_line(ai_actor, order_id=draft.pk, product_id=product_b.pk, quantity=1)

        services.remove_line(ai_actor, order_id=draft.pk, line_id=line_b.pk)

        draft.refresh_from_db()
        assert draft.total_amount == Decimal("100000.00")


class TestSetDelivery:
    @pytest.fixture
    def reserved_order(self, draft: Order, ai_actor: Actor) -> Order:
        warehouse = WarehouseFactory()
        product = ProductFactory()
        StockFactory(product=product, warehouse=warehouse, on_hand=10)
        services.add_line(ai_actor, order_id=draft.pk, product_id=product.pk, quantity=1)
        return services.reserve_order(ai_actor, order_id=draft.pk, warehouse_id=warehouse.pk)

    def test_yetkazib_berish_oynasi_band_qilinadi(
        self, reserved_order: Order, ai_actor: Actor
    ) -> None:
        slot = DeliverySlotFactory(capacity=5, booked_count=0)

        order = services.set_delivery(
            ai_actor,
            order_id=reserved_order.pk,
            slot_id=slot.pk,
            address="Toshkent, Chilonzor 5",
        )

        slot.refresh_from_db()
        assert order.delivery_slot_id == slot.pk
        assert slot.booked_count == 1
        assert slot.free_capacity == 4

    def test_sigimi_tugagan_oyna_rad_etiladi(self, reserved_order: Order, ai_actor: Actor) -> None:
        """AI mijozga yetkazib berolmaydigan vaqtni aytib qo'ymasligi kerak."""
        slot = DeliverySlotFactory(capacity=2, booked_count=2)

        with pytest.raises(InsufficientStockError):
            services.set_delivery(
                ai_actor, order_id=reserved_order.pk, slot_id=slot.pk, address="Manzil"
            )

    def test_manzil_majburiy(self, reserved_order: Order, ai_actor: Actor) -> None:
        slot = DeliverySlotFactory()

        with pytest.raises(ValueError, match="manzil"):
            services.set_delivery(ai_actor, order_id=reserved_order.pk, slot_id=slot.pk, address="")

    def test_oynani_ozgartirish_eskisini_boshatadi(
        self, reserved_order: Order, ai_actor: Actor
    ) -> None:
        first = DeliverySlotFactory(capacity=5)
        second = DeliverySlotFactory(capacity=5, region="Samarqand")

        services.set_delivery(
            ai_actor, order_id=reserved_order.pk, slot_id=first.pk, address="Manzil 1"
        )
        services.set_delivery(
            ai_actor, order_id=reserved_order.pk, slot_id=second.pk, address="Manzil 2"
        )

        first.refresh_from_db()
        second.refresh_from_db()
        assert first.booked_count == 0, "eski oyna bo'shatilishi kerak"
        assert second.booked_count == 1

    def test_ayni_oynani_qayta_belgilash_ikki_marta_sanalmaydi(
        self, reserved_order: Order, ai_actor: Actor
    ) -> None:
        slot = DeliverySlotFactory(capacity=5)

        services.set_delivery(
            ai_actor, order_id=reserved_order.pk, slot_id=slot.pk, address="Manzil 1"
        )
        services.set_delivery(
            ai_actor, order_id=reserved_order.pk, slot_id=slot.pk, address="Manzil 2 (tuzatildi)"
        )

        slot.refresh_from_db()
        assert slot.booked_count == 1

    def test_bekor_qilish_oynani_boshatadi(self, reserved_order: Order, ai_actor: Actor) -> None:
        slot = DeliverySlotFactory(capacity=5)
        services.set_delivery(
            ai_actor, order_id=reserved_order.pk, slot_id=slot.pk, address="Manzil"
        )

        services.cancel_order(ai_actor, order_id=reserved_order.pk, reason="Test")

        slot.refresh_from_db()
        assert slot.booked_count == 0


class TestOrderConstraints:
    """Baza darajasidagi kafolatlar."""

    def test_sigimdan_ortiq_band_qilish_bazada_taqiqlanadi(self) -> None:
        slot = DeliverySlotFactory(capacity=3, booked_count=3)

        with pytest.raises(IntegrityError), transaction.atomic():
            type(slot).objects.filter(pk=slot.pk).update(booked_count=4)

    def test_bir_buyurtmada_bir_mahsulot_ikki_qatorda_bolmaydi(
        self, draft: Order, ai_actor: Actor
    ) -> None:
        """INV-08 baza darajasida."""
        product = ProductFactory()
        services.add_line(ai_actor, order_id=draft.pk, product_id=product.pk, quantity=1)

        with pytest.raises(IntegrityError), transaction.atomic():
            OrderLine.objects.create(
                order=draft, product=product, quantity=1, unit_price=Decimal("1")
            )
