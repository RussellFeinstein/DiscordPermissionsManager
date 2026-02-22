import discord
from discord import app_commands
from discord.ext import commands

from services.sync import build_permission_plan, apply_permission_plan, diff_permission_plan

# Max characters Discord allows in a single message
_DISCORD_MAX = 2000


def _chunk_lines(lines: list[str], max_len: int = _DISCORD_MAX) -> list[str]:
    """Split a list of lines into chunks that fit within Discord's message limit."""
    chunks, current = [], []
    current_len = 0
    for line in lines:
        if current_len + len(line) + 1 > max_len:
            chunks.append("\n".join(current))
            current, current_len = [], 0
        current.append(line)
        current_len += len(line) + 1
    if current:
        chunks.append("\n".join(current))
    return chunks


class SyncConfirmView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=60.0)
        self.choice: str | None = None
        self.button_interaction: discord.Interaction | None = None

    @discord.ui.button(label="Sync Now", style=discord.ButtonStyle.danger)
    async def sync_now(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.choice = "sync"
        self.button_interaction = interaction
        self.stop()

    @discord.ui.button(label="Preview Changes", style=discord.ButtonStyle.primary)
    async def preview_changes(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.choice = "preview"
        self.button_interaction = interaction
        self.stop()

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.secondary)
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.choice = "cancel"
        self.button_interaction = interaction
        self.stop()


class SyncApplyView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=60.0)
        self.confirmed: bool | None = None
        self.button_interaction: discord.Interaction | None = None

    @discord.ui.button(label="Apply", style=discord.ButtonStyle.danger)
    async def apply(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.confirmed = True
        self.button_interaction = interaction
        self.stop()

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.secondary)
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.confirmed = False
        self.button_interaction = interaction
        self.stop()


class PermissionsCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    # ------------------------------------------------------------------
    # /preview-permissions
    # ------------------------------------------------------------------
    @app_commands.command(
        name="preview-permissions",
        description="Show what /sync-permissions would change without applying anything.",
    )
    @app_commands.default_permissions(administrator=True)
    @app_commands.guild_only()
    async def preview_permissions(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True, thinking=True)

        guild = interaction.guild
        try:
            plan = build_permission_plan(guild)
        except Exception as e:
            await interaction.followup.send(f"Failed to build permission plan: `{e}`", ephemeral=True)
            return

        lines = diff_permission_plan(plan, guild)

        if not lines:
            await interaction.followup.send("No permission changes detected.", ephemeral=True)
            return

        await interaction.followup.send(
            f"**Permission preview — {len(lines)} overwrite(s)**", ephemeral=True
        )
        for chunk in _chunk_lines(lines):
            await interaction.followup.send(chunk, ephemeral=True)

    # ------------------------------------------------------------------
    # /sync-permissions
    # ------------------------------------------------------------------
    @app_commands.command(
        name="sync-permissions",
        description="Apply all configured permission levels and access rules to Discord.",
    )
    @app_commands.default_permissions(administrator=True)
    @app_commands.guild_only()
    async def sync_permissions(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True, thinking=True)

        guild = interaction.guild
        try:
            plan = build_permission_plan(guild)
        except Exception as e:
            await interaction.followup.send(f"Failed to build permission plan: `{e}`", ephemeral=True)
            return

        total = sum(len(v) for v in plan.entries.values())
        if total == 0:
            await interaction.followup.send("No overwrites to apply.", ephemeral=True)
            return

        # --- Confirmation step ---
        confirm_view = SyncConfirmView()
        confirm_msg = await interaction.followup.send(
            f"This will apply **{total}** permission overwrite(s) across "
            f"**{len(plan.entries)}** channel(s)/category(s).\n"
            f"*Expires in 60 seconds.*",
            view=confirm_view,
            ephemeral=True,
        )
        await confirm_view.wait()

        if confirm_view.choice is None:
            # Timed out
            await confirm_msg.edit(content="Timed out — no changes were made.", view=None)
            return

        btn_interaction = confirm_view.button_interaction

        if confirm_view.choice == "cancel":
            await btn_interaction.response.edit_message(content="Cancelled — no changes were made.", view=None)
            return

        if confirm_view.choice == "preview":
            # Show diff, then offer Apply / Cancel
            await btn_interaction.response.edit_message(content="Fetching preview…", view=None)

            lines = diff_permission_plan(plan, guild)
            if not lines:
                await interaction.followup.send("No permission changes detected.", ephemeral=True)
                return

            await interaction.followup.send(
                f"**Permission preview — {len(lines)} overwrite(s)**", ephemeral=True
            )
            for chunk in _chunk_lines(lines):
                await interaction.followup.send(chunk, ephemeral=True)

            apply_view = SyncApplyView()
            apply_msg = await interaction.followup.send(
                f"Apply these **{len(lines)}** overwrite(s) now?\n*Expires in 60 seconds.*",
                view=apply_view,
                ephemeral=True,
            )
            await apply_view.wait()

            if apply_view.confirmed is None:
                await apply_msg.edit(content="Timed out — no changes were made.", view=None)
                return

            apply_btn = apply_view.button_interaction
            if not apply_view.confirmed:
                await apply_btn.response.edit_message(content="Cancelled — no changes were made.", view=None)
                return

            # User confirmed after preview
            await apply_btn.response.edit_message(
                content=f"Applying **{total}** overwrite(s)…", view=None
            )
            applied, removed, errors = await apply_permission_plan(plan, guild)

        else:
            # "sync" — apply immediately
            await btn_interaction.response.edit_message(
                content=f"Applying **{total}** overwrite(s)…", view=None
            )
            applied, removed, errors = await apply_permission_plan(plan, guild)

        result = f"Done — **{applied}** applied"
        if removed:
            result += f", **{removed}** stale overwrite(s) removed"
        result += "."
        if errors:
            result += f"  ⚠️ {errors} error(s) — check bot logs."

        await interaction.followup.send(result, ephemeral=True)


async def setup(bot: commands.Bot):
    await bot.add_cog(PermissionsCog(bot))
