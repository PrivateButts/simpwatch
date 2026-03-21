import os
import time

import discord
from discord import app_commands
from discord.errors import LoginFailure
from asgiref.sync import sync_to_async

from services.common_setup import setup_django


setup_django()

from simpwatch.models import Identity  # noqa: E402
from simpwatch.scoring import IdentityInput, get_or_create_identity, register_simp  # noqa: E402


class DiscordSimpBot(discord.Client):
    def __init__(self):
        intents = discord.Intents.default()
        super().__init__(intents=intents)
        self.tree = app_commands.CommandTree(self)

    async def setup_hook(self):
        guild_id = os.getenv("DISCORD_GUILD_ID", "").strip()
        if guild_id:
            guild = discord.Object(id=int(guild_id))
            self.tree.copy_global_to(guild=guild)
            await self.tree.sync(guild=guild)
        else:
            await self.tree.sync()

    async def on_ready(self):
        print(f"Discord bot ready: {self.user}")


bot = DiscordSimpBot()


@bot.tree.command(name="simp", description="Register a simp callout for someone")
@app_commands.describe(target="Who should receive the simp point")
async def simp(interaction: discord.Interaction, target: discord.Member):
    actor_user = interaction.user

    actor_input = IdentityInput(
        platform=Identity.Platform.DISCORD,
        platform_user_id=str(actor_user.id),
        username=actor_user.name,
        display_name=actor_user.display_name or actor_user.name,
    )

    target_identity = await sync_to_async(get_or_create_identity)(
        IdentityInput(
            platform=Identity.Platform.DISCORD,
            platform_user_id=str(target.id),
            username=target.name,
            display_name=target.display_name or target.name,
        )
    )

    event = await sync_to_async(register_simp)(
        actor=actor_input,
        target=target_identity.person,
        platform=Identity.Platform.DISCORD,
        source=str(interaction.guild_id or "dm"),
        raw_content=f"/simp target:{target.name}",
        message_id=str(interaction.id),
        dedupe_key=f"discord:{interaction.id}",
    )

    if event:
        await interaction.response.send_message(
            f"Simp registered: {actor_user.mention} -> {target.mention} (+{event.points})"
        )
    else:
        await interaction.response.send_message(
            "Simp ignored due to cooldown.",
            ephemeral=True,
        )


if __name__ == "__main__":
    token = os.getenv("DISCORD_BOT_TOKEN", "")
    if not token.strip():
        print("Discord bot disabled: set DISCORD_BOT_TOKEN")
        while True:
            time.sleep(300)

    while True:
        try:
            bot.run(token)
        except LoginFailure:
            print("Discord bot auth failed: verify DISCORD_BOT_TOKEN")
            time.sleep(60)
        except Exception as exc:
            print(f"Discord bot crashed: {exc}")
            time.sleep(5)
