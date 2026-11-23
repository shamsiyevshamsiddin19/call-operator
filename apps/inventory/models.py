"""Ombor, zaxira va o'zgarmas harakatlar daftari (Append-only Ledger).

Loyihaning eng nozik joyi. Ikki xil yozuv birga ishlaydi:

* **`StockMovement`** — haqiqat manbasi. O'zgarmas daftar: har kirim, chiqim,
  rezervatsiya va bekor qilish **yangi qator** sifatida qo'shiladi. Hech qachon
  tahrirlanmaydi (konstitutsiya 4-band, DB triggeri bilan himoyalangan).

* **`Stock`** — shu daftardan kelib chiqadigan **joriy qoldiq** (materialized).
  Nega alohida saqlanadi: har zaxira so'rovida 50 000 qatorli daftarni `SUM`
  qilish ovoz suhbatining kechikish byudjetiga (≤ 100 ms) sig'maydi.

Ikkisining mosligi arxitektura invarianti:

    Stock.on_hand  == SUM(StockMovement.on_hand_delta)
    Stock.reserved == SUM(StockMovement.reserved_delta)

Buni `services` qatlami bitta tranzaksiyada kafolatlaydi, test esa har safar
tekshiradi (`test_ledger_matches_stock`). Agar ular ajralib ketsa — tizim
noto'g'ri qoldiq aytadi, bu esa ortiqcha sotuvga olib keladi.

**Rezervatsiya mantiqi.** `on_hand` — jismonan omborda turgan miqdor.
`reserved` — tasdiqlanmagan buyurtmalar uchun ushlab turilgan miqdor.
Sotuvga tayyor miqdor: `available = on_hand - reserved`. Mijozga aytiladigan
va buyurtma olinadigan son — faqat `available`.
"""

from django.db import models
from django.db.models import F, Q
from django.utils.translation import gettext_lazy as _

from apps.catalog.models import Product
from apps.common.models import ImmutableModel, SoftDeletableModel, TimestampedModel


class Warehouse(TimestampedModel, SoftDeletableModel):
    """Ombor.

    MVP da bitta ombor ishlatiladi, ammo model ko'p omborga ochiq (Hooker 5 —
    kelajakka ochiqlik): filial qo'shilganda sxema o'zgartirilmaydi.
    """

    code = models.CharField(_("kodi"), max_length=32, unique=True)
    name = models.CharField(_("nomi"), max_length=200)
    address = models.CharField(_("manzili"), max_length=500, blank=True)

    class Meta:
        verbose_name = _("ombor")
        verbose_name_plural = _("omborlar")

    def __str__(self) -> str:
        return f"{self.name} [{self.code}]"


class Stock(TimestampedModel):
    """Muayyan ombordagi muayyan mahsulotning joriy qoldig'i.

    Bu yozuv **faqat `inventory.services` orqali va `select_for_update()` bilan**
    o'zgartiriladi. To'g'ridan-to'g'ri `Stock.objects.update()` yozish taqiqlanadi:
    u daftarga yozuv qo'shmaydi va yuqoridagi invariantni buzadi.
    """

    warehouse = models.ForeignKey(
        Warehouse,
        on_delete=models.PROTECT,
        related_name="stocks",
        verbose_name=_("ombor"),
    )
    product = models.ForeignKey(
        Product,
        on_delete=models.PROTECT,  # INV-09: zaxirasi bor mahsulot o'chirilmaydi
        related_name="stocks",
        verbose_name=_("mahsulot"),
    )
    on_hand = models.IntegerField(
        _("jismoniy qoldiq"),
        default=0,
        help_text=_("Omborda turgan miqdor."),
    )
    reserved = models.IntegerField(
        _("rezervda"),
        default=0,
        help_text=_("Tasdiqlanmagan buyurtmalar uchun ushlab turilgan miqdor."),
    )
    reorder_level = models.IntegerField(
        _("minimal zaxira"),
        default=0,
        help_text=_("Shu darajadan pastga tushganda ombor xodimiga ogohlantirish."),
    )

    class Meta:
        verbose_name = _("zaxira")
        verbose_name_plural = _("zaxiralar")
        constraints = [
            models.UniqueConstraint(
                fields=["warehouse", "product"],
                name="stock_unique_per_warehouse_product",
            ),
            # INV-01: jismoniy qoldiq manfiy bo'lmaydi.
            # Bu dastur kodidagi `if` emas — baza darajasidagi kafolat. Hatto
            # xato yozilgan migratsiya yoki qo'lda SQL ham buni buzolmaydi.
            models.CheckConstraint(condition=Q(on_hand__gte=0), name="stock_on_hand_non_negative"),
            # INV-02: rezerv manfiy bo'lmaydi va qoldiqdan oshmaydi.
            # Oshsa `available` manfiyga tushardi va AI yo'q mahsulotni sotardi.
            models.CheckConstraint(
                condition=Q(reserved__gte=0), name="stock_reserved_non_negative"
            ),
            models.CheckConstraint(
                condition=Q(reserved__lte=F("on_hand")),
                name="stock_reserved_not_exceeding_on_hand",
            ),
            models.CheckConstraint(
                condition=Q(reorder_level__gte=0), name="stock_reorder_level_non_negative"
            ),
        ]
        indexes = [
            models.Index(fields=["product", "warehouse"], name="stock_product_warehouse_idx"),
        ]

    def __str__(self) -> str:
        return f"{self.product.sku} @ {self.warehouse.code}: {self.available}"

    @property
    def available(self) -> int:
        """Sotuvga tayyor miqdor — mijozga aytiladigan va buyurtma olinadigan son.

        Enkapsulyatsiya (OOP 1): `on_hand - reserved` ayirmasi kod bo'ylab
        takrorlanmaydi, bitta joyda hisoblanadi. Ro'yxat so'rovlarida esa bu
        ayirma SQL darajasida (`annotate`) hisoblanadi — `selectors` ga qarang.
        """
        return self.on_hand - self.reserved


