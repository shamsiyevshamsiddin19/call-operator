"""Buyurtma amallari (CQS — Command/Write qatlami).

**Deadlock profilaktikasi eng nozik joy.** Ko'p qatorli buyurtmani rezerv
qilganda bir nechta `Stock` qatori qulflanadi. Agar tartib tasodifiy bo'lsa,
ikki parallel buyurtma bir-birini kutib qolishi mumkin:

    Buyurtma A: SKU-9 ni qulfladi, endi SKU-5 ni so'raydi
    Buyurtma B: SKU-5 ni qulfladi, endi SKU-9 ni so'raydi   -> deadlock

Shuning uchun `reserve_order` barcha kerakli qatorlarni **bitta so'rovda,
`pk` o'sish tartibida** qulflaydi va shundan keyingina rezervatsiyaga o'tadi.

**Chegirma berish amali bu modulda yo'q** (INV-05). AI uchun u shunchaki
mavjud emas: prompt buzilsa ham chaqirib bo'lmaydigan funksiya zarar keltirmaydi.
"""

from datetime import timedelta
from decimal import Decimal

from django.conf import settings
from django.db import transaction
from django.db.models import F, Sum
from django.db.models.functions import Coalesce
from django.utils import timezone

from apps.accounts.actors import Actor, ActorKind
from apps.audit import services as audit
from apps.audit.models import AuditEventType
from apps.catalog.models import Product
from apps.common.exceptions import (
    EmptyOrderError,
    InsufficientStockError,
    OrderStateError,
)
from apps.customers.models import Customer
from apps.inventory import services as inventory_services
from apps.inventory.models import Stock
from apps.orders.models import DeliverySlot, Order, OrderLine, OrderStatus


@transaction.atomic
def create_draft(
    actor: Actor,
    *,
    customer: Customer,
    source_channel: str,
) -> Order:
    """Bo'sh qoralama buyurtma yaratadi.

    Qoralama hech qanday zaxirani ushlamaydi — mijoz suhbat davomida fikrini
    o'zgartirsa ombor bloklanib qolmaydi.
    """
    order = Order.objects.create(
        customer=customer,
        status=OrderStatus.DRAFT,
        source_channel=source_channel,
        created_by_label=actor.label,
        conversation_ref=actor.conversation_id,
        trace_id=actor.trace_id,
    )

    audit.record_event(
        actor,
        AuditEventType.ORDER_CREATED,
        f"Qoralama buyurtma yaratildi (kanal: {source_channel})",
        customer_ref=customer.pk,
        order_ref=order.pk,
        payload={"source_channel": source_channel},
    )
    return order


@transaction.atomic
def add_line(
    actor: Actor,
    *,
    order_id: int,
    product_id: int,
    quantity: int,
) -> OrderLine:
    """Buyurtmaga mahsulot qo'shadi yoki mavjud qator miqdorini oshiradi.

    Narx katalogdan **nusxa olinadi** (`unit_price`): keyin katalog narxi
    o'zgarsa ham mijozga aytilgan summa o'zgarmaydi.

    Bir mahsulot ikki marta qo'shilsa yangi qator yaratilmaydi — mavjud qator
    miqdori oshadi (INV-08). Aks holda AI bir mahsulotni ikki marta sanab,
    mijozga noto'g'ri summa aytardi.
    """
    if quantity <= 0:
        raise ValueError(f"miqdor musbat bo'lishi kerak: {quantity}")

    order = Order.objects.select_for_update().get(pk=order_id)
    if order.status != OrderStatus.DRAFT:
        raise OrderStateError(order.pk, order.status, "add_line")

    product = Product.objects.only("id", "sku", "price", "is_active").get(pk=product_id)
    if not product.is_active:
        raise ValueError(f"arxivlangan mahsulotga buyurtma olinmaydi: {product.sku}")

    line, created = OrderLine.objects.get_or_create(
        order=order,
        product=product,
        defaults={"quantity": quantity, "unit_price": product.price},
    )
    if not created:
        line.quantity = F("quantity") + quantity
        line.save(update_fields=["quantity", "updated_at"])
        line.refresh_from_db(fields=["quantity"])

    total = recalculate_total(order_id=order.pk)

    # Har qator qo'shilishi auditga tushadi: nazoratchi suhbat davomida AI
    # qanday buyurtma yiqqanini qadamba-qadam ko'rishi kerak (UC-09).
    event_type = (
        AuditEventType.AI_TOOL_CALLED
        if actor.kind == ActorKind.AI
        else AuditEventType.ORDER_CREATED
    )
    audit.record_event(
        actor,
        event_type,
        f"Buyurtmaga qo'shildi: {product.sku} x {quantity}",
        customer_ref=order.customer_id,
        order_ref=order.pk,
        payload={
            "sku": product.sku,
            "quantity_added": quantity,
            "unit_price": str(line.unit_price),
            "order_total": str(total),
        },
    )
    return line


