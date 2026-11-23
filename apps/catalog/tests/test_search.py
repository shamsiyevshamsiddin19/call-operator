"""Ovozli so'rov bo'yicha mahsulot izlash testlari.

**Nega bu testlar hayotiy.** Ovozli interfeysda mijozning aytgani matnga
aylantirilganda xato bo'lishi tabiiy: "samsung galaksi", "aypon o'n besh".
Agar izlash buni topmasa, AI "bunday mahsulot yo'q" deydi va sotuv yo'qoladi —
mahsulot esa katalogda turgan bo'ladi.

Bu testlar `pg_trgm` kengaytmasi va `ProductSynonym` jadvalining **birgalikda**
ishlashini tekshiradi. Shu sababli ular haqiqiy PostgreSQL talab qiladi.
"""

from decimal import Decimal

import pytest

from apps.catalog import selectors
from apps.catalog.models import ProductSynonym, SynonymSource
from tests.factories import ProductFactory

pytestmark = pytest.mark.django_db


class TestExactMatch:
    def test_sku_boyicha_aniq_moslik_eng_yuqori_ball_oladi(self) -> None:
        target = ProductFactory(sku="IPH-15-128", name="iPhone 15 128GB")
        ProductFactory(sku="IPH-14-128", name="iPhone 14 128GB")

        results = selectors.search_products("IPH-15-128")

        assert results[0].sku == target.sku
        assert results[0].score == pytest.approx(1.0)

    def test_sku_registrga_sezgir_emas(self) -> None:
        ProductFactory(sku="IPH-15-128", name="iPhone 15")

        results = selectors.search_products("iph-15-128")

        assert len(results) >= 1
        assert results[0].sku == "IPH-15-128"


class TestTrigramSearch:
    """Imlo xatosiga chidamlilik — `pg_trgm`."""

    def test_imlo_xatosi_bilan_yozilgan_nom_topiladi(self) -> None:
        """Ovozdan matnga aylantirishda tipik xato."""
        ProductFactory(name="Samsung Galaxy A54")

        results = selectors.search_products("Samsung Galaxi A54")

        assert len(results) >= 1
        assert results[0].name == "Samsung Galaxy A54"

    def test_qisman_nom_boyicha_topiladi(self) -> None:
        ProductFactory(name="Samsung Galaxy A54 128GB")

        results = selectors.search_products("Samsung Galaxy A54")

        assert len(results) >= 1

    def test_butunlay_boshqa_sorov_hech_narsa_topmaydi(self) -> None:
        """Yolg'on moslik xavfli: AI noto'g'ri mahsulot narxini aytib qo'yardi."""
        ProductFactory(name="Samsung Galaxy A54")

        results = selectors.search_products("muzlatgich Atlant")

        assert results == []


class TestSynonymSearch:
    """Butunlay boshqa so'z — trigram topa olmaydigan holat."""

    def test_sinonim_boyicha_topiladi(self) -> None:
        product = ProductFactory(name="iPhone 15")
        ProductSynonym.objects.create(product=product, term="aypon 15")

        results = selectors.search_products("aypon 15")

        assert len(results) >= 1
        assert results[0].product_id == product.pk

    def test_rus_tilidagi_sinonim_ishlaydi(self) -> None:
        """Mijoz ruscha nom aytishi mumkin."""
        product = ProductFactory(name="Muzlatgich Artel")
        ProductSynonym.objects.create(product=product, term="холодильник Artel")

        results = selectors.search_products("холодильник Artel")

        assert len(results) >= 1
        assert results[0].product_id == product.pk

    def test_tasdiqlanmagan_ai_sinonimi_izlashda_qatnashmaydi(self) -> None:
        """AI o'rgangan sinonim nazoratchi tasdig'ini kutadi.

        AI suhbatdan noto'g'ri bog'lanish o'rganib olishi mumkin. Tasdiqlanmagan
        atama bo'yicha mahsulot ko'rsatilsa, AI mijozga butunlay boshqa
        mahsulotning narxini aytib qo'yardi.
        """
        product = ProductFactory(name="Televizor Artel")
        ProductSynonym.objects.create(
            product=product,
            term="plazma",
            source=SynonymSource.AI_LEARNED,
            is_approved=False,
        )

        results = selectors.search_products("plazma")

        assert results == [], "tasdiqlanmagan sinonim natija bermasligi kerak"

    def test_tasdiqlangandan_keyin_ai_sinonimi_ishlaydi(self) -> None:
        """Nazoratchi tasdiqlagandan keyin sinonim izlashga qo'shiladi."""
        product = ProductFactory(name="Televizor Artel")
        synonym = ProductSynonym.objects.create(
            product=product,
            term="plazma",
            source=SynonymSource.AI_LEARNED,
            is_approved=False,
        )

        assert selectors.search_products("plazma") == []

        synonym.is_approved = True
        synonym.save(update_fields=["is_approved"])

        results = selectors.search_products("plazma")
        assert len(results) == 1
        assert results[0].product_id == product.pk

    def test_bir_mahsulotning_bir_nechta_sinonimi_takroriy_natija_bermaydi(self) -> None:
        """`distinct()` JOIN tufayli takrorlanishni to'sadi."""
        product = ProductFactory(name="iPhone 15")
        for term in ("aypon 15", "ayfon 15", "iphone 15"):
            ProductSynonym.objects.create(product=product, term=term)

        results = selectors.search_products("aypon 15")

        product_ids = [match.product_id for match in results]
        assert len(product_ids) == len(set(product_ids)), "har mahsulot bir marta chiqishi kerak"


