"""Mahsulot katalogi.

Ovozli interfeys katalogga ikki o'ziga xos talab qo'yadi:

1. **Xato eshitishga chidamlilik.** Mijoz "aypon o'n besh" deydi, katalogda
   `iPhone 15` turadi. Shuning uchun `pg_trgm` (trigram o'xshashlik) va
   `ProductSynonym` jadvali — ikkisi birga ishlaydi.

2. **Tannarxning qat'iy yashirinligi.** `cost_price` — tijorat siri.
   AI operator va mijoz uni **hech qachon** ko'rmaydi (STRIDE-I).
   Himoya `selectors` qatlamida: tannarx so'rovga umuman qo'shilmaydi,
   "frontendda yashirish" emas.
"""

from decimal import Decimal

from django.contrib.postgres.indexes import GinIndex
from django.core.validators import MinValueValidator
from django.db import models
from django.db.models import Q
from django.utils.translation import gettext_lazy as _

from apps.common.models import ImmutableModel, SoftDeletableModel, TimestampedModel


class Unit(models.TextChoices):
    """O'lchov birligi.

    Ovozli suhbatda muhim: AI "2 dona" yoki "2 kilo" deb aniq aytishi kerak,
    aks holda mijoz nima buyurganini tushunmaydi (Mandel 2 — xotira yuklamasini kamaytirish).
    """

    PIECE = "PIECE", _("dona")
    KILOGRAM = "KG", _("kilogramm")
    LITER = "LITER", _("litr")
    METER = "METER", _("metr")
    PACK = "PACK", _("paket")


class Category(TimestampedModel, SoftDeletableModel):
    """Mahsulot kategoriyasi (ierarxik)."""

    name = models.CharField(_("nomi"), max_length=200)
    parent = models.ForeignKey(
        "self",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="children",
        verbose_name=_("ota kategoriya"),
    )

    class Meta:
        verbose_name = _("kategoriya")
        verbose_name_plural = _("kategoriyalar")
        constraints = [
            models.UniqueConstraint(
                fields=["parent", "name"],
                name="category_name_unique_per_parent",
            ),
        ]

    def __str__(self) -> str:
        return self.name


class Product(TimestampedModel, SoftDeletableModel):
    """Mahsulot.

    `price` — sotuv narxi, mijozga aytiladi.
    `cost_price` — tannarx, **tijorat siri** (`Actor.can_view_cost_price`).
    """

    sku = models.CharField(
        _("artikul (SKU)"),
        max_length=64,
        unique=True,
        help_text=_("Ombor va buyurtmalarda ishlatiladigan yagona kod."),
    )
    name = models.CharField(_("nomi"), max_length=300, db_index=True)
    category = models.ForeignKey(
        Category,
        on_delete=models.PROTECT,  # INV-09
        related_name="products",
        verbose_name=_("kategoriya"),
    )
    description = models.TextField(_("tavsifi"), blank=True)
    unit = models.CharField(
        _("o'lchov birligi"),
        max_length=10,
        choices=Unit.choices,
        default=Unit.PIECE,
    )
    price = models.DecimalField(
        _("sotuv narxi"),
        max_digits=14,
        decimal_places=2,
        validators=[MinValueValidator(Decimal("0"))],
    )
    cost_price = models.DecimalField(
        _("tannarx"),
        max_digits=14,
        decimal_places=2,
        validators=[MinValueValidator(Decimal("0"))],
        help_text=_("TIJORAT SIRI. AI operator va mijozga ko'rsatilmaydi."),
    )

    class Meta:
        verbose_name = _("mahsulot")
        verbose_name_plural = _("mahsulotlar")
        constraints = [
            # Manfiy narx mantiqsiz holat — dastur kodiga ishonilmaydi (konstitutsiya 5-band)
            models.CheckConstraint(condition=Q(price__gte=0), name="product_price_non_negative"),
            models.CheckConstraint(
                condition=Q(cost_price__gte=0), name="product_cost_price_non_negative"
            ),
        ]
        indexes = [
            models.Index(fields=["is_active", "category"], name="product_active_category_idx"),
            # Trigram indeks — ovozli so'rovdagi imlo xatosini kechiradi:
            # "samsung galaksi" → `Samsung Galaxy`. Buning ustiga `ILIKE %...%`
            # so'rovlari ham shu indeksdan foydalanadi (oddiy B-tree indeks
            # boshida `%` bo'lgan qidiruvda ishlamaydi).
            GinIndex(
                fields=["name"],
                name="product_name_trgm_idx",
                opclasses=["gin_trgm_ops"],
            ),
        ]

    def __str__(self) -> str:
        return f"{self.name} [{self.sku}]"


