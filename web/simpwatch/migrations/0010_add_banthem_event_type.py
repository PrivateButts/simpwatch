from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("simpwatch", "0009_scoreadjustment_types"),
    ]

    operations = [
        migrations.AlterField(
            model_name="simpevent",
            name="event_type",
            field=models.CharField(
                choices=[
                    ("simp", "Simp"),
                    ("bamder", "Bamder"),
                    ("banthem", "Banthem"),
                    ("death", "Death"),
                ],
                default="simp",
                max_length=20,
            ),
        ),
        migrations.AlterField(
            model_name="scoreadjustment",
            name="adjustment_type",
            field=models.CharField(
                choices=[
                    ("simp", "Simp"),
                    ("bamder", "Bamder"),
                    ("banthem", "Banthem"),
                    ("death", "Death"),
                ],
                default="simp",
                max_length=20,
            ),
        ),
    ]