class MovementKind(models.TextChoices):
    """Ombor harakati turi.

    Har tur `on_hand` va `reserved` ga qanday ta'sir qilishi `services` da
    qat'iy belgilangan va test bilan qoplangan:

    ===============  ==============  ===============
    Tur              on_hand_delta   reserved_delta
    ===============  ==============  ===============
    RECEIPT          +qty            0
    RESERVE          0               +qty
    RELEASE          0               -qty
    ISSUE            -qty            -qty
    RETURN           +qty            0
    ADJUSTMENT       +/-qty            0
    WRITE_OFF        -qty            0
    ===============  ==============  ===============

    `ISSUE` ikki maydonni birga kamaytiradi: tovar rezervdan chiqib jismonan
    ham ketadi. Shu sababli rezervlanmagan tovarni `ISSUE` qilish taqiqlanadi.
    """

    RECEIPT = "RECEIPT", _("Kirim (ta'minotchidan)")
    RESERVE = "RESERVE", _("Rezervatsiya")
    RELEASE = "RELEASE", _("Rezervatsiyani bekor qilish")
    ISSUE = "ISSUE", _("Chiqim (mijozga berildi)")
    RETURN = "RETURN", _("Qaytarish (mijozdan)")
    ADJUSTMENT = "ADJUSTMENT", _("Inventarizatsiya tuzatishi")
    WRITE_OFF = "WRITE_OFF", _("Yo'q qilish (buzilgan/yo'qolgan)")


class StockMovement(ImmutableModel):
    """Ombor harakatlari daftari — **append-only**.

    Xato kiritilgan yozuv tahrirlanmaydi va o'chirilmaydi: uning o'rniga teskari
    yo'nalishdagi **yangi** yozuv (storno) qo'shiladi va `reversal_of` orqali
    aslga bog'lanadi. Shunda "kim, qachon, nega tuzatdi" savoliga javob qoladi
    (STRIDE-R — rad eta olmaslik).

    `on_hand_delta` va `reserved_delta` — ishorali (signed) qiymatlar. Shu tufayli
    qoldiqni tekshirish bitta SQL agregatsiyaga aylanadi:
    `SUM(on_hand_delta)` natijasi `Stock.on_hand` ga teng bo'lishi shart.
    """

    stock = models.ForeignKey(
        Stock,
        on_delete=models.PROTECT,  # daftar yozuvi hech qachon yo'qolmaydi
        related_name="movements",
        verbose_name=_("zaxira"),
    )
    kind = models.CharField(
        _("turi"),
        max_length=20,
        choices=MovementKind.choices,
        db_index=True,
    )
    on_hand_delta = models.IntegerField(
        _("jismoniy qoldiq o'zgarishi"),
        help_text=_("Ishorali: +10 kirim, -2 chiqim."),
    )
    reserved_delta = models.IntegerField(
        _("rezerv o'zgarishi"),
        help_text=_("Ishorali: +2 rezervatsiya, -2 bekor qilish."),
    )

    # ── Kim va nega (audit) ──────────────────────────────────────────────────
    actor_label = models.CharField(
        _("aktor"),
        max_length=100,
        help_text=_("Actor.label — masalan `ai:VOICE:conv42` yoki `staff:7:WAREHOUSE`."),
    )
    reason = models.CharField(_("sabab"), max_length=300, blank=True)
    trace_id = models.CharField(
        _("kuzatuv identifikatori"),
        max_length=64,
        blank=True,
        db_index=True,
        help_text=_("Audio → Gemini → tool-call → DB zanjirini bog'laydi."),
    )

    # ── Bog'lanishlar ────────────────────────────────────────────────────────
    # Buyurtmaga havola **FK emas**: daftar hech qanday boshqa jadvalning
    # o'chirilishi yoki anonimlashtirilishiga qaram bo'lmasligi kerak. Mijoz
    # ma'lumotini o'chirishni so'rasa (maxfiylik talabi) buyurtma anonimlashadi,
    # ammo ombor daftari butun qoladi — aks holda hisobot buziladi.
    order_ref = models.BigIntegerField(
        _("buyurtma havolasi"),
        null=True,
        blank=True,
        db_index=True,
    )
    reversal_of = models.ForeignKey(
        "self",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="reversals",
        verbose_name=_("qaysi yozuvning tuzatilishi"),
    )

    class Meta:
        verbose_name = _("ombor harakati")
        verbose_name_plural = _("ombor harakatlari daftari")
        constraints = [
            # Ikki delta ham nol bo'lgan yozuv hech narsani o'zgartirmaydi —
            # daftarni shovqin bilan to'ldiradi va tahlilni chalg'itadi.
            models.CheckConstraint(
                condition=~(Q(on_hand_delta=0) & Q(reserved_delta=0)),
                name="movement_must_change_something",
            ),
        ]
        indexes = [
            models.Index(fields=["stock", "-created_at"], name="movement_stock_time_idx"),
            models.Index(fields=["kind", "-created_at"], name="movement_kind_time_idx"),
        ]

    def __str__(self) -> str:
        return (
            f"{self.get_kind_display()} {self.stock.product.sku}: "
            f"qoldiq {self.on_hand_delta:+d}, rezerv {self.reserved_delta:+d}"
        )
