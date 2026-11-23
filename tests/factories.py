"""Test ma'lumotlari fabrikalari (Factory Method — GoF, konstitutsiya 2.5).

Test ma'lumotini qo'lda yaratish (`Product.objects.create(...)` ni har testda
takrorlash) uch muammo keltiradi: takrorlanish, model o'zgarganda barcha
testlarni tuzatish zarurati va "nima muhim" degan savolning yo'qolishi.
Fabrika esa faqat testga **muhim** maydonlarni ko'rsatishga imkon beradi.
"""

from datetime import time
from decimal import Decimal

import factory
from django.utils import timezone
from factory.django import DjangoModelFactory

from apps.accounts.models import StaffRole, User
from apps.catalog.models import Category, Product, Unit
from apps.customers.models import Customer
from apps.inventory.models import Stock, Warehouse
from apps.orders.models import DeliverySlot


class UserFactory(DjangoModelFactory[User]):
    class Meta:
        model = User

    username = factory.Sequence(lambda n: f"xodim{n}")
    role = StaffRole.HUMAN_OPERATOR
    is_approved = True


class CustomerFactory(DjangoModelFactory[Customer]):
    class Meta:
        model = Customer

    full_name = factory.Sequence(lambda n: f"Mijoz {n}")
    # Har fabrika chaqiruvida yangi, E.164 formatga mos raqam
    primary_phone = factory.Sequence(lambda n: f"+99890{1234567 + n:07d}"[:13])


class CategoryFactory(DjangoModelFactory[Category]):
    class Meta:
        model = Category

    name = factory.Sequence(lambda n: f"Kategoriya {n}")


class ProductFactory(DjangoModelFactory[Product]):
    class Meta:
        model = Product

    sku = factory.Sequence(lambda n: f"SKU-{n:05d}")
    name = factory.Sequence(lambda n: f"Mahsulot {n}")
    category = factory.SubFactory(CategoryFactory)
    unit = Unit.PIECE
    price = Decimal("120000.00")
    cost_price = Decimal("90000.00")


class WarehouseFactory(DjangoModelFactory[Warehouse]):
    class Meta:
        model = Warehouse

    code = factory.Sequence(lambda n: f"OMBOR-{n}")
    name = factory.Sequence(lambda n: f"Ombor {n}")


class StockFactory(DjangoModelFactory[Stock]):
    """Zaxira qatori.

    `on_hand` ataylab noldan katta: testlarning ko'pi mavjud zaxira bilan
    boshlanadi. Nol zaxira kerak bo'lsa test uni aniq ko'rsatadi
    (`StockFactory(on_hand=0)`) — shunda test maqsadi o'qiyotgan odamga ravshan.
    """

    class Meta:
        model = Stock

    warehouse = factory.SubFactory(WarehouseFactory)
    product = factory.SubFactory(ProductFactory)
    on_hand = 10
    reserved = 0
    reorder_level = 2


class DeliverySlotFactory(DjangoModelFactory[DeliverySlot]):
    class Meta:
        model = DeliverySlot

    # `localdate()` ishlatiladi, `date.today()` emas: loyihada naive vaqt
    # taqiqlangan (ruff DTZ) va Toshkent vaqt zonasi hisobga olinishi kerak.
    date = factory.LazyFunction(timezone.localdate)
    window_start = time(10, 0)
    window_end = time(12, 0)
    region = "Toshkent"
    capacity = 5
    booked_count = 0
