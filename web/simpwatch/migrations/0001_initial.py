from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    initial = True

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="Person",
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
                ("name", models.CharField(max_length=255)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
            ],
        ),
        migrations.CreateModel(
            name="ScoringConfig",
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
                ("cooldown_seconds", models.PositiveIntegerField(default=0)),
                ("default_points", models.IntegerField(default=1)),
                ("updated_at", models.DateTimeField(auto_now=True)),
            ],
        ),
        migrations.CreateModel(
            name="Identity",
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
                (
                    "platform",
                    models.CharField(
                        choices=[("twitch", "Twitch"), ("discord", "Discord")],
                        max_length=20,
                    ),
                ),
                ("platform_user_id", models.CharField(max_length=255)),
                ("username", models.CharField(max_length=255)),
                ("display_name", models.CharField(blank=True, max_length=255)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                (
                    "person",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="identities",
                        to="simpwatch.person",
                    ),
                ),
            ],
            options={
                "unique_together": {("platform", "platform_user_id")},
            },
        ),
        migrations.CreateModel(
            name="SimpEvent",
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
                (
                    "platform",
                    models.CharField(
                        choices=[("twitch", "Twitch"), ("discord", "Discord")],
                        max_length=20,
                    ),
                ),
                ("source", models.CharField(max_length=255)),
                ("points", models.IntegerField(default=1)),
                ("raw_content", models.TextField(blank=True)),
                ("message_id", models.CharField(blank=True, max_length=255)),
                ("dedupe_key", models.CharField(blank=True, max_length=255)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                (
                    "actor_identity",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="acted_events",
                        to="simpwatch.identity",
                    ),
                ),
                (
                    "target_person",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="received_events",
                        to="simpwatch.person",
                    ),
                ),
            ],
        ),
        migrations.CreateModel(
            name="ScoreAdjustment",
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
                ("points_delta", models.IntegerField()),
                ("reason", models.CharField(max_length=500)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                (
                    "created_by",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    "target_person",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="score_adjustments",
                        to="simpwatch.person",
                    ),
                ),
            ],
        ),
        migrations.AddIndex(
            model_name="identity",
            index=models.Index(
                fields=["platform", "username"], name="simpwatch_i_platfor_b2c0d4_idx"
            ),
        ),
        migrations.AddIndex(
            model_name="simpevent",
            index=models.Index(
                fields=["created_at"], name="simpwatch_s_created_a5dd8e_idx"
            ),
        ),
        migrations.AddIndex(
            model_name="simpevent",
            index=models.Index(
                fields=["platform", "created_at"], name="simpwatch_s_platfor_dfd114_idx"
            ),
        ),
        migrations.AddIndex(
            model_name="simpevent",
            index=models.Index(
                fields=["target_person", "created_at"],
                name="simpwatch_s_target__db34f8_idx",
            ),
        ),
        migrations.AddIndex(
            model_name="scoreadjustment",
            index=models.Index(
                fields=["created_at"], name="simpwatch_s_created_d80566_idx"
            ),
        ),
    ]
