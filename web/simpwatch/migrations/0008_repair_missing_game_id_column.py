from django.db import migrations, models


def add_game_id_column_if_missing(apps, schema_editor):
    table_name = "simpwatch_simpevent"
    connection = schema_editor.connection

    with connection.cursor() as cursor:
        existing_columns = {
            col.name
            for col in connection.introspection.get_table_description(
                cursor,
                table_name,
            )
        }

    if "game_id" in existing_columns:
        return

    SimpEvent = apps.get_model("simpwatch", "SimpEvent")
    field = models.CharField(max_length=255, blank=True, default="")
    field.set_attributes_from_name("game_id")
    schema_editor.add_field(SimpEvent, field)


class Migration(migrations.Migration):

    dependencies = [
        ("simpwatch", "0007_simpevent_death_and_game_name"),
    ]

    operations = [
        migrations.RunPython(
            add_game_id_column_if_missing,
            reverse_code=migrations.RunPython.noop,
        ),
    ]
