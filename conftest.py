"""Umumiy test fixtures.

`tests/factories.py` obyekt yaratishni, bu modul esa testlarda eng ko'p
takrorlanadigan **sahnalarni** beradi (ombor + mahsulot + zaxira birgalikda).
"""

from collections.abc import Iterator

import pytest
from django.db import connection

from apps.accounts.actors import Actor
from apps.accounts.models import StaffRole
from apps.catalog.models import Product
from apps.customers.models import Customer
from apps.inventory.models import Stock
from tests.factories import (
    CustomerFactory,
    ProductFactory,
    StockFactory,
    UserFactory,
    WarehouseFactory,
)


@pytest.fixture
def customer() -> Customer:
    return CustomerFactory()


@pytest.fixture
def product() -> Product:
    return ProductFactory()


@pytest.fixture
def stock(product: Product) -> Stock:
    """10 dona zaxira bilan bitta pozitsiya (standart sahna)."""
    return StockFactory(product=product, on_hand=10, reserved=0)


@pytest.fixture
def ai_actor(customer: Customer) -> Actor:
    """AI operator aktori — shu mijoz bilan suhbatda."""
    return Actor.ai(
        customer_id=customer.pk,
        conversation_id=1,
        channel="VOICE",
        trace_id="test-trace",
    )


@pytest.fixture
def warehouse_actor() -> Actor:
    """Ombor xodimi — zaxira kiritish huquqiga ega."""
    return Actor.staff(UserFactory(role=StaffRole.WAREHOUSE))


@pytest.fixture
def supervisor_actor() -> Actor:
    return Actor.staff(UserFactory(role=StaffRole.SUPERVISOR))


@pytest.fixture
def warehouse() -> object:
    return WarehouseFactory()


@pytest.fixture
def close_db_connections() -> Iterator[None]:
    """Poyga holati testlaridan keyin thread ulanishlarini yopadi.

    Parallel testlarda har thread o'z DB ulanishini ochadi. Ular yopilmasa
    PostgreSQL ulanish limiti tugaydi va keyingi testlar tushunarsiz
    sabab bilan yiqiladi.
    """
    yield
    connection.close()
