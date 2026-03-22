import os
import time

from twitchio.ext import commands
from twitchio import errors as twitch_errors
from asgiref.sync import sync_to_async

from services.common_setup import setup_django


setup_django()

from simpwatch.models import Identity  # noqa: E402
from simpwatch.scoring import IdentityInput, get_or_create_twitch_target, register_simp  # noqa: E402


def _parse_target(content: str) -> str | None:
    parts = content.strip().split()
    if len(parts) < 2:
        return None
    candidate = parts[1]
    if not candidate.startswith("@"):
        return None
    username = candidate.lstrip("@").strip().lower()
    return username or None


def _parse_reason(content: str) -> str:
    parts = content.strip().split()
    if len(parts) < 3:
        return ""
    start_index = 1
    if parts[1].startswith("@"):
        start_index = 2
    if len(parts) <= start_index:
        return ""
    if parts[start_index].lower() not in {"reason", "because"}:
        return ""
    if len(parts) <= start_index + 1:
        return ""
    return " ".join(parts[start_index + 1 :]).strip()


class TwitchSimpBot(commands.Bot):
    def __init__(self) -> None:
        channels = [
            c.strip().lower()
            for c in os.getenv("TWITCH_CHANNELS", "").split(",")
            if c.strip()
        ]
        super().__init__(
            token=os.getenv("TWITCH_OAUTH_TOKEN", ""),
            prefix="!",
            initial_channels=channels,
            nick=os.getenv("TWITCH_BOT_USERNAME", ""),
        )

    async def event_ready(self):
        print(f"Twitch bot ready: {self.nick}")

    async def event_message(self, message):
        if message.echo:
            return
        content = (message.content or "").strip()
        if not content.lower().startswith("!simp"):
            return

        actor_input = IdentityInput(
            platform=Identity.Platform.TWITCH,
            platform_user_id=str(message.author.id),
            username=message.author.name,
            display_name=message.author.display_name or message.author.name,
        )

        target_username = _parse_target(content)
        if target_username:
            target_person = await sync_to_async(get_or_create_twitch_target)(
                target_username
            )
        else:
            broadcaster = message.channel.name
            target_person = await sync_to_async(get_or_create_twitch_target)(
                broadcaster
            )

        reason = _parse_reason(content)

        event = await sync_to_async(register_simp)(
            actor=actor_input,
            target=target_person,
            platform=Identity.Platform.TWITCH,
            source=message.channel.name,
            reason=reason,
            raw_content=content,
            message_id=str(message.id),
            dedupe_key=f"twitch:{message.id}",
        )
        if event:
            print(
                f"simp registered twitch actor={actor_input.username} target={target_person.name} id={event.id}"
            )


if __name__ == "__main__":
    token = os.getenv("TWITCH_OAUTH_TOKEN", "").strip()
    channels = os.getenv("TWITCH_CHANNELS", "").strip()
    if not token or not channels:
        print("Twitch bot disabled: set TWITCH_OAUTH_TOKEN and TWITCH_CHANNELS")
        while True:
            time.sleep(300)

    while True:
        try:
            bot = TwitchSimpBot()
            bot.run()
        except twitch_errors.AuthenticationError:
            print("Twitch bot auth failed: verify TWITCH_OAUTH_TOKEN")
            time.sleep(60)
        except Exception as exc:
            print(f"Twitch bot crashed: {exc}")
            time.sleep(5)
