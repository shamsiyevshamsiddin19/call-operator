"""Mavjudlik so'rovlari testlari.

Bu funksiyalar AI ning eng ko'p chaqiradigan amallari: har qo'ng'iroqda
"bu mahsulot bormi?" savoliga javob beradi. Shu sababli ular ikki talabga
javob berishi shart:

* **to'g'ri** — noto'g'ri javob ortiqcha sotuvga yoki yo'qotilgan savdoga olib keladi;
* **tez** — tool chaqiruvi byudjeti 100 ms, N+1 so'rov bu byudjetni buzadi.
"""

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext

from apps.accounts.actors import Actor
from apps.inventory import selectors, services
from apps.inventory.models import Stock
from tests.factories import ProductFactory, StockFactory, WarehouseFactory

pytestmark = pytest.mark.django_db


class TestGetAvailability:
    def test_mavjud_miqdor_rezervni_hisobga_oladi(self, stock: Stock, ai_actor: Actor) -> None:
        services.reserve_stock(ai_actor, stock_id=stock.pk, quantity=4)

        available = selectors.get_availability(stock.product_id)

        assert available == 6, "available = on_hand - reserved"

    def test_bir_nechta_ombor_yigindisi_qaytariladi(self) -> None:
        """Ko'p omborli holatda mijozga umumiy mavjudlik aytiladi."""
        product = ProductFactory()
        StockFactory(product=product, warehouse=WarehouseFactory(), on_hand=10)
        StockFactory(product=product, warehouse=WarehouseFactory(), on_hand=5)

        assert selectors.get_availability(product.pk) == 15

    def test_muayyan_ombor_boyicha_filtrlanadi(self) -> None:
        product = ProductFactory()
        first_warehouse = WarehouseFactory()
        StockFactory(product=product, warehouse=first_warehouse, on_hand=10)
        StockFactory(product=product, warehouse=WarehouseFactory(), on_hand=5)

        result = selectors.get_availability(product.pk, warehouse_id=first_warehouse.pk)

        assert result == 10

    def test_zaxira_qatori_yoq_mahsulot_nol_qaytaradi(self) -> None:
        """`None` emas, `0`: chaqiruvchi kod qo'shimcha tekshiruv yozmasligi uchun."""
        product = ProductFactory()

        assert selectors.get_availability(product.pk) == 0


class TestGetAvailabilityMap:
    """N+1 oldini olish — kechikish byudjeti uchun hayotiy."""

    def test_bir_nechta_mahsulot_bitta_sorovda_olinadi(self) -> None:
        """10 qatorli buyurtma 10 so'rovga aylanmasligi kerak."""
        warehouse = WarehouseFactory()
        products = [ProductFactory() for _ in range(10)]
        for index, product in enumerate(products, start=1):
            StockFactory(product=product, warehouse=warehouse, on_hand=index)

        with CaptureQueriesContext(connection) as captured:
            result = selectors.get_availability_map([p.pk for p in products])

        assert len(captured) == 1, f"bitta so'rov kutilgan, {len(captured)} ta bajarildi"
        assert len(result) == 10
        assert result[products[4].pk] == 5

    def test_rezerv_hisobga_olinadi(self, stock: Stock, ai_actor: Actor) -> None:
        services.reserve_stock(ai_actor, stock_id=stock.pk, quantity=3)

        result = selectors.get_availability_map([stock.product_id])

        assert result[stock.product_id] == 7

    def test_zaxirasi_yoq_mahsulot_royxatda_bolmaydi(self) -> None:
        """Natijada yo'q kalit "zaxira qatori yaratilmagan" degani."""
        with_stock = StockFactory(on_hand=5)
        without_stock = ProductFactory()

        result = selectors.get_availability_map([with_stock.product_id, without_stock.pk])

        assert result[with_stock.product_id] == 5
        assert without_stock.pk not in result

    def test_bosh_royxat_bosh_natija_beradi(self) -> None:
        assert selectors.get_availability_map([]) == {}


class TestStocksWithAvailability:
    def test_sql_darajasida_filtrlash_mumkin(self) -> None:
        """Annotatsiya kerak, chunki `available` property bilan filtrlab bo'lmaydi."""
        warehouse = WarehouseFactory()
        StockFactory(product=ProductFactory(), warehouse=warehouse, on_hand=10, reserved=10)
        StockFactory(product=ProductFactory(), warehouse=warehouse, on_hand=10, reserved=2)

        # `type: ignore[misc]` — django-stubs `annotate()` bilan qo'shilgan maydon
        # nomlarini kuzatmaydi (stublar cheklovi, kod xatosi emas).
        in_stock = selectors.stocks_with_availability().filter(available_qty__gt=0)  # type: ignore[misc]

        assert in_stock.count() == 1

    def test_annotatsiya_property_bilan_bir_xil_qiymat_beradi(self, stock: Stock) -> None:
        """Ikki yo'l bir xil natija berishi shart, aks holda hisobot chalkashadi."""
        Stock.objects.filter(pk=stock.pk).update(on_hand=10, reserved=3)

        annotated = selectors.stocks_with_availability().get(pk=stock.pk)
        stock.refresh_from_db()

        assert annotated.available_qty == stock.available == 7  # type: ignore[attr-defined]


