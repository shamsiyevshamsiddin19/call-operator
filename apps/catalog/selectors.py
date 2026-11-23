"""Katalog o'qish amallari (CQS — Query/Read qatlami).

**Tannarx bu qatlamdan chiqmaydi.** `cost_price` maydoni so'rovga umuman
qo'shilmaydi (`.defer()` / aniq `values()`), shuning uchun u AI kontekstiga
yoki javobga tasodifan tushishi **mumkin emas**. Bu "frontendda yashirish"
emas, ma'lumotni manbada bermaslik (STRIDE-I).

**Ovozli izlash uch qatlamli.** Mijozning aytgani matnga aylantirilganda xato
bo'lishi tabiiy, shuning uchun bitta usul yetarli emas:

1. **SKU aniq moslik** — mijoz kod aytsa (kamdan-kam, lekin aniq).
2. **Sinonim** — butunlay boshqa so'z: "muzlatgich" -> "холодильник",
   "aypon" -> "iPhone". Trigram bunday bog'lanishni topa olmaydi.
3. **Trigram o'xshashlik** — imlo xatosi: "samsung galaksi" -> "Samsung Galaxy".
   Sinonim jadvali bunday variantlarning hammasini qamrab olmaydi.
"""

from dataclasses import dataclass
from decimal import Decimal

from django.contrib.postgres.search import TrigramSimilarity
from django.db.models import Case, F, FloatField, QuerySet, Value, When
from django.db.models.functions import Greatest

from apps.catalog.models import Product

# Trigram o'xshashligining minimal chegarasi. Pastroq qilsak begona mahsulotlar
# chiqadi va AI mijozga noto'g'ri narx aytadi; balandroq qilsak imlo xatosi
# bo'lgan so'rov hech narsa topmaydi. Qiymat real qo'ng'iroq so'rovlarida
# o'lchab sozlanadi.
TRIGRAM_THRESHOLD = 0.25

# Nechta variant qaytariladi. Ovozli suhbatda 3 dan ko'p variantni sanab berish
# mijozni chalkashtiradi (Hick qonuni) — AI eng yaqin uchtasini aytadi.
DEFAULT_SEARCH_LIMIT = 3


@dataclass(frozen=True, slots=True)
class ProductMatch:
    """Izlash natijasi — AI ga uzatiladigan yengil obyekt.

    Tannarx bu yerda **yo'q**: strukturaga umuman kiritilmagan, shuning uchun
    uni oshkor qilib yuborish texnik jihatdan imkonsiz.
    """

    product_id: int
    sku: str
    name: str
    price: Decimal
    unit: str
    score: float


def search_products(query: str, *, limit: int = DEFAULT_SEARCH_LIMIT) -> list[ProductMatch]:
    """Ovozli so'rov bo'yicha mahsulot izlaydi.

    Uch usulning eng yaxshi natijasi (`Greatest`) bo'yicha tartiblanadi —
    shu sababli sinonim bo'yicha topilgan aniq moslik trigram bo'yicha
    taxminiy mosliklardan yuqorida turadi.

    Args:
        query: mijozning aytgani (matnga aylantirilgan).
        limit: qaytariladigan variantlar soni.

    Returns:
        Moslik darajasi bo'yicha kamayish tartibida ro'yxat. Hech narsa
        topilmasa bo'sh ro'yxat (bu xatolik emas — AI "topolmadim" deydi).
    """
    cleaned = query.strip()
    if not cleaned:
        return []

    rows = (
        Product.objects.filter(is_active=True)
        .annotate(
            # Aniq mosliklar maksimal ball oladi: ular taxminiy trigram
            # natijalaridan har doim yuqorida turishi kerak.
            sku_score=Case(
                When(sku__iexact=cleaned, then=Value(1.0)),
                default=Value(0.0),
                output_field=FloatField(),
            ),
            synonym_exact_score=Case(
                When(
                    synonyms__term__iexact=cleaned,
                    synonyms__is_approved=True,
                    then=Value(1.0),
                ),
                default=Value(0.0),
                output_field=FloatField(),
            ),
            name_score=TrigramSimilarity("name", cleaned),
            synonym_score=TrigramSimilarity("synonyms__term", cleaned),
        )
        .annotate(
            score=Greatest(
                "sku_score",
                "synonym_exact_score",
                "name_score",
                "synonym_score",
                output_field=FloatField(),
            )
        )
        .filter(score__gte=TRIGRAM_THRESHOLD)
        # `cost_price` tanlanmaydi — ro'yxatda u yo'q.
        .values("id", "sku", "name", "price", "unit", "score")
        .order_by("-score", "name")
        .distinct()[: limit * 3]
    )

    # `distinct()` sinonim JOIN tufayli takroriy qatorlar berishi mumkin
    # (bitta mahsulotning bir nechta sinonimi mos kelsa), shuning uchun
    # mahsulot bo'yicha eng yuqori ballni qoldiramiz.
    best: dict[int, ProductMatch] = {}
    for row in rows:
        product_id = int(row["id"])
        score = float(row["score"] or 0.0)
        existing = best.get(product_id)
        if existing is None or score > existing.score:
            best[product_id] = ProductMatch(
                product_id=product_id,
                sku=str(row["sku"]),
                name=str(row["name"]),
                price=row["price"],
                unit=str(row["unit"]),
                score=score,
            )

    return sorted(best.values(), key=lambda m: (-m.score, m.name))[:limit]


def get_product_for_sale(product_id: int) -> ProductMatch | None:
    """Sotuv uchun mahsulot ma'lumoti (tannarxsiz).

    `None` qaytaradi agar mahsulot mavjud bo'lmasa yoki arxivlangan bo'lsa —
    arxivlangan mahsulotga yangi buyurtma olinmaydi, ammo eski buyurtmalarda
    u ko'rinishda qoladi.
    """
    row = (
        Product.objects.filter(pk=product_id, is_active=True)
        .values("id", "sku", "name", "price", "unit")
        .first()
    )
    if row is None:
        return None

    return ProductMatch(
        product_id=int(row["id"]),
        sku=str(row["sku"]),
        name=str(row["name"]),
        price=row["price"],
        unit=str(row["unit"]),
        score=1.0,
    )


def products_for_staff() -> QuerySet[Product]:
    """Xodimlar uchun to'liq katalog so'rovi.

    Bu yerda `cost_price` mavjud, shuning uchun natijani ishlatuvchi kod
    `Actor.can_view_cost_price` ni tekshirishi **shart**. AI yo'lida bu
    funksiya hech qachon chaqirilmaydi.
    """
    qs: QuerySet[Product] = Product.objects.select_related("category").annotate(
        margin=F("price") - F("cost_price"),
    )
    return qs
