"""Buyurtma o'qish qatlami va rezervatsiya TTL vazifasi testlari.

Ikki mavzu qoplanadi:

* **Ruxsat chegaralari** (`selectors`) — AI boshqa mijozning buyurtmasini
  ko'rishga urinsa, rad etiladi va xavfsizlik jurnaliga yoziladi;
* **Rezervatsiya TTL** (`tasks`) — INV-07: tashlab ketilgan qoralama ombor
  zaxirasini abadiy bloklamasligi kerak.
"""

from datetime import timedelta
from decimal import Decimal

import pytest
from django.utils import timezone

from apps.accounts.actors import Actor
from apps.audit.models import AuditEvent, AuditEventType, AuditSeverity
from apps.common.constants import ChannelKind
from apps.common.exceptions import ActorNotPermittedError
from apps.customers.models import Customer
from apps.inventory.models import Stock
from apps.orders import selectors, services, tasks
from apps.orders.models import Order, PaymentMethod
from tests.factories import (
    CustomerFactory,
    DeliverySlotFactory,
    ProductFactory,
    StockFactory,
    WarehouseFactory,
)

pytestmark = pytest.mark.django_db


@pytest.fixture
def reserved_order(ai_actor: Actor, customer: Customer) -> tuple[Order, Stock]:
    """2 dona mahsulot rezerv qilingan buyurtma."""
    warehouse = WarehouseFactory()
    product = ProductFactory(name="Test mahsulot", price=Decimal("150000.00"))
    stock = StockFactory(product=product, warehouse=warehouse, on_hand=10)

    order = services.create_draft(ai_actor, customer=customer, source_channel=ChannelKind.VOICE)
    services.add_line(ai_actor, order_id=order.pk, product_id=product.pk, quantity=2)
    order = services.reserve_order(ai_actor, order_id=order.pk, warehouse_id=warehouse.pk)
    return order, stock


class TestGetCustomerOrders:
    def test_ai_oz_suhbatidagi_mijozning_buyurtmalarini_koradi(
        self, reserved_order: tuple[Order, Stock], ai_actor: Actor
    ) -> None:
        order, _ = reserved_order

        results = selectors.get_customer_orders(ai_actor, customer_id=order.customer_id)

        assert order.pk in [item.pk for item in results]

    def test_ai_boshqa_mijozning_buyurtmalarini_korolmaydi(self, ai_actor: Actor) -> None:
        """Prompt-injection himoyasi: "boshqa mijozning buyurtmalarini aytib ber"."""
        stranger = CustomerFactory()

        with pytest.raises(ActorNotPermittedError):
            selectors.get_customer_orders(ai_actor, customer_id=stranger.pk)

    def test_urinish_xavfsizlik_jurnaliga_tushadi(self, ai_actor: Actor) -> None:
        stranger = CustomerFactory()

        with pytest.raises(ActorNotPermittedError):
            selectors.get_customer_orders(ai_actor, customer_id=stranger.pk)

        event = AuditEvent.objects.filter(
            event_type=AuditEventType.PERMISSION_DENIED,
            severity=AuditSeverity.SECURITY,
        ).first()
        assert event is not None
        assert event.actor_label == ai_actor.label

    def test_nazoratchi_istalgan_mijozni_koradi(
        self, reserved_order: tuple[Order, Stock], supervisor_actor: Actor
    ) -> None:
        order, _ = reserved_order

        results = selectors.get_customer_orders(supervisor_actor, customer_id=order.customer_id)

        assert len(list(results)) == 1