@transaction.atomic
def remove_line(actor: Actor, *, order_id: int, line_id: int) -> None:
    """Buyurtmadan qatorni olib tashlaydi va rezervatsiyasini qaytaradi."""
    order = Order.objects.select_for_update().get(pk=order_id)
    if order.status not in {OrderStatus.DRAFT, OrderStatus.RESERVED}:
        raise OrderStateError(order.pk, order.status, "remove_line")

    line = OrderLine.objects.select_related("product").get(pk=line_id, order=order)

    if line.reserved_quantity > 0 and line.reserved_stock_id is not None:
        inventory_services.release_reservation(
            actor,
            stock_id=line.reserved_stock_id,
            quantity=line.reserved_quantity,
            order_ref=order.pk,
            reason="Buyurtma qatori olib tashlandi",
        )

    line.delete()
    recalculate_total(order_id=order.pk)


def recalculate_total(*, order_id: int) -> Decimal:
    """Buyurtma summasini qatorlar bo'yicha qayta hisoblaydi (INV-03).

    Hisob **SQL agregatsiyasida** bajariladi (konstitutsiya 5-band). Python'da
    `sum(line.line_total for line in order.lines.all())` yozish taqiqlanadi:
    u barcha qatorlarni xotiraga tortadi va katta buyurtmalarda sekinlashadi.
    """
    total = OrderLine.objects.filter(order_id=order_id).aggregate(
        total=Coalesce(Sum("line_total"), Decimal("0")),
    )["total"]

    Order.objects.filter(pk=order_id).update(total_amount=total, updated_at=timezone.now())
    return Decimal(total)


