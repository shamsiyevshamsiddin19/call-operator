"""Ombor harakatlari daftarini o'zgarmas qilish (INV-04).

Bu migratsiyadan keyin `inventory_stockmovement` jadvalidagi birorta qatorni
`UPDATE` yoki `DELETE` qilish — hatto to'g'ridan-to'g'ri SQL bilan ham —
`restrict_violation` xatoligi bilan rad etiladi.
"""

from django.db import migrations

CREATE_TRIGGER = """
CREATE TRIGGER stockmovement_append_only
    BEFORE UPDATE OR DELETE ON inventory_stockmovement
    FOR EACH ROW
    EXECUTE FUNCTION call_operator_forbid_mutation();
"""

DROP_TRIGGER = "DROP TRIGGER IF EXISTS stockmovement_append_only ON inventory_stockmovement;"


class Migration(migrations.Migration):
    dependencies = [
        ("inventory", "0001_initial"),
        ("common", "0001_append_only_guard"),
    ]

    operations = [
        migrations.RunSQL(sql=CREATE_TRIGGER, reverse_sql=DROP_TRIGGER),
    ]