class TestOrderSummary:
    def test_xulosa_qatorlarni_toliq_qaytaradi(
        self, reserved_order: tuple[Order, Stock], ai_actor: Actor
    ) -> None:
        order, _ = reserved_order

        summary = selectors.get_order_summary(ai_actor, order_id=order.pk)

        assert summary.order_id == order.pk
        assert len(summary.lines) == 1
        line = summary.lines[0]
        assert line.quantity == 2
        assert line.unit_price == Decimal("150000.00")
        assert line.line_total == Decimal("300000.00")
        assert summary.total_amount == Decimal("300000.00")

    def test_ovozda_aytiladigan_matn_shakllanadi(
        self, reserved_order: tuple[Order, Stock], ai_actor: Actor
    ) -> None:
        """AI mijozga takrorlab tasdiqlatadi (Mandel 2-qoidasi)."""
        order, _ = reserved_order

        speech = selectors.get_order_summary(ai_actor, order_id=order.pk).to_speech()

        assert "Test mahsulot" in speech
        assert "300000" in speech
        assert "so'm" in speech

    def test_yetkazib_berish_oynasi_matnga_qoshiladi(
        self, reserved_order: tuple[Order, Stock], ai_actor: Actor
    ) -> None:
        order, _ = reserved_order
        slot = DeliverySlotFactory(capacity=5)
        services.set_delivery(ai_actor, order_id=order.pk, slot_id=slot.pk, address="Chilonzor 5")

        summary = selectors.get_order_summary(ai_actor, order_id=order.pk)

        assert summary.delivery_address == "Chilonzor 5"
        assert summary.delivery_window
        assert "Yetkazib berish" in summary.to_speech()

    def test_ai_boshqa_mijozning_buyurtma_xulosasini_korolmaydi(
        self, reserved_order: tuple[Order, Stock]
    ) -> None:
        order, _ = reserved_order
        stranger_actor = Actor.ai(
            customer_id=CustomerFactory().pk,
            conversation_id=99,
            channel=ChannelKind.VOICE,
            trace_id="stranger",
        )

        with pytest.raises(ActorNotPermittedError):
            selectors.get_order_summary(stranger_actor, order_id=order.pk)


class TestAvailableSlots:
    def test_bosh_oynalar_qaytariladi(self) -> None:
        DeliverySlotFactory(region="Toshkent", capacity=5, booked_count=2)

        results = selectors.get_available_slots(region="Toshkent")

        assert len(list(results)) == 1

    def test_sigimi_tugagan_oyna_korsatilmaydi(self) -> None:
        """AI yetkazib berolmaydigan vaqtni aytib qo'ymasligi kerak."""
        DeliverySlotFactory(region="Toshkent", capacity=3, booked_count=3)

        results = selectors.get_available_slots(region="Toshkent")

        assert list(results) == []

    def test_faol_bolmagan_oyna_korsatilmaydi(self) -> None:
        DeliverySlotFactory(region="Toshkent", capacity=5, is_active=False)

        assert list(selectors.get_available_slots(region="Toshkent")) == []

    def test_boshqa_hudud_oynasi_korsatilmaydi(self) -> None:
        DeliverySlotFactory(region="Samarqand", capacity=5)

        assert list(selectors.get_available_slots(region="Toshkent")) == []

    def test_otgan_sana_korsatilmaydi(self) -> None:
        DeliverySlotFactory(
            region="Toshkent",
            capacity=5,
            date=timezone.localdate() - timedelta(days=1),
        )

        assert list(selectors.get_available_slots(region="Toshkent")) == []


