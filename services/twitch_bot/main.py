import os
import time

from twitchio.ext import commands
from twitchio import errors as twitch_errors
from asgiref.sync import sync_to_async

from services.common_setup import setup_django


setup_django()

from simpwatch.models import Identity, SimpEvent  # noqa: E402
from simpwatch.command_parsing import (  # noqa: E402
    parse_bot_mention_command,
    parse_twitch_bamder_reason,
    parse_twitch_reason,
    parse_twitch_target,
)
from simpwatch.scoring import (  # noqa: E402
    IdentityInput,
    get_leaderboard_entries,
    get_or_create_named_person,
    get_or_create_twitch_target,
    get_person_score_and_rank,
    get_score_and_rank_for_person,
    normalize_username,
    register_simp,
)


class TwitchSimpBot(commands.Bot):
    def __init__(self) -> None:
        channels = [
            c.strip().lower()
            for c in os.getenv("TWITCH_CHANNELS", "").split(",")
            if c.strip()
        ]
        self._reply_channels: set[str] = {
            c.strip().lower()
            for c in os.getenv("TWITCH_REPLY_CHANNELS", "").split(",")
            if c.strip()
        }
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

        bot_cmd = parse_bot_mention_command(content, self.nick)
        if bot_cmd is not None:
            command, args = bot_cmd
            await self._handle_bot_command(message, command, args)
            return

        lowered = content.lower()
        if not lowered.startswith("!simp") and not lowered.startswith("!bamder"):
            return

        actor_input = IdentityInput(
            platform=Identity.Platform.TWITCH,
            platform_user_id=str(message.author.id),
            username=message.author.name,
            display_name=message.author.display_name or message.author.name,
        )

        if lowered.startswith("!bamder"):
            target_person = await sync_to_async(get_or_create_named_person)("pamder")
            reason = parse_twitch_bamder_reason(content)
            event_type = str(SimpEvent.EventType.BAMDER)
        else:
            target_username = parse_twitch_target(content)
            if target_username:
                target_person = await sync_to_async(get_or_create_twitch_target)(
                    target_username
                )
            else:
                broadcaster = message.channel.name
                target_person = await sync_to_async(get_or_create_twitch_target)(
                    broadcaster
                )
            reason = parse_twitch_reason(content)
            event_type = str(SimpEvent.EventType.SIMP)

        event = await sync_to_async(register_simp)(
            actor=actor_input,
            target=target_person,
            platform=Identity.Platform.TWITCH,
            event_type=event_type,
            source=message.channel.name,
            reason=reason,
            raw_content=content,
            message_id=str(message.id),
            dedupe_key=f"twitch:{message.id}",
        )
        if event:
            print(
                f"event registered twitch type={event_type} actor={actor_input.username} target={target_person.name} id={event.id}"
            )
            if message.channel.name in self._reply_channels:
                score, rank = await sync_to_async(get_score_and_rank_for_person)(
                    target_person
                )
                if rank is not None:
                    await message.channel.send(
                        f"{target_person.name} is ranked #{rank} with {score} point(s)."
                    )
                else:
                    await message.channel.send(
                        f"{target_person.name} has been registered!"
                    )

    async def _handle_bot_command(self, message, command: str, args: list[str]) -> None:
        channel = message.channel

        if command == "simpcheck":
            target_username = (
                normalize_username(args[0]) if args else message.channel.name
            )
            score, rank = await sync_to_async(get_person_score_and_rank)(
                target_username
            )
            if rank is None:
                await channel.send(f"{target_username} has no score yet.")
            else:
                await channel.send(
                    f"{target_username} is ranked #{rank} with {score} point(s)."
                )

        elif command == "standings":
            limit = 3
            if args:
                try:
                    limit = max(1, min(int(args[0]), 10))
                except ValueError:
                    pass
            entries = await sync_to_async(get_leaderboard_entries)()
            top = entries[:limit]
            if not top:
                await channel.send("No standings yet!")
            else:
                parts = [
                    f"#{i + 1} {row['person'].name} ({row['points']} pts)"
                    for i, row in enumerate(top)
                ]
                await channel.send(f"Top {len(top)} simps: " + ", ".join(parts))


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