class TestLowStockItems:
    def test_minimal_zaxiradan_pastga_tushganlar_qaytariladi(self) -> None:
        warehouse = WarehouseFactory()
        low = StockFactory(
            product=ProductFactory(), warehouse=warehouse, on_hand=2, reorder_level=5
        )
        StockFactory(product=ProductFactory(), warehouse=warehouse, on_hand=20, reorder_level=5)

        results = list(selectors.get_low_stock_items())

        assert [item.pk for item in results] == [low.pk]

    def test_rezerv_hisobga_olinadi(self) -> None:
        """Jismonan tovar bor, lekin hammasi rezervda — bu ham kam zaxira."""
        stock = StockFactory(on_hand=10, reserved=8, reorder_level=5)

        results = list(selectors.get_low_stock_items())

        assert stock.pk in [item.pk for item in results]

    def test_eng_kam_zaxira_birinchi_turadi(self) -> None:
        warehouse = WarehouseFactory()
        StockFactory(product=ProductFactory(), warehouse=warehouse, on_hand=4, reorder_level=5)
        StockFactory(product=ProductFactory(), warehouse=warehouse, on_hand=1, reorder_level=5)

        results = list(selectors.get_low_stock_items())

        assert results[0].available <= results[1].available


class TestAdjustStock:
    """Inventarizatsiya tuzatishi — ombor xodimi huquqida."""

    def test_musbat_tuzatish_qoldiqni_oshiradi(self, stock: Stock, warehouse_actor: Actor) -> None:
        services.adjust_stock(
            warehouse_actor, stock_id=stock.pk, delta=3, reason="Sanoqda ortiq topildi"
        )

        stock.refresh_from_db()
        assert stock.on_hand == 13

    def test_manfiy_tuzatish_qoldiqni_kamaytiradi(
        self, stock: Stock, warehouse_actor: Actor
    ) -> None:
        services.adjust_stock(
            warehouse_actor, stock_id=stock.pk, delta=-4, reason="Sanoqda kam topildi"
        )

        stock.refresh_from_db()
        assert stock.on_hand == 6

    def test_tuzatish_daftarda_alohida_tur_bilan_qoladi(
        self, stock: Stock, warehouse_actor: Actor
    ) -> None:
        """ "Qoldiq nega o'zgardi?" savoliga javob har doim mavjud bo'lishi kerak."""
        movement = services.adjust_stock(
            warehouse_actor, stock_id=stock.pk, delta=2, reason="Inventarizatsiya"
        )

        assert movement.kind == "ADJUSTMENT"
        assert movement.reason == "Inventarizatsiya"
        assert movement.actor_label == warehouse_actor.label

    def test_nol_tuzatish_rad_etiladi(self, stock: Stock, warehouse_actor: Actor) -> None:
        with pytest.raises(ValueError, match="nolga teng"):
            services.adjust_stock(warehouse_actor, stock_id=stock.pk, delta=0, reason="Test")

    def test_tuzatish_daftar_mosligini_saqlaydi(self, warehouse_actor: Actor) -> None:
        stock = StockFactory(on_hand=0)
        services.receive_stock(warehouse_actor, stock_id=stock.pk, quantity=10, reason="Kirim")
        services.adjust_stock(warehouse_actor, stock_id=stock.pk, delta=-2, reason="Buzilgan")

        result = selectors.verify_ledger_consistency(stock.pk)

        assert result.is_consistent
        assert result.stock_on_hand == 8


class TestReleaseReservationEdgeCases:
    def test_rezervdan_ortiq_bekor_qilish_mavjud_bilan_chegaralanadi(
        self, stock: Stock, ai_actor: Actor
    ) -> None:
        """Hisobni buzmaslik uchun: 10 so'ralsa, 3 bor bo'lsa — 3 qaytariladi."""
        services.reserve_stock(ai_actor, stock_id=stock.pk, quantity=3)

        movement = services.release_reservation(ai_actor, stock_id=stock.pk, quantity=10)

        assert movement.reserved_delta == -3
        stock.refresh_from_db()
        assert stock.reserved == 0

    def test_rezervsiz_bekor_qilish_xatolik_beradi(self, stock: Stock, ai_actor: Actor) -> None:
        with pytest.raises(ValueError, match="rezerv yo'q"):
            services.release_reservation(ai_actor, stock_id=stock.pk, quantity=1)

    @pytest.mark.parametrize("bad_quantity", [0, -5])
    def test_nol_va_manfiy_miqdor_rad_etiladi(
        self, stock: Stock, ai_actor: Actor, bad_quantity: int
    ) -> None:
        with pytest.raises(ValueError, match="musbat"):
            services.release_reservation(ai_actor, stock_id=stock.pk, quantity=bad_quantity)


class TestIssueStockValidation:
    @pytest.mark.parametrize("bad_quantity", [0, -3])
    def test_nol_va_manfiy_chiqim_rad_etiladi(
        self, stock: Stock, ai_actor: Actor, bad_quantity: int
    ) -> None:
        with pytest.raises(ValueError, match="musbat"):
            services.issue_stock(ai_actor, stock_id=stock.pk, quantity=bad_quantity)


class TestReceiveStockValidation:
    @pytest.mark.parametrize("bad_quantity", [0, -7])
    def test_nol_va_manfiy_kirim_rad_etiladi(
        self, stock: Stock, warehouse_actor: Actor, bad_quantity: int
    ) -> None:
        with pytest.raises(ValueError, match="musbat"):
            services.receive_stock(
                warehouse_actor, stock_id=stock.pk, quantity=bad_quantity, reason="Test"
            )
