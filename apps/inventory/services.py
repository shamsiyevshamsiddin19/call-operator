"""Ombor amallari (CQS — Command/Write qatlami).

**Poyga holatlari (race conditions) bu modulning asosiy muammosi.**
Bir millisekundda ikki mijoz oxirgi mahsulotni buyurtma qilishga urinishi mumkin.
Himoya ikki qatlamli:

1. `select_for_update()` — qator darajasidagi qulf. Ikkinchi tranzaksiya
   birinchisi tugaguncha kutadi, shuning uchun `available` hisobi eskirmaydi.
2. `CheckConstraint` — bazadagi so'nggi chegara. Agar kodda xato bo'lsa ham
   qoldiq manfiyga tushmaydi (INV-01, INV-02).

Faqat ikkinchi qatlamga tayanish yetarli emas: constraint xatolik qaytaradi,
ammo mijozga tushunarli javob bermaydi. Faqat birinchiga tayanish ham yetarli
emas: kodni aylanib o'tish yo'llari bor.

**Deadlock profilaktikasi.** Bir amalda bir nechta `Stock` qatori qulflanganda,
ular **har doim `pk` o'sish tartibida** qulflanadi (`lock_stocks_for_update`).
Aks holda ikki parallel tranzaksiya bir-birini kutib qolishi mumkin: A qator 5 ni,
B qator 9 ni oldi, keyin A 9 ni, B 5 ni so'radi.
"""

from collections.abc import Sequence
from datetime import timedelta

from django.conf import settings
from django.db import transaction
from django.db.models import QuerySet
from django.utils import timezone

from apps.accounts.actors import Actor
from apps.audit import services as audit
from apps.audit.models import AuditEventType, AuditSeverity
from apps.common.exceptions import ActorNotPermittedError, InsufficientStockError
from apps.inventory.models import MovementKind, Stock, StockMovement


def lock_stocks_for_update(stock_ids: Sequence[int]) -> QuerySet[Stock]:
    """Bir nechta zaxira qatorini deadlock'siz qulflaydi.

    Qulflash tartibi `pk` bo'yicha o'sish tartibida bo'lishi shart — shu sababli
    `order_by("pk")` **hayotiy ahamiyatga ega**, uni optimizatsiya deb o'ylab
    olib tashlash taqiqlanadi.
    """
    return (
        Stock.objects.select_for_update()
        .select_related("product")
        .filter(pk__in=sorted(set(stock_ids)))
        .order_by("pk")
    )


@transaction.atomic
def reserve_stock(
    actor: Actor,
    *,
    stock_id: int,
    quantity: int,
    order_ref: int | None = None,
    ttl_minutes: int | None = None,
) -> StockMovement:
    """Zaxirani ushlab turadi (rezervatsiya).

    AI operator uchun **ruxsat etilgan** amal: rezervatsiya o'z-o'zidan
    qaytariladi (TTL tugaganda Celery bekor qiladi), shuning uchun xato
    rezervatsiya tuzatib bo'lmaydigan zarar keltirmaydi. Zaxirani
    *to'g'ridan-to'g'ri o'zgartirish* esa AI uchun yopiq (`adjust_stock`).

    Qaytaradi:
        Daftarga tushgan `StockMovement` yozuvi.

    Xatolik:
        `InsufficientStockError` — so'ralgan miqdor mavjud zaxiradan ko'p.
    """
    if quantity <= 0:
        raise ValueError(f"rezervatsiya miqdori musbat bo'lishi kerak: {quantity}")

    stock = Stock.objects.select_for_update().select_related("product").get(pk=stock_id)
    available = stock.on_hand - stock.reserved

    if quantity > available:
        # Konstitutsiya 7-bandi: zaxiradan ortiq miqdor so'rovi xavfsizlik
        # jurnaliga tushadi. Bu poyga holati belgisi yoki tizimni aldashga
        # urinish bo'lishi mumkin — ikkisi ham tekshirilishi kerak.
        audit.record_security_violation(
            actor,
            AuditEventType.INSUFFICIENT_STOCK_REQUEST,
            f"Zaxiradan ortiq miqdor so'raldi: {stock.product.sku}",
            payload={
                "sku": stock.product.sku,
                "requested": quantity,
                "available": available,
                "order_ref": order_ref,
            },
        )
        raise InsufficientStockError(stock.product.sku, quantity, available)

    stock.reserved += quantity
    stock.save(update_fields=["reserved", "updated_at"])

    ttl = ttl_minutes if ttl_minutes is not None else settings.RESERVATION_TTL_MINUTES
    expires_at = timezone.now() + timedelta(minutes=ttl)

    movement = StockMovement.objects.create(
        stock=stock,
        kind=MovementKind.RESERVE,
        on_hand_delta=0,
        reserved_delta=quantity,
        actor_label=actor.label,
        reason=f"Rezervatsiya, muddati {ttl} daqiqa",
        trace_id=actor.trace_id,
        order_ref=order_ref,
    )

    audit.record_event(
        actor,
        AuditEventType.STOCK_RESERVED,
        f"{stock.product.sku}: {quantity} dona rezerv qilindi",
        order_ref=order_ref,
        payload={
            "sku": stock.product.sku,
            "quantity": quantity,
            "expires_at": expires_at.isoformat(),
            "available_after": available - quantity,
        },
    )
    return movement


