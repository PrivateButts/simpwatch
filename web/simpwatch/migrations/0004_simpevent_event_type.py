from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("simpwatch", "0003_simpevent_reason"),
    ]

    operations = [
        migrations.AddField(
            model_name="simpevent",
            name="event_type",
            field=models.CharField(
                choices=[("simp", "Simp"), ("bamder", "Bamder")],
                default="simp",
                max_length=20,
            ),
        ),
    ]
