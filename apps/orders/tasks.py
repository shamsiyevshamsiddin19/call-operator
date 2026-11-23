"""Buyurtma fon vazifalari (Celery).

**Nega rezervatsiya avtomatik bekor bo'lishi kerak (INV-07).**
Mijoz qo'ng'iroq davomida buyurtma yig'adi, keyin "o'ylab ko'raman" deb
telefonni qo'yadi. Rezervatsiya qolib ketsa, tovar boshqa mijozga sotilmaydi
va ombor jimgina bloklanadi. Bir kunda o'nlab shunday tashlab ketilgan
qoralama bilan omborning yarmi "sotilgan" bo'lib ko'rinadi.
"""

import logging
import uuid

from celery import shared_task
from django.db import DatabaseError, transaction

from apps.accounts.actors import Actor
from apps.inventory import services as inventory_services
from apps.orders import selectors

logger = logging.getLogger("call_operator")


@shared_task(name="orders.release_expired_reservations")
def release_expired_reservations() -> int:
    """Muddati o'tgan rezervatsiyalarni bekor qiladi va zaxirani qaytaradi.

    **Har qator alohida tranzaksiyada** bekor qilinadi. Hammasini bitta
    tranzaksiyaga o'rash xato bo'lardi: bitta qatordagi nosozlik (masalan
    qulf kutish vaqti tugashi) butun partiyani rollback qilardi va
    muddati o'tgan rezervatsiyalar abadiy qolib ketardi.

    Returns:
        Bekor qilingan qatorlar soni (monitoring uchun).
    """
    actor = Actor.system(trace_id=f"ttl-{uuid.uuid4().hex[:12]}")
    released = 0
    failed = 0

    # Ro'yxat oldindan materializatsiya qilinadi: sikl ichida qatorlar
    # o'zgaradi va kursor bo'ylab yurish natijani o'tkazib yuborishi mumkin.
    expired_lines = list(selectors.get_expired_reservations())

    for line in expired_lines:
        if line.reserved_stock_id is None:
            continue
        try:
            with transaction.atomic():
                inventory_services.release_reservation(
                    actor,
                    stock_id=line.reserved_stock_id,
                    quantity=line.reserved_quantity,
                    order_ref=line.order_id,
                    reason="Rezervatsiya muddati tugadi",
                )
                line.reserved_quantity = 0
                line.reservation_expires_at = None
                line.reserved_stock = None
                line.save(
                    update_fields=[
                        "reserved_quantity",
                        "reservation_expires_at",
                        "reserved_stock",
                        "updated_at",
                    ]
                )
            released += 1
        except DatabaseError:
            # Aniq tipdagi xatolik ushlanadi (`except Exception` taqiqlanadi).
            # Qulf kutish vaqti tugashi normal holat: qator shu paytda boshqa
            # tranzaksiyada ishlatilmoqda. Keyingi yurishda qayta urinib ko'riladi.
            failed += 1
            logger.warning(
                "Rezervatsiyani bekor qilish kechiktirildi",
                extra={"order_line": line.pk, "order_ref": line.order_id},
            )

    if released or failed:
        logger.info(
            "Muddati o'tgan rezervatsiyalar ishlandi",
            extra={"released": released, "deferred": failed, "trace_id": actor.trace_id},
        )
    return released
