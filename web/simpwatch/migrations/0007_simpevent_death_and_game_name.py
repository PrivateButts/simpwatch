from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("simpwatch", "0006_add_twitch_bot_grant"),
    ]

    operations = [
        migrations.AddField(
            model_name="simpevent",
            name="game_id",
            field=models.CharField(blank=True, max_length=255),
        ),
        migrations.AddField(
            model_name="simpevent",
            name="game_name",
            field=models.CharField(blank=True, max_length=255),
        ),
        migrations.AlterField(
            model_name="simpevent",
            name="event_type",
            field=models.CharField(
                choices=[("simp", "Simp"), ("bamder", "Bamder"), ("death", "Death")],
                default="simp",
                max_length=20,
            ),
        ),
    ]
