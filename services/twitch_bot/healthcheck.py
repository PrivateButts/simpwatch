import os
import sys
import time
from pathlib import Path


HEARTBEAT_FILE = Path(os.getenv("TWITCH_HEARTBEAT_FILE", "/tmp/twitch_bot_heartbeat"))
MAX_AGE_SECONDS = int(os.getenv("TWITCH_HEALTHCHECK_MAX_AGE_SECONDS", "90"))


def mark_healthy() -> None:
    HEARTBEAT_FILE.parent.mkdir(parents=True, exist_ok=True)
    HEARTBEAT_FILE.write_text(str(int(time.time())), encoding="ascii")


def clear_heartbeat() -> None:
    HEARTBEAT_FILE.unlink(missing_ok=True)


def main() -> int:
    required = {
        "TWITCH_CLIENT_ID": os.getenv("TWITCH_CLIENT_ID", "").strip(),
        "TWITCH_CLIENT_SECRET": os.getenv("TWITCH_CLIENT_SECRET", "").strip(),
        "TWITCH_BOT_ID": os.getenv("TWITCH_BOT_ID", "").strip(),
        "TWITCH_BOT_ACCESS_TOKEN": os.getenv("TWITCH_BOT_ACCESS_TOKEN", "").strip(),
        "TWITCH_BOT_REFRESH_TOKEN": os.getenv("TWITCH_BOT_REFRESH_TOKEN", "").strip(),
    }
    missing = [name for name, value in required.items() if not value]
    if missing:
        print(f"missing required env vars: {', '.join(missing)}", file=sys.stderr)
        return 1

    try:
        heartbeat_stat = HEARTBEAT_FILE.stat()
    except FileNotFoundError:
        print(f"heartbeat file missing: {HEARTBEAT_FILE}", file=sys.stderr)
        return 1

    age_seconds = time.time() - heartbeat_stat.st_mtime
    if age_seconds > MAX_AGE_SECONDS:
        print(
            (f"heartbeat stale: age={age_seconds:.0f}s max_age={MAX_AGE_SECONDS}s"),
            file=sys.stderr,
        )
        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
