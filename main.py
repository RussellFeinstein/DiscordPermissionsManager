import os
import sys
import discord
from discord.ext import commands
from dotenv import load_dotenv

load_dotenv()

_token = os.environ.get("DISCORD_BOT_TOKEN")
if not _token:
    print("ERROR: DISCORD_BOT_TOKEN is not set.")
    print("Add it to your .env file and restart.")
    sys.exit(1)

intents = discord.Intents.default()
intents.members = True
intents.guilds = True


class Bot(commands.Bot):
    def __init__(self):
        super().__init__(command_prefix=commands.when_mentioned, intents=intents)

    async def setup_hook(self):
        await self.load_extension("cogs.permissions")
        await self.load_extension("cogs.roles")
        await self.load_extension("cogs.admin")

        dev_guild_id = os.environ.get("DISCORD_GUILD_ID")
        if dev_guild_id:
            # Dev mode: sync only to the specified guild for instant updates.
            # Also clear any stale global commands so they don't show up as duplicates.
            guild = discord.Object(id=int(dev_guild_id))
            self.tree.copy_global_to(guild=guild)
            await self.tree.sync(guild=guild)
            self.tree.clear_commands(guild=None)
            await self.tree.sync()
            print(f"Commands synced to dev guild {dev_guild_id}.")
        else:
            # Production: sync globally (takes ~1 hour to propagate to all servers).
            await self.tree.sync()
            print("Global slash commands synced.")

    async def on_ready(self):
        print(f"Logged in as {self.user} (ID: {self.user.id})")
        print(f"Serving {len(self.guilds)} server(s).")

        # In production (no DISCORD_GUILD_ID), clear any stale guild-specific
        # commands left over from dev-mode testing so they don't appear twice.
        if not os.environ.get("DISCORD_GUILD_ID"):
            cleared = 0
            for guild in self.guilds:
                self.tree.clear_commands(guild=guild)
                await self.tree.sync(guild=guild)
                cleared += 1
            if cleared:
                print(f"Cleared guild-specific commands from {cleared} server(s).")

    async def on_guild_join(self, guild: discord.Guild):
        """Send a welcome DM to the person who invited the bot."""
        embed = discord.Embed(
            title="Thanks for adding Permissions Manager!",
            description=(
                f"You've added the bot to **{guild.name}**. "
                "Everything is configured through Discord slash commands.\n\n"
                "**Role management** (works right away):\n"
                "• `/bundle create` — create a role bundle\n"
                "• `/assign @member <bundle>` — assign a bundle to a member\n"
                "• `/exclusive-group create` — create a mutually-exclusive role group\n\n"
                "**Permission sync**:\n"
                "• `/category baseline-set` — set the @everyone permission per category\n"
                "• `/access-rule add-category` — grant a role access to a category\n"
                "• `/access-rule add-channel` — grant a role access to a specific channel\n"
                "• `/sync-permissions` — apply all rules to Discord\n\n"
                "Run `/status` at any time to see your current configuration."
            ),
            color=discord.Color.blurple(),
        )

        inviter = None
        try:
            async for entry in guild.audit_logs(
                action=discord.AuditLogAction.bot_add, limit=5
            ):
                if entry.target.id == self.user.id:
                    inviter = entry.user
                    break
        except discord.Forbidden:
            pass  # No audit log access — skip

        if inviter is None:
            return

        try:
            await inviter.send(embed=embed)
        except discord.Forbidden:
            pass  # Inviter has DMs closed — skip silently


bot = Bot()
bot.run(_token)
