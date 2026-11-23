"""Ombor o'qish amallari (CQS — Query/Read qatlami).

**Bu qatlam hech qachon bazaga yozmaydi** (konstitutsiya 3.3 — yon ta'sirlar chegarasi).

Barcha hisob-kitob SQL darajasida bajariladi. Python siklida yig'ish taqiqlanadi:
50 000 qatorli daftarni xotiraga tortish ovoz suhbatining kechikish byudjetini
(tool chaqiruvi ≤ 100 ms) bir necha barobar buzadi.
"""

from dataclasses import dataclass

from django.conf import settings
from django.db.models import F, QuerySet, Sum
from django.db.models.functions import Coalesce

from apps.inventory.models import Stock, StockMovement


def stocks_with_availability() -> QuerySet[Stock]:
    """Mavjud miqdor bilan boyitilgan zaxira so'rovi.

    Annotatsiya nomi `available_qty` — modeldagi `Stock.available` property'si
    bilan to'qnashmasligi uchun (Django bir xil nomdagi annotatsiyani rad etadi).
    Ikkisi bir xil qiymatni beradi, ammo turli joyda ishlatiladi:
    property — bitta obyekt bilan ishlaganda, annotatsiya — SQL darajasida
    filtrlash va tartiblash kerak bo'lganda (`.filter(available_qty__gt=0)`),
    buni Python property bilan qilish imkonsiz.
    """
    return Stock.objects.annotate(available_qty=F("on_hand") - F("reserved"))


def get_availability(product_id: int, *, warehouse_id: int | None = None) -> int:
    """Mahsulotning sotuvga tayyor miqdori.

    Ko'p omborli holatda barcha omborlar yig'indisi qaytariladi (bitta SQL so'rov).
    """
    qs = Stock.objects.filter(product_id=product_id)
    if warehouse_id is not None:
        qs = qs.filter(warehouse_id=warehouse_id)

    result = qs.aggregate(
        available_qty=Coalesce(Sum(F("on_hand") - F("reserved")), 0),
    )
    return int(result["available_qty"])


def get_availability_map(product_ids: list[int]) -> dict[int, int]:
    """Bir nechta mahsulotning mavjudligi — **bitta** SQL so'rovda.

    Buyurtma bir necha qatordan iborat bo'lganda har qator uchun alohida so'rov
    yuborish N+1 muammosini keltiradi: 10 qatorli buyurtma 10 so'rovga aylanadi
    va kechikish byudjetidan chiqib ketadi.
    """
    rows = (
        Stock.objects.filter(product_id__in=product_ids)
        .values("product_id")
        .annotate(available_qty=Coalesce(Sum(F("on_hand") - F("reserved")), 0))
    )
    return {int(row["product_id"]): int(row["available_qty"]) for row in rows}


# ─────────────────────────────────────────────────────────────────────────────
# Qoldiqni oshkor qilish siyosati (domen tahlili §6, STRIDE-I)
# ─────────────────────────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class AvailabilityDisclosure:
    """Mijozga aytiladigan mavjudlik ma'lumoti.

    `exact_quantity` faqat qoldiq kam bo'lganda to'ldiriladi. Aks holda `None` —
    AI aniq sonni **bilmaydi**, shuning uchun uni aytib ham qo'ymaydi. Bu
    "AI ga aytmaslikni buyurish" dan ishonchliroq: berilmagan ma'lumot oshkor
    bo'lmaydi.
    """

    is_available: bool
    exact_quantity: int | None
    message: str


def describe_availability(available: int) -> AvailabilityDisclosure:
    """Mavjud miqdorni mijozga aytiladigan shaklga aylantiradi.

    Siyosat (sof funksiya — bir xil kirishga bir xil natija):

    * `available > chegara` → "mavjud", aniq son **aytilmaydi** (raqobatchi
      ombor hajmini bilmasligi uchun);
    * `0 < available <= chegara` → "oxirgi N dona qoldi" — bu haqiqat va
      biznes uchun ham foydali (shoshilish effekti);
    * `available <= 0` → "hozir yo'q".

    Chegara `.env` dan o'qiladi (`LOW_STOCK_DISCLOSURE_THRESHOLD`), kodda
    qattiq yozilmaydi (Hooker 5).
    """
    threshold: int = settings.LOW_STOCK_DISCLOSURE_THRESHOLD

    if available <= 0:
        return AvailabilityDisclosure(
            is_available=False,
            exact_quantity=None,
            message="Hozir mavjud emas.",
        )

    if available <= threshold:
        return AvailabilityDisclosure(
            is_available=True,
            exact_quantity=available,
            message=f"Oxirgi {available} dona qoldi.",
        )

    return AvailabilityDisclosure(
        is_available=True,
        exact_quantity=None,
        message="Mavjud.",
    )


def get_low_stock_items() -> QuerySet[Stock]:
    """Minimal zaxiradan pastga tushgan pozitsiyalar — ombor xodimi uchun.

    AI bu ma'lumotga murojaat qilmaydi: u ombor boshqaruvi hisoboti, sotuv emas.
    """
    # `type: ignore[misc]` — django-stubs `annotate()` bilan qo'shilgan maydon
    # nomlarini kuzatmaydi, shuning uchun `available_qty` ni mavjud emas deb
    # hisoblaydi. Bu stublar cheklovi, kod xatosi emas: annotatsiya
    # `stocks_with_availability()` da aniq qo'shilgan.
    return (
        stocks_with_availability()
        .filter(available_qty__lte=F("reorder_level"))  # type: ignore[misc]
        .select_related("product", "warehouse")
        .order_by("available_qty")
    )


# ─────────────────────────────────────────────────────────────────────────────
# Invariant tekshiruvi
# ─────────────────────────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class LedgerConsistency:
    """Daftar va joriy qoldiq mosligi natijasi."""

    stock_id: int
    stock_on_hand: int
    ledger_on_hand: int
    stock_reserved: int
    ledger_reserved: int

    @property
    def is_consistent(self) -> bool:
        return (
            self.stock_on_hand == self.ledger_on_hand
            and self.stock_reserved == self.ledger_reserved
        )


def verify_ledger_consistency(stock_id: int) -> LedgerConsistency:
    """`Stock` qiymatlari daftar yig'indisiga mos kelishini tekshiradi.

    Arxitektura invarianti (modeldagi izohga qarang):

        Stock.on_hand  == SUM(StockMovement.on_hand_delta)
        Stock.reserved == SUM(StockMovement.reserved_delta)

    Ikkisi ajralib ketsa tizim noto'g'ri qoldiq aytadi va ortiqcha sotuv
    boshlanadi. Shu sababli bu tekshiruv ikki joyda ishlatiladi: testlarda
    (har rezervatsiya testidan keyin) va ishlab chiqarishda kunlik monitoring
    vazifasida.
    """
    stock = Stock.objects.values("on_hand", "reserved").get(pk=stock_id)
    totals = StockMovement.objects.filter(stock_id=stock_id).aggregate(
        on_hand=Coalesce(Sum("on_hand_delta"), 0),
        reserved=Coalesce(Sum("reserved_delta"), 0),
    )
    return LedgerConsistency(
        stock_id=stock_id,
        stock_on_hand=int(stock["on_hand"]),
        ledger_on_hand=int(totals["on_hand"]),
        stock_reserved=int(stock["reserved"]),
        ledger_reserved=int(totals["reserved"]),
    )