class SynonymSource(models.TextChoices):
    """Sinonim qaydan kelgani.

    `AI_LEARNED` yozuvlari nazoratchi tomonidan ko'rib chiqiladi: AI noto'g'ri
    bog'lanish o'rganib olishi mumkin, shuning uchun avtomatik o'rganish
    tasdiqlanmaguncha izlashda past vaznga ega bo'ladi.
    """

    MANUAL = "MANUAL", _("Qo'lda kiritilgan")
    AI_LEARNED = "AI_LEARNED", _("AI suhbatdan o'rgangan")


class ProductSynonym(TimestampedModel):
    """Mahsulotning ovozda aytiladigan muqobil nomi.

    Misollar: `iPhone 15` → "aypon o'n besh", "ayfon 15";
    `Samsung Galaxy A54` → "samsung galaksi a54", "samsung a54".

    Buni `pg_trgm` o'rnini bosuvchi deb tushunmaslik kerak: trigram
    *imlo xatosini* kechiradi, sinonim esa *butunlay boshqa so'zni* bog'laydi
    ("muzlatgich" → "холодильник"). Ikkisi birga ishlaydi.
    """

    product = models.ForeignKey(
        Product,
        on_delete=models.CASCADE,  # sinonim mahsulotsiz ma'nosiz
        related_name="synonyms",
        verbose_name=_("mahsulot"),
    )
    term = models.CharField(_("atama"), max_length=200, db_index=True)
    source = models.CharField(
        _("manba"),
        max_length=20,
        choices=SynonymSource.choices,
        default=SynonymSource.MANUAL,
    )
    is_approved = models.BooleanField(
        _("tasdiqlangan"),
        default=True,
        help_text=_("AI o'rgangan sinonimlar nazoratchi tasdig'ini kutadi."),
    )

    class Meta:
        verbose_name = _("mahsulot sinonimi")
        verbose_name_plural = _("mahsulot sinonimlari")
        constraints = [
            models.UniqueConstraint(
                fields=["product", "term"],
                name="synonym_unique_per_product",
            ),
        ]
        indexes = [
            GinIndex(
                fields=["term"],
                name="synonym_term_trgm_idx",
                opclasses=["gin_trgm_ops"],
            ),
        ]

    def __str__(self) -> str:
        return f"{self.term} → {self.product.sku}"


class PriceChange(ImmutableModel):
    """Narx o'zgarishlari daftari — append-only (konstitutsiya 4-band).

    **Nega o'zgarmas.** Nizo paytida ("men 200 mingga kelishgandim") narx
    tarixini tahrirlab bo'lmasligi kerak. Yozuv faqat qo'shiladi, hech qachon
    o'zgartirilmaydi va o'chirilmaydi — DB triggeri bilan himoyalangan.

    Buyurtma qatori narxni **o'z ichida nusxa** qilib saqlaydi (snapshot), shuning
    uchun katalog narxi keyin o'zgarsa ham eski buyurtma summasi buzilmaydi.
    """

    product = models.ForeignKey(
        Product,
        on_delete=models.PROTECT,  # tarix yo'qolmaydi
        related_name="price_changes",
        verbose_name=_("mahsulot"),
    )
    old_price = models.DecimalField(_("eski narx"), max_digits=14, decimal_places=2)
    new_price = models.DecimalField(_("yangi narx"), max_digits=14, decimal_places=2)
    changed_by_label = models.CharField(
        _("o'zgartirgan aktor"),
        max_length=100,
        help_text=_("Actor.label — kim o'zgartirgani (STRIDE-R: rad eta olmaslik)."),
    )
    reason = models.CharField(_("sabab"), max_length=300)

    class Meta:
        verbose_name = _("narx o'zgarishi")
        verbose_name_plural = _("narx o'zgarishlari")
        constraints = [
            models.CheckConstraint(
                condition=Q(old_price__gte=0) & Q(new_price__gte=0),
                name="price_change_non_negative",
            ),
            # Narx o'zgarmagan yozuv ma'nosiz — daftarni shovqin bilan to'ldiradi
            models.CheckConstraint(
                condition=~Q(old_price=models.F("new_price")),
                name="price_change_must_differ",
            ),
        ]
        indexes = [
            models.Index(fields=["product", "-created_at"], name="price_change_product_time_idx"),
        ]

    def __str__(self) -> str:
        return f"{self.product.sku}: {self.old_price} → {self.new_price}"