@transaction.atomic
def release_reservation(
    actor: Actor,
    *,
    stock_id: int,
    quantity: int,
    order_ref: int | None = None,
    reason: str = "",
) -> StockMovement:
    """Rezervatsiyani bekor qiladi — ushlab turilgan miqdor sotuvga qaytadi.

    Chaqiriladi: mijoz buyurtmani bekor qilganda, TTL tugaganda (Celery) yoki
    buyurtma qatori o'chirilganda.
    """
    if quantity <= 0:
        raise ValueError(f"bekor qilish miqdori musbat bo'lishi kerak: {quantity}")

    stock = Stock.objects.select_for_update().select_related("product").get(pk=stock_id)

    # Rezervdan ko'p bekor qilish mantiqsiz: bu hisobni buzadi va `reserved`
    # manfiyga tushardi (CheckConstraint bilan to'siladi, ammo bu yerda
    # tushunarli xatolik berish yaxshiroq).
    release_qty = min(quantity, stock.reserved)
    if release_qty == 0:
        raise ValueError(f"bekor qiladigan rezerv yo'q: stock={stock_id}")

    stock.reserved -= release_qty
    stock.save(update_fields=["reserved", "updated_at"])

    movement = StockMovement.objects.create(
        stock=stock,
        kind=MovementKind.RELEASE,
        on_hand_delta=0,
        reserved_delta=-release_qty,
        actor_label=actor.label,
        reason=reason or "Rezervatsiya bekor qilindi",
        trace_id=actor.trace_id,
        order_ref=order_ref,
    )

    audit.record_event(
        actor,
        AuditEventType.STOCK_RELEASED,
        f"{stock.product.sku}: {release_qty} dona rezervdan chiqarildi",
        order_ref=order_ref,
        payload={"sku": stock.product.sku, "quantity": release_qty, "reason": reason},
    )
    return movement


