"""
roles.py — /assign and /remove commands.

Bundles (named collections of roles applied together) are managed via
/bundle commands in cogs/admin.py and stored in data/{guild_id}/bundles.json.

Roles are stored by Discord ID (with legacy name fallback for older data).
"""

import discord
from discord import app_commands
from discord.ext import commands

from services import local_store


# ---------------------------------------------------------------------------
# ID-first role resolution helper
# ---------------------------------------------------------------------------

def _lookup_role(
    role_str: str,
    by_id: dict[int, discord.Role],
    by_name: dict[str, discord.Role],
) -> discord.Role | None:
    """
    Resolve a stored role string to a discord.Role.
    Tries integer Discord ID first; falls back to name for legacy data.
    """
    try:
        return by_id.get(int(role_str))
    except ValueError:
        return by_name.get(role_str)


# ---------------------------------------------------------------------------
# Role hierarchy helpers
# ---------------------------------------------------------------------------

def _blocked_roles(executor: discord.Member, roles: list[discord.Role]) -> list[str]:
    """Names of roles the executor cannot manage (at or above their top role). Empty = all ok."""
    if executor.id == executor.guild.owner_id:
        return []
    return [r.name for r in roles if r >= executor.top_role]


def _can_manage_member(executor: discord.Member, target: discord.Member) -> bool:
    """True if executor outranks target in the role hierarchy (or is the guild owner)."""
    if executor.id == executor.guild.owner_id:
        return True
    if target.id == executor.guild.owner_id:
        return False
    return executor.top_role > target.top_role


# ---------------------------------------------------------------------------
# Bundle helpers
# ---------------------------------------------------------------------------

async def _apply_bundle(
    member: discord.Member,
    bundle_roles: list[discord.Role],
    guild: discord.Guild,
) -> tuple[list[discord.Role], list[discord.Role]]:
    """
    Add all roles in bundle_roles to the member, automatically removing
    any conflicting roles from the same exclusive group.

    Returns (added, removed).
    """
    to_remove: list[discord.Role] = []

    groups = local_store.get_exclusive_groups(guild.id)
    discord_roles_by_id: dict[int, discord.Role] = {r.id: r for r in guild.roles}
    discord_roles_by_name: dict[str, discord.Role] = {r.name: r for r in guild.roles}

    # Build role → group mapping using ID-first resolution
    role_to_group: dict[discord.Role, str] = {}
    for group_name, role_strs in groups.items():
        for rs in role_strs:
            r = _lookup_role(rs, discord_roles_by_id, discord_roles_by_name)
            if r:
                role_to_group[r] = group_name

    # Find which exclusive groups the incoming roles belong to
    incoming_groups: set[str] = set()
    for role in bundle_roles:
        g = role_to_group.get(role)
        if g:
            incoming_groups.add(g)

    # Collect any roles the member already holds that conflict
    if incoming_groups:
        member_roles_set = set(member.roles)
        for group in incoming_groups:
            for rs in groups[group]:
                discord_role = _lookup_role(rs, discord_roles_by_id, discord_roles_by_name)
                if discord_role and discord_role in member_roles_set and discord_role not in bundle_roles:
                    to_remove.append(discord_role)

    if to_remove:
        await member.remove_roles(*to_remove, reason="Exclusive group conflict — bundle assignment")
    await member.add_roles(*bundle_roles, reason="Bundle assignment")

    return bundle_roles, to_remove


# ---------------------------------------------------------------------------
# Cog
# ---------------------------------------------------------------------------

class RolesCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    # ------------------------------------------------------------------
    # /assign
    # ------------------------------------------------------------------
    @app_commands.command(
        name="assign",
        description="Assign a role bundle to one or more members.",
    )
    @app_commands.describe(
        member="Member to assign roles to",
        bundle="The name of the bundle to apply",
        member2="Additional member",
        member3="Additional member",
        member4="Additional member",
        member5="Additional member",
    )
    @app_commands.default_permissions(manage_roles=True)
    async def assign(
        self,
        interaction: discord.Interaction,
        member: discord.Member,
        bundle: str,
        member2: discord.Member | None = None,
        member3: discord.Member | None = None,
        member4: discord.Member | None = None,
        member5: discord.Member | None = None,
    ):
        await interaction.response.defer(ephemeral=True)

        bundles = local_store.get_bundles(interaction.guild_id)
        if bundle not in bundles:
            names = ", ".join(sorted(bundles.keys())) or "(none defined yet)"
            await interaction.followup.send(
                f"Bundle **{bundle}** not found.\nAvailable bundles: {names}",
                ephemeral=True,
            )
            return

        guild = interaction.guild
        by_id: dict[int, discord.Role] = {r.id: r for r in guild.roles}
        by_name: dict[str, discord.Role] = {r.name: r for r in guild.roles}

        bundle_roles = [
            r for rs in bundles[bundle]
            if (r := _lookup_role(rs, by_id, by_name)) is not None
        ]
        missing = [
            rs for rs in bundles[bundle]
            if _lookup_role(rs, by_id, by_name) is None
        ]

        if not bundle_roles:
            await interaction.followup.send(
                f"No matching Discord roles found for bundle **{bundle}**.",
                ephemeral=True,
            )
            return

        blocked = _blocked_roles(interaction.user, bundle_roles)
        if blocked:
            await interaction.followup.send(
                f"Cannot assign bundle **{bundle}** — it contains role(s) at or above your "
                "highest role: " + ", ".join(f"**{n}**" for n in blocked),
                ephemeral=True,
            )
            return

        members = [m for m in [member, member2, member3, member4, member5] if m is not None]
        lines = []
        for m in members:
            if not _can_manage_member(interaction.user, m):
                lines.append(
                    f"**{m.display_name}**: ⚠️ Their role is equal to or above yours."
                )
                continue
            try:
                added, removed = await _apply_bundle(m, bundle_roles, guild)
                line = f"**{m.display_name}**: added {', '.join(r.name for r in added)}"
                if removed:
                    line += f"; removed (exclusive group) {', '.join(r.name for r in removed)}"
                lines.append(line)
            except discord.Forbidden:
                lines.append(
                    f"**{m.display_name}**: ⚠️ Missing permissions — make sure the bot's role "
                    "is above all roles it needs to manage."
                )

        if missing:
            lines.append("⚠️ Not found in Discord: " + ", ".join(missing))

        await interaction.followup.send("\n".join(lines), ephemeral=True)

    @assign.autocomplete("bundle")
    async def assign_bundle_autocomplete(
        self, interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[str]]:
        bundles = local_store.get_bundles(interaction.guild_id)
        return [
            app_commands.Choice(name=name, value=name)
            for name in sorted(bundles.keys())
            if current.lower() in name.lower()
        ][:25]

    # ------------------------------------------------------------------
    # /remove
    # ------------------------------------------------------------------
    @app_commands.command(
        name="remove",
        description="Remove a role bundle from one or more members.",
    )
    @app_commands.describe(
        member="Member to remove roles from",
        bundle="The name of the bundle to remove",
        member2="Additional member",
        member3="Additional member",
        member4="Additional member",
        member5="Additional member",
    )
    @app_commands.default_permissions(manage_roles=True)
    async def remove_bundle(
        self,
        interaction: discord.Interaction,
        member: discord.Member,
        bundle: str,
        member2: discord.Member | None = None,
        member3: discord.Member | None = None,
        member4: discord.Member | None = None,
        member5: discord.Member | None = None,
    ):
        await interaction.response.defer(ephemeral=True)

        bundles = local_store.get_bundles(interaction.guild_id)
        if bundle not in bundles:
            names = ", ".join(sorted(bundles.keys())) or "(none defined yet)"
            await interaction.followup.send(
                f"Bundle **{bundle}** not found.\nAvailable bundles: {names}",
                ephemeral=True,
            )
            return

        guild = interaction.guild
        by_id: dict[int, discord.Role] = {r.id: r for r in guild.roles}
        by_name: dict[str, discord.Role] = {r.name: r for r in guild.roles}

        all_bundle_roles = [
            r for rs in bundles[bundle]
            if (r := _lookup_role(rs, by_id, by_name)) is not None
        ]
        blocked = _blocked_roles(interaction.user, all_bundle_roles)
        if blocked:
            await interaction.followup.send(
                f"Cannot remove bundle **{bundle}** — it contains role(s) at or above your "
                "highest role: " + ", ".join(f"**{n}**" for n in blocked),
                ephemeral=True,
            )
            return

        members = [m for m in [member, member2, member3, member4, member5] if m is not None]
        lines = []
        for m in members:
            if not _can_manage_member(interaction.user, m):
                lines.append(
                    f"**{m.display_name}**: ⚠️ Their role is equal to or above yours."
                )
                continue
            member_roles_set = set(m.roles)
            roles_to_remove = [r for r in all_bundle_roles if r in member_roles_set]
            if not roles_to_remove:
                lines.append(f"**{m.display_name}**: no roles from this bundle to remove")
                continue
            try:
                await m.remove_roles(*roles_to_remove, reason=f"Bundle removal: {bundle}")
                lines.append(
                    f"**{m.display_name}**: removed {', '.join(r.name for r in roles_to_remove)}"
                )
            except discord.Forbidden:
                lines.append(
                    f"**{m.display_name}**: ⚠️ Missing permissions — make sure the bot's role "
                    "is above all roles it needs to manage."
                )

        await interaction.followup.send("\n".join(lines), ephemeral=True)

    @remove_bundle.autocomplete("bundle")
    async def remove_bundle_autocomplete(
        self, interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[str]]:
        bundles = local_store.get_bundles(interaction.guild_id)
        return [
            app_commands.Choice(name=name, value=name)
            for name in sorted(bundles.keys())
            if current.lower() in name.lower()
        ][:25]


async def setup(bot: commands.Bot):
    await bot.add_cog(RolesCog(bot))