@transaction.atomic
def reserve_order(actor: Actor, *, order_id: int, warehouse_id: int) -> Order:
    """Buyurtmaning barcha qatorlari uchun zaxirani ushlab turadi.

    Amal **atomar**: biror qatorga zaxira yetmasa butun rezervatsiya bekor
    bo'ladi (tranzaksiya rollback). Yarim rezerv qilingan buyurtma qolmaydi —
    aks holda mijozga "yarmini berolaman" degan chalkash holat yuzaga kelardi.

    Raises:
        `EmptyOrderError` — buyurtmada qator yo'q.
        `OrderStateError` — holat mashinasi bu o'tishga ruxsat bermaydi.
        `InsufficientStockError` — biror qatorga zaxira yetmaydi.
    """
    order = Order.objects.select_for_update().get(pk=order_id)

    if not order.can_transition_to(OrderStatus.RESERVED):
        raise OrderStateError(order.pk, order.status, OrderStatus.RESERVED)

    lines = list(order.lines.select_related("product").all())
    if not lines:
        raise EmptyOrderError(f"order={order.pk}")

    # Kerakli zaxira qatorlarini topamiz: mahsulot -> Stock.pk
    stock_by_product = {
        row["product_id"]: row["id"]
        for row in Stock.objects.filter(
            product_id__in=[line.product_id for line in lines],
            warehouse_id=warehouse_id,
        ).values("id", "product_id")
    }

    missing = [line.product.sku for line in lines if line.product_id not in stock_by_product]
    if missing:
        raise InsufficientStockError(", ".join(missing), 0, 0)

    # DEADLOCK PROFILAKTIKASI: barcha qatorlarni oldindan, `pk` o'sish
    # tartibida qulflaymiz. Shundan keyin `reserve_stock` ichidagi qulflash
    # allaqachon bizga tegishli qatorni oladi va kutish bo'lmaydi.
    list(inventory_services.lock_stocks_for_update(list(stock_by_product.values())))

    for line in lines:
        need = line.quantity - line.reserved_quantity
        if need <= 0:
            continue

        stock_id = stock_by_product[line.product_id]
        inventory_services.reserve_stock(
            actor,
            stock_id=stock_id,
            quantity=need,
            order_ref=order.pk,
        )
        line.reserved_stock_id = stock_id
        line.reserved_quantity = line.quantity
        line.reservation_expires_at = timezone.now() + timedelta(
            minutes=settings.RESERVATION_TTL_MINUTES
        )
        line.save(
            update_fields=[
                "reserved_stock",
                "reserved_quantity",
                "reservation_expires_at",
                "updated_at",
            ]
        )

    previous_status = order.status
    order.status = OrderStatus.RESERVED
    order.save(update_fields=["status", "updated_at"])

    audit.record_event(
        actor,
        AuditEventType.ORDER_STATUS_CHANGED,
        f"Buyurtma rezerv qilindi ({len(lines)} qator)",
        customer_ref=order.customer_id,
        order_ref=order.pk,
        payload={"from": previous_status, "to": OrderStatus.RESERVED, "lines": len(lines)},
    )
    return order


@transaction.atomic
def confirm_order(actor: Actor, *, order_id: int, payment_method: str) -> Order:
    """Buyurtmani tasdiqlaydi.

    To'lov usuli shu bosqichda majburiy (DB constraint ham buni talab qiladi).
    Pulning o'zi bu yerda harakatlanmaydi — AI faqat mijozning tanlovini qayd etadi.
    """
    order = Order.objects.select_for_update().get(pk=order_id)

    if not order.can_transition_to(OrderStatus.CONFIRMED):
        raise OrderStateError(order.pk, order.status, OrderStatus.CONFIRMED)
    if not order.lines.exists():
        raise EmptyOrderError(f"order={order.pk}")
    if not payment_method:
        raise ValueError("to'lov usuli ko'rsatilishi shart")

    order.status = OrderStatus.CONFIRMED
    order.payment_method = payment_method
    order.save(update_fields=["status", "payment_method", "updated_at"])

    audit.record_event(
        actor,
        AuditEventType.ORDER_STATUS_CHANGED,
        f"Buyurtma tasdiqlandi, to'lov: {payment_method}",
        customer_ref=order.customer_id,
        order_ref=order.pk,
        payload={
            "to": OrderStatus.CONFIRMED,
            "payment_method": payment_method,
            "total_amount": str(order.total_amount),
        },
    )
    return order


@transaction.atomic
def cancel_order(actor: Actor, *, order_id: int, reason: str) -> Order:
    """Buyurtmani bekor qiladi va ushlab turilgan zaxirani sotuvga qaytaradi.

    Sabab majburiy (DB constraint): "shunchaki bekor qilindi" degan yozuv
    nizoda hech narsani isbotlamaydi.
    """
    if not reason:
        raise ValueError("bekor qilish sababi ko'rsatilishi shart")

    order = Order.objects.select_for_update().get(pk=order_id)

    if not order.can_transition_to(OrderStatus.CANCELLED):
        raise OrderStateError(order.pk, order.status, OrderStatus.CANCELLED)

    released = 0
    reserved_lines = order.lines.filter(
        reserved_quantity__gt=0, reserved_stock__isnull=False
    ).order_by("reserved_stock_id")  # deadlock profilaktikasi: qulflash tartibi barqaror

    for line in reserved_lines:
        if line.reserved_stock_id is None:  # filtr bilan to'silgan, tip aniqligi uchun
            continue
        inventory_services.release_reservation(
            actor,
            stock_id=line.reserved_stock_id,
            quantity=line.reserved_quantity,
            order_ref=order.pk,
            reason=f"Buyurtma bekor qilindi: {reason}",
        )
        released += line.reserved_quantity
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

    # Band qilingan yetkazib berish oynasi bo'shatiladi
    if order.delivery_slot_id is not None:
        DeliverySlot.objects.filter(pk=order.delivery_slot_id).update(
            booked_count=F("booked_count") - 1
        )

    order.status = OrderStatus.CANCELLED
    order.cancelled_at = timezone.now()
    order.cancellation_reason = reason
    order.save(update_fields=["status", "cancelled_at", "cancellation_reason", "updated_at"])

    audit.record_event(
        actor,
        AuditEventType.ORDER_CANCELLED,
        f"Buyurtma bekor qilindi: {reason}",
        customer_ref=order.customer_id,
        order_ref=order.pk,
        payload={"reason": reason, "released_quantity": released},
    )
    return order


@transaction.atomic
def set_delivery(
    actor: Actor,
    *,
    order_id: int,
    slot_id: int,
    address: str,
    recipient_name: str = "",
    recipient_phone: str = "",
    notes: str = "",
) -> Order:
    """Yetkazib berish oynasi va manzilini belgilaydi.

    **Ikkinchi poyga holati manbasi.** Bir vaqtda ikki mijoz oxirgi bo'sh
    oynani band qilishga urinishi mumkin, shuning uchun oyna `select_for_update()`
    bilan qulflanadi va sig'im tekshiriladi. Baza darajasidagi
    `slot_booked_not_exceeding_capacity` constraint — so'nggi chegara.
    """
    if not address:
        raise ValueError("yetkazib berish manzili ko'rsatilishi shart")

    order = Order.objects.select_for_update().get(pk=order_id)
    if order.status not in {OrderStatus.RESERVED, OrderStatus.CONFIRMED}:
        raise OrderStateError(order.pk, order.status, "set_delivery")

    slot = DeliverySlot.objects.select_for_update().get(pk=slot_id, is_active=True)

    if slot.booked_count >= slot.capacity:
        raise InsufficientStockError(f"slot-{slot.pk}", 1, 0)

    # Oldin boshqa oyna band qilingan bo'lsa — bo'shatamiz
    if order.delivery_slot_id is not None and order.delivery_slot_id != slot.pk:
        DeliverySlot.objects.filter(pk=order.delivery_slot_id).update(
            booked_count=F("booked_count") - 1
        )
    elif order.delivery_slot_id == slot.pk:
        slot.booked_count -= 1  # qayta band qilishda ikki marta sanalmasligi uchun

    slot.booked_count += 1
    slot.save(update_fields=["booked_count", "updated_at"])

    order.delivery_slot = slot
    order.delivery_address = address
    order.recipient_name = recipient_name
    order.recipient_phone = recipient_phone
    order.delivery_notes = notes
    order.save(
        update_fields=[
            "delivery_slot",
            "delivery_address",
            "recipient_name",
            "recipient_phone",
            "delivery_notes",
            "updated_at",
        ]
    )

    audit.record_event(
        actor,
        AuditEventType.DELIVERY_SLOT_BOOKED,
        f"Yetkazib berish belgilandi: {slot}",
        customer_ref=order.customer_id,
        order_ref=order.pk,
        payload={"slot_id": slot.pk, "date": str(slot.date), "region": slot.region},
    )
    return order