@transaction.atomic
def issue_stock(
    actor: Actor,
    *,
    stock_id: int,
    quantity: int,
    order_ref: int | None = None,
) -> StockMovement:
    """Tovarni mijozga beradi: rezervdan chiqaradi va jismoniy qoldiqdan ayiradi.

    Chaqiriladi buyurtma yetkazib berilgandan keyin. Rezervlanmagan tovarni
    chiqarish taqiqlanadi — aks holda ikki mijozga bir tovar sotilgan bo'lardi.
    """
    if quantity <= 0:
        raise ValueError(f"chiqim miqdori musbat bo'lishi kerak: {quantity}")

    stock = Stock.objects.select_for_update().select_related("product").get(pk=stock_id)

    if quantity > stock.reserved:
        audit.record_security_violation(
            actor,
            AuditEventType.INSUFFICIENT_STOCK_REQUEST,
            f"Rezervlanmagan tovarni chiqarishga urinish: {stock.product.sku}",
            payload={"sku": stock.product.sku, "requested": quantity, "reserved": stock.reserved},
        )
        raise InsufficientStockError(stock.product.sku, quantity, stock.reserved)

    stock.reserved -= quantity
    stock.on_hand -= quantity
    stock.save(update_fields=["reserved", "on_hand", "updated_at"])

    movement = StockMovement.objects.create(
        stock=stock,
        kind=MovementKind.ISSUE,
        on_hand_delta=-quantity,
        reserved_delta=-quantity,
        actor_label=actor.label,
        reason="Mijozga berildi",
        trace_id=actor.trace_id,
        order_ref=order_ref,
    )

    audit.record_event(
        actor,
        AuditEventType.STOCK_ADJUSTED,
        f"{stock.product.sku}: {quantity} dona chiqim",
        order_ref=order_ref,
        payload={"sku": stock.product.sku, "quantity": quantity, "kind": MovementKind.ISSUE},
    )
    return movement


@transaction.atomic
def receive_stock(
    actor: Actor,
    *,
    stock_id: int,
    quantity: int,
    reason: str,
) -> StockMovement:
    """Ta'minotchidan kirim. Faqat ombor xodimi va ma'mur uchun."""
    if not actor.can_adjust_stock:
        audit.record_permission_denied(actor, "receive_stock")
        raise ActorNotPermittedError(actor.label, "receive_stock")

    if quantity <= 0:
        raise ValueError(f"kirim miqdori musbat bo'lishi kerak: {quantity}")

    stock = Stock.objects.select_for_update().select_related("product").get(pk=stock_id)
    stock.on_hand += quantity
    stock.save(update_fields=["on_hand", "updated_at"])

    movement = StockMovement.objects.create(
        stock=stock,
        kind=MovementKind.RECEIPT,
        on_hand_delta=quantity,
        reserved_delta=0,
        actor_label=actor.label,
        reason=reason,
        trace_id=actor.trace_id,
    )

    audit.record_event(
        actor,
        AuditEventType.STOCK_ADJUSTED,
        f"{stock.product.sku}: {quantity} dona kirim",
        payload={"sku": stock.product.sku, "quantity": quantity, "reason": reason},
    )
    return movement


@transaction.atomic
def adjust_stock(
    actor: Actor,
    *,
    stock_id: int,
    delta: int,
    reason: str,
) -> StockMovement:
    """Inventarizatsiya tuzatishi — jismoniy sanoq natijasi bo'yicha.

    Bu amal **AI uchun yopiq**: tuzatish odam tomonidan ko'rilgan jismoniy
    holatga asoslanadi, suhbatdagi so'zga emas.

    Tuzatish daftarda alohida tur (`ADJUSTMENT`) bilan qoladi: shu sababli
    "qoldiq nega o'zgardi?" savoliga javob har doim mavjud.
    """
    if not actor.can_adjust_stock:
        audit.record_permission_denied(actor, "adjust_stock")
        raise ActorNotPermittedError(actor.label, "adjust_stock")

    if delta == 0:
        raise ValueError("tuzatish nolga teng bo'lishi mumkin emas")
    if not reason:
        raise ValueError("inventarizatsiya tuzatishida sabab ko'rsatilishi shart")

    stock = Stock.objects.select_for_update().select_related("product").get(pk=stock_id)
    stock.on_hand += delta
    stock.save(update_fields=["on_hand", "updated_at"])

    movement = StockMovement.objects.create(
        stock=stock,
        kind=MovementKind.ADJUSTMENT,
        on_hand_delta=delta,
        reserved_delta=0,
        actor_label=actor.label,
        reason=reason,
        trace_id=actor.trace_id,
    )

    audit.record_event(
        actor,
        AuditEventType.STOCK_ADJUSTED,
        f"{stock.product.sku}: inventarizatsiya {delta:+d}",
        severity=AuditSeverity.WARNING,
        payload={"sku": stock.product.sku, "delta": delta, "reason": reason},
    )
    return movement
