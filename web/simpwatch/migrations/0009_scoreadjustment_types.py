from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("simpwatch", "0008_repair_missing_game_id_column"),
    ]

    operations = [
        migrations.AddField(
            model_name="scoreadjustment",
            name="adjustment_type",
            field=models.CharField(
                choices=[("simp", "Simp"), ("bamder", "Bamder"), ("death", "Death")],
                default="simp",
                max_length=20,
            ),
        ),
        migrations.AddField(
            model_name="scoreadjustment",
            name="game_id",
            field=models.CharField(blank=True, max_length=255),
        ),
        migrations.AddField(
            model_name="scoreadjustment",
            name="game_name",
            field=models.CharField(blank=True, max_length=255),
        ),
    ]
