import os
import sys
from pathlib import Path


def setup_django() -> None:
    root = Path(__file__).resolve().parent.parent
    web_path = root / "web"
    if str(web_path) not in sys.path:
        sys.path.insert(0, str(web_path))
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
    import django

    django.setup()