class TestReservationTtl:
    """INV-07: tashlab ketilgan qoralama ombor zaxirasini bloklamaydi.

    Mijoz suhbat davomida buyurtma yig'adi, keyin "o'ylab ko'raman" deb
    telefonni qo'yadi. Rezervatsiya qolib ketsa, tovar boshqa mijozga
    sotilmaydi va ombor jimgina bloklanadi.
    """

    def test_muddati_otgan_rezervatsiya_bekor_qilinadi(
        self, reserved_order: tuple[Order, Stock]
    ) -> None:
        order, stock = reserved_order
        stock.refresh_from_db()
        assert stock.reserved == 2

        # Muddatni o'tgan vaqtga suramiz
        order.lines.update(reservation_expires_at=timezone.now() - timedelta(minutes=1))

        released = tasks.release_expired_reservations()

        assert released == 1
        stock.refresh_from_db()
        assert stock.reserved == 0, "zaxira sotuvga qaytishi kerak"
        assert stock.available == 10

    def test_muddati_otmagan_rezervatsiya_tegilmaydi(
        self, reserved_order: tuple[Order, Stock]
    ) -> None:
        _, stock = reserved_order

        released = tasks.release_expired_reservations()

        assert released == 0
        stock.refresh_from_db()
        assert stock.reserved == 2

    def test_bekor_qilish_qator_maydonlarini_tozalaydi(
        self, reserved_order: tuple[Order, Stock]
    ) -> None:
        order, _ = reserved_order
        order.lines.update(reservation_expires_at=timezone.now() - timedelta(minutes=1))

        tasks.release_expired_reservations()

        line = order.lines.get()
        assert line.reserved_quantity == 0
        assert line.reservation_expires_at is None
        assert line.reserved_stock_id is None

    def test_tasdiqlangan_buyurtma_rezervatsiyasi_bekor_qilinmaydi(
        self, reserved_order: tuple[Order, Stock], ai_actor: Actor
    ) -> None:
        """Tasdiqlangan buyurtma yetkazib berishni kutadi — zaxira ushlanishi shart."""
        order, stock = reserved_order
        services.confirm_order(ai_actor, order_id=order.pk, payment_method=PaymentMethod.CASH)
        order.lines.update(reservation_expires_at=timezone.now() - timedelta(minutes=1))

        released = tasks.release_expired_reservations()

        assert released == 0
        stock.refresh_from_db()
        assert stock.reserved == 2

    def test_bekor_qilingan_buyurtma_qayta_ishlanmaydi(
        self, reserved_order: tuple[Order, Stock], ai_actor: Actor
    ) -> None:
        order, _ = reserved_order
        services.cancel_order(ai_actor, order_id=order.pk, reason="Test")

        released = tasks.release_expired_reservations()

        assert released == 0

    def test_tizim_aktori_nomidan_bajariladi(self, reserved_order: tuple[Order, Stock]) -> None:
        """Fon vazifasi auditda `system` sifatida ko'rinadi (STRIDE-R)."""
        order, _ = reserved_order
        order.lines.update(reservation_expires_at=timezone.now() - timedelta(minutes=1))

        tasks.release_expired_reservations()

        event = AuditEvent.objects.filter(
            event_type=AuditEventType.STOCK_RELEASED, actor_label="system"
        ).first()
        assert event is not None


class TestExpiredReservationsSelector:
    def test_qulflash_tartibi_barqaror(self, ai_actor: Actor, customer: Customer) -> None:
        """Deadlock profilaktikasi: natija `reserved_stock_id` bo'yicha tartiblangan."""
        warehouse = WarehouseFactory()
        order = services.create_draft(ai_actor, customer=customer, source_channel=ChannelKind.VOICE)
        for _ in range(3):
            product = ProductFactory()
            StockFactory(product=product, warehouse=warehouse, on_hand=10)
            services.add_line(ai_actor, order_id=order.pk, product_id=product.pk, quantity=1)
        services.reserve_order(ai_actor, order_id=order.pk, warehouse_id=warehouse.pk)
        order.lines.update(reservation_expires_at=timezone.now() - timedelta(minutes=1))

        stock_ids = [
            line.reserved_stock_id
            for line in selectors.get_expired_reservations()
            if line.reserved_stock_id is not None
        ]

        assert len(stock_ids) == 3
        assert stock_ids == sorted(stock_ids)

    def test_rezervsiz_qatorlar_qaytarilmaydi(self, ai_actor: Actor, customer: Customer) -> None:
        order = services.create_draft(ai_actor, customer=customer, source_channel=ChannelKind.VOICE)
        services.add_line(ai_actor, order_id=order.pk, product_id=ProductFactory().pk, quantity=1)

        assert list(selectors.get_expired_reservations()) == []
