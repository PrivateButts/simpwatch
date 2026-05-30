from django.db import migrations, models


def add_scoreadjustment_columns_if_missing(apps, schema_editor):
    table_name = "simpwatch_scoreadjustment"
    connection = schema_editor.connection

    with connection.cursor() as cursor:
        existing_columns = {
            col.name
            for col in connection.introspection.get_table_description(
                cursor,
                table_name,
            )
        }

    ScoreAdjustment = apps.get_model("simpwatch", "ScoreAdjustment")

    missing_fields = []
    if "adjustment_type" not in existing_columns:
        field = models.CharField(
            choices=[
                ("simp", "Simp"),
                ("bamder", "Bamder"),
                ("banthem", "Banthem"),
                ("death", "Death"),
            ],
            default="simp",
            max_length=20,
        )
        field.set_attributes_from_name("adjustment_type")
        missing_fields.append(field)

    if "game_id" not in existing_columns:
        field = models.CharField(max_length=255, blank=True, default="")
        field.set_attributes_from_name("game_id")
        missing_fields.append(field)

    if "game_name" not in existing_columns:
        field = models.CharField(max_length=255, blank=True, default="")
        field.set_attributes_from_name("game_name")
        missing_fields.append(field)

    for field in missing_fields:
        schema_editor.add_field(ScoreAdjustment, field)


class Migration(migrations.Migration):
    dependencies = [
        ("simpwatch", "0010_add_banthem_event_type"),
    ]

    operations = [
        migrations.RunPython(
            add_scoreadjustment_columns_if_missing,
            reverse_code=migrations.RunPython.noop,
        ),
    ]
