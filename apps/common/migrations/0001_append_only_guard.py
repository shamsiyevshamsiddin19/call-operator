"""Append-only (o'zgarmaslik) kafolatining ma'lumotlar bazasi darajasidagi poydevori.

Konstitutsiya 4-bandi moliyaviy va ombor yozuvlarini tahrirlashni taqiqlaydi.
Python darajasidagi himoya (`apps.common.models.ImmutableModel`) **yetarli emas**:
uni quyidagi yo'llar bilan aylanib o'tish mumkin —

    StockMovement.objects.filter(...).update(on_hand_delta=0)   # save() chaqirilmaydi
    StockMovement.objects.filter(...).delete()                  # delete() chaqirilmaydi
    cursor.execute("UPDATE inventory_stockmovement SET ...")     # ORM butunlay aylanib o'tiladi

Shu sababli haqiqiy kafolat shu yerda — ma'lumotlar bazasi triggerida.
Bu funksiya bir marta yaratiladi va uni har bir o'zgarmas jadval ishlatadi (DRY).

Diqqat: trigger `FOR EACH ROW` bo'lgani uchun `TRUNCATE` ni to'smaydi.
Bu ataylab: Django test infratuzilmasi (`TransactionTestCase`) jadvallarni
`TRUNCATE` bilan tozalaydi. Ishlab chiqarish muhitida esa ilova foydalanuvchisiga
`TRUNCATE` huquqi berilmaydi (deploy hujjatiga qarang).
"""

from django.db import migrations

CREATE_GUARD = """
CREATE OR REPLACE FUNCTION call_operator_forbid_mutation()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
    RAISE EXCEPTION
        'APPEND_ONLY: % jadvalida % amali taqiqlanadi. '
        'Tuzatish uchun teskari yo''nalishdagi yangi yozuv (storno) qo''shing.',
        TG_TABLE_NAME, TG_OP
        USING ERRCODE = 'restrict_violation';
END;
$$;
"""

DROP_GUARD = "DROP FUNCTION IF EXISTS call_operator_forbid_mutation();"


class Migration(migrations.Migration):
    """Barcha o'zgarmas jadvallar shu migratsiyaga bog'lanadi."""

    initial = True
    dependencies: list[tuple[str, str]] = []

    operations = [
        migrations.RunSQL(sql=CREATE_GUARD, reverse_sql=DROP_GUARD),
    ]
