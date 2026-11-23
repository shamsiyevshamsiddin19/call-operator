"""Audit jurnalini o'zgarmas qilish (STRIDE-T — Tampering himoyasi).

Audit jurnali tahrirlanadigan bo'lsa, u audit bo'lmaydi: huquqni suiiste'mol
qilgan odam o'z izini o'chirib tashlaydi. Shu sababli bu jadvalga yozuv faqat
qo'shiladi.
"""

from django.db import migrations

CREATE_TRIGGER = """
CREATE TRIGGER auditevent_append_only
    BEFORE UPDATE OR DELETE ON audit_auditevent
    FOR EACH ROW
    EXECUTE FUNCTION call_operator_forbid_mutation();
"""

DROP_TRIGGER = "DROP TRIGGER IF EXISTS auditevent_append_only ON audit_auditevent;"


class Migration(migrations.Migration):
    dependencies = [
        ("audit", "0001_initial"),
        ("common", "0001_append_only_guard"),
    ]

    operations = [
        migrations.RunSQL(sql=CREATE_TRIGGER, reverse_sql=DROP_TRIGGER),
    ]
