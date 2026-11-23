"""Ovozli izlash uchun trigram indekslar va narx daftarining o'zgarmasligi.

**Nega trigram.** Ovozli so'rovda mijozning aytgani matnga aylantirilganda
xato bo'lishi tabiiy: "samsung galaksi a54", "aypon 15 pro". Oddiy
`name__icontains` so'rovi bunday yozilishni topmaydi, `pg_trgm` esa
uch harfli bo'laklar o'xshashligi bo'yicha topadi.

Bu mahsulot izlashning **ikkinchi qatlami**: birinchi qatlam — `ProductSynonym`
(butunlay boshqa so'z: "muzlatgich" → "холодильник"), ikkinchisi — trigram
(imlo xatosi). Ikkisi birga ishlaydi, biri ikkinchisining o'rnini bosmaydi.
"""

from django.contrib.postgres.indexes import GinIndex
from django.contrib.postgres.operations import TrigramExtension
from django.db import migrations

CREATE_TRIGGER = """
CREATE TRIGGER pricechange_append_only
    BEFORE UPDATE OR DELETE ON catalog_pricechange
    FOR EACH ROW
    EXECUTE FUNCTION call_operator_forbid_mutation();
"""

DROP_TRIGGER = "DROP TRIGGER IF EXISTS pricechange_append_only ON catalog_pricechange;"


class Migration(migrations.Migration):
    dependencies = [
        ("catalog", "0001_initial"),
        ("common", "0001_append_only_guard"),
    ]

    operations = [
        # Kengaytma indekslardan OLDIN o'rnatilishi shart: `gin_trgm_ops`
        # operator klassi shu kengaytma bilan keladi.
        TrigramExtension(),
        migrations.AddIndex(
            model_name="product",
            index=GinIndex(
                fields=["name"],
                name="product_name_trgm_idx",
                opclasses=["gin_trgm_ops"],
            ),
        ),
        migrations.AddIndex(
            model_name="productsynonym",
            index=GinIndex(
                fields=["term"],
                name="synonym_term_trgm_idx",
                opclasses=["gin_trgm_ops"],
            ),
        ),
        migrations.RunSQL(sql=CREATE_TRIGGER, reverse_sql=DROP_TRIGGER),
    ]