class TestCostPriceIsNeverExposed:
    """STRIDE-I: tannarx bu qatlamdan chiqmaydi.

    Himoya "frontendda yashirish" emas — tannarx natija strukturasida
    **umuman yo'q**, shuning uchun uni oshkor qilish texnik jihatdan imkonsiz.
    """

    def test_izlash_natijasida_tannarx_maydoni_yoq(self) -> None:
        ProductFactory(name="Test mahsulot", price=Decimal("100000"), cost_price=Decimal("70000"))

        results = selectors.search_products("Test mahsulot")

        assert len(results) >= 1
        assert not hasattr(results[0], "cost_price")
        assert "70000" not in str(results[0])

    def test_sotuv_malumotida_tannarx_yoq(self) -> None:
        product = ProductFactory(price=Decimal("100000"), cost_price=Decimal("70000"))

        result = selectors.get_product_for_sale(product.pk)

        assert result is not None
        assert result.price == Decimal("100000")
        assert not hasattr(result, "cost_price")

    def test_xodimlar_sorovida_marja_hisoblanadi(self) -> None:
        """Xodimlar yo'li alohida: u yerda tannarx va marja mavjud."""
        ProductFactory(price=Decimal("100000"), cost_price=Decimal("70000"))

        # `type: ignore[misc]` — django-stubs annotatsiya maydonlarini kuzatmaydi
        row = selectors.products_for_staff().values("margin").first()  # type: ignore[misc]

        assert row is not None
        assert row["margin"] == Decimal("30000")


class TestArchivedProducts:
    def test_arxivlangan_mahsulot_izlashda_chiqmaydi(self) -> None:
        ProductFactory(name="Eski model", is_active=False)

        results = selectors.search_products("Eski model")

        assert results == []

    def test_arxivlangan_mahsulot_sotuvga_berilmaydi(self) -> None:
        product = ProductFactory(is_active=False)

        assert selectors.get_product_for_sale(product.pk) is None

    def test_mavjud_bolmagan_mahsulot_none_qaytaradi(self) -> None:
        assert selectors.get_product_for_sale(999_999) is None


class TestSearchLimits:
    def test_natijalar_soni_chegaralanadi(self) -> None:
        """Ovozli suhbatda 3 dan ko'p variant mijozni chalkashtiradi (Hick qonuni)."""
        for i in range(10):
            ProductFactory(name=f"Samsung Galaxy A{50 + i}")

        results = selectors.search_products("Samsung Galaxy", limit=3)

        assert len(results) <= 3

    @pytest.mark.parametrize("empty_query", ["", "   ", "\t\n"])
    def test_bosh_sorov_bosh_royxat_qaytaradi(self, empty_query: str) -> None:
        ProductFactory(name="Mahsulot")

        assert selectors.search_products(empty_query) == []

    def test_natijalar_ball_boyicha_tartiblangan(self) -> None:
        ProductFactory(sku="EXACT-SKU", name="Boshqa nom")
        ProductFactory(name="Boshqa nomga yaqin")

        results = selectors.search_products("Boshqa nom")

        scores = [match.score for match in results]
        assert scores == sorted(scores, reverse=True)
