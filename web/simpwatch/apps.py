from django.apps import AppConfig


class SimpwatchConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "simpwatch"

    def ready(self) -> None:
        from . import signals  # noqa: F401
