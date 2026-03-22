from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("simpwatch", "0002_rename_indexes"),
    ]

    operations = [
        migrations.AddField(
            model_name="simpevent",
            name="reason",
            field=models.TextField(blank=True),
        ),
    ]
