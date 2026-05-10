from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("simpwatch", "0004_simpevent_event_type"),
    ]

    operations = [
        migrations.CreateModel(
            name="TwitchBroadcasterGrant",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                ("username", models.CharField(max_length=255, unique=True)),
                (
                    "broadcaster_user_id",
                    models.CharField(max_length=255, unique=True),
                ),
                ("access_token", models.TextField()),
                ("refresh_token", models.TextField()),
                ("scopes", models.TextField(blank=True)),
                ("expires_at", models.DateTimeField(blank=True, null=True)),
                ("is_active", models.BooleanField(default=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
            ],
            options={
                "indexes": [
                    models.Index(
                        fields=["is_active", "username"],
                        name="simpwatch_t_is_acti_ee8f3d_idx",
                    )
                ],
            },
        ),
    ]