"""
admin.py — interactive management of permission levels, role bundles,
           exclusive groups, category baselines, and access rules.

Permission level commands  (/level ...)
  /level list              — list all levels
  /level view <name>       — show all permissions for a level
  /level edit <name>       — interactive 3-step UI: group → permission → value
  /level set <name> <perm> <value>  — set one permission directly (with autocomplete)
  /level create <name>     — create a new level (optionally clone from existing)
  /level delete <name>     — delete a level
  /level reset-defaults    — restore all levels to config.py defaults

Bundle commands  (/bundle ...)
  /bundle list             — list all bundles and their roles
  /bundle view <name>      — show roles in a bundle
  /bundle create <name>    — create an empty bundle
  /bundle delete <name>    — delete a bundle
  /bundle add-role <bundle> <role>     — add a Discord role to a bundle
  /bundle remove-role <bundle> <role>  — remove a role from a bundle

Exclusive group commands  (/exclusive-group ...)
  /exclusive-group list                       — list all groups and their roles
  /exclusive-group create <name>              — create an empty group
  /exclusive-group delete <name>              — delete a group
  /exclusive-group add-role <group> <role>    — add a role to a group
  /exclusive-group remove-role <group> <role> — remove a role from a group

Category baseline commands  (/category ...)
  /category baseline-list                      — list @everyone baselines per category
  /category baseline-set <category> <level>    — set @everyone baseline for a category
  /category baseline-clear <category>          — remove baseline from a category

Access rule commands  (/access-rule ...)
  /access-rule list                                     — list all rules
  /access-rule add-category <role> <category> <level>   — rule targeting a category
  /access-rule add-channel  <role> <channel>  <level>   — rule targeting a channel
  /access-rule remove <id>                              — delete a rule by its ID
  /access-rule edit <id> [level] [overwrite]            — change level or allow/deny
  /access-rule prune                                    — remove stale rules/baselines

Status  (/status)
  /status — show counts of all configured items
"""

import discord
from discord import app_commands
from discord.ext import commands

from config import ALL_PERMISSIONS, PERMISSION_GROUPS
from services import local_store


# ---------------------------------------------------------------------------
# Embed helpers
# ---------------------------------------------------------------------------

_VAL_EMOJI = {True: "✅", False: "❌", None: "⬜"}
_VAL_LABEL = {True: "Allow", False: "Deny", None: "Neutral"}


def _build_level_embed(
    level_name: str,
    guild_id: int,
    active_group: str | None = None,
) -> discord.Embed:
    """Build a rich embed showing all permissions for a level, grouped."""
    levels = local_store.get_permission_levels(guild_id)
    perms: dict[str, bool] = levels.get(level_name, {})

    embed = discord.Embed(
        title=f"Permission Level — {level_name}",
        color=discord.Color.blurple(),
    )

    for group_name, group_perms in PERMISSION_GROUPS.items():
        lines = []
        for attr in group_perms:
            val = perms.get(attr)
            display_name = attr.replace("_", " ").title()
            lines.append(f"{_VAL_EMOJI[val]} {display_name}")

        # Split into two columns so the embed isn't too tall
        mid = (len(lines) + 1) // 2
        marker = "▶ " if group_name == active_group else ""
        embed.add_field(name=f"{marker}{group_name}", value="\n".join(lines[:mid]), inline=True)
        embed.add_field(name="\u200b",                value="\n".join(lines[mid:]) or "\u200b", inline=True)
        embed.add_field(name="\u200b",                value="\u200b", inline=False)   # row break

    embed.set_footer(text="✅ Allow  ❌ Deny  ⬜ Neutral (inherit)")
    return embed


def _display_role(guild: discord.Guild, role_str: str) -> str:
    """Resolve a stored role ID (or legacy name) to a display name."""
    try:
        r = guild.get_role(int(role_str))
        return r.name if r else f"(deleted {role_str})"
    except ValueError:
        return role_str  # legacy name stored before ID migration


def _build_bundle_embed(bundle_name: str, guild_id: int, guild: discord.Guild | None = None) -> discord.Embed:
    bundles = local_store.get_bundles(guild_id)
    role_strs = bundles.get(bundle_name, [])
    if guild:
        roles_display = [_display_role(guild, rs) for rs in role_strs]
    else:
        roles_display = role_strs
    embed = discord.Embed(
        title=f"Bundle — {bundle_name}",
        color=discord.Color.green(),
        description="\n".join(f"• {r}" for r in roles_display) if roles_display else "*No roles yet*",
    )
    return embed


# ---------------------------------------------------------------------------
# Interactive UI — permission level editor
# ---------------------------------------------------------------------------

class LevelGroupSelect(discord.ui.Select):
    """Step 1: pick General / Text / Voice."""

    def __init__(self, level_name: str, guild_id: int):
        self.level_name = level_name
        self.guild_id = guild_id
        options = [
            discord.SelectOption(label="General", description="Server-wide permissions"),
            discord.SelectOption(label="Text",    description="Text channel permissions"),
            discord.SelectOption(label="Voice",   description="Voice channel permissions"),
        ]
        super().__init__(placeholder="Select a permission group…", options=options, row=0)

    async def callback(self, interaction: discord.Interaction):
        group = self.values[0]
        view = LevelPermissionEditView(self.level_name, group, self.guild_id)
        embed = _build_level_embed(self.level_name, self.guild_id, active_group=group)
        await interaction.response.edit_message(embed=embed, view=view)


class LevelGroupView(discord.ui.View):
    def __init__(self, level_name: str, guild_id: int):
        super().__init__(timeout=180)
        self.add_item(LevelGroupSelect(level_name, guild_id))


class LevelPermissionSelect(discord.ui.Select):
    """Step 2: pick which permission within the chosen group."""

    def __init__(self, level_name: str, group: str, guild_id: int):
        self.level_name = level_name
        self.group = group
        self.guild_id = guild_id

        levels = local_store.get_permission_levels(guild_id)
        current = levels.get(level_name, {})
        options = []
        for attr in PERMISSION_GROUPS[group]:
            val = current.get(attr)
            options.append(discord.SelectOption(
                label=attr.replace("_", " ").title(),
                value=attr,
                description=f"Current: {_VAL_LABEL[val]}",
                emoji=_VAL_EMOJI[val],
            ))
        super().__init__(
            placeholder="Select a permission to change…",
            options=options[:25],
            row=0,
        )

    async def callback(self, interaction: discord.Interaction):
        attr = self.values[0]
        view = LevelValueView(self.level_name, self.group, attr, self.guild_id)
        levels = local_store.get_permission_levels(self.guild_id)
        current_val = levels.get(self.level_name, {}).get(attr)
        embed = discord.Embed(
            title=f"{self.level_name} → {attr.replace('_', ' ').title()}",
            description=f"Current value: **{_VAL_LABEL[current_val]}** {_VAL_EMOJI[current_val]}\n\nSelect new value:",
            color=discord.Color.blurple(),
        )
        await interaction.response.edit_message(embed=embed, view=view)


class LevelBackButton(discord.ui.Button):
    """Returns to the group selector."""

    def __init__(self, level_name: str, guild_id: int):
        self.level_name = level_name
        self.guild_id = guild_id
        super().__init__(label="← Back", style=discord.ButtonStyle.secondary, row=1)

    async def callback(self, interaction: discord.Interaction):
        view = LevelGroupView(self.level_name, self.guild_id)
        embed = _build_level_embed(self.level_name, self.guild_id)
        await interaction.response.edit_message(embed=embed, view=view)


class LevelPermissionEditView(discord.ui.View):
    def __init__(self, level_name: str, group: str, guild_id: int):
        super().__init__(timeout=180)
        self.add_item(LevelPermissionSelect(level_name, group, guild_id))
        self.add_item(LevelBackButton(level_name, guild_id))


class LevelValueButton(discord.ui.Button):
    """Step 3: set Allow / Deny / Neutral for a specific permission."""

    def __init__(self, level_name: str, group: str, attr: str, value: bool | None, guild_id: int):
        self.level_name = level_name
        self.group = group
        self.attr = attr
        self.value = value
        self.guild_id = guild_id

        label_map = {True: "Allow", False: "Deny", None: "Neutral"}
        style_map = {
            True:  discord.ButtonStyle.success,
            False: discord.ButtonStyle.danger,
            None:  discord.ButtonStyle.secondary,
        }
        super().__init__(
            label=label_map[value],
            style=style_map[value],
            emoji=_VAL_EMOJI[value],
            row=0,
        )

    async def callback(self, interaction: discord.Interaction):
        local_store.set_permission(self.guild_id, self.level_name, self.attr, self.value)
        # Return to the group view with updated embed
        view = LevelPermissionEditView(self.level_name, self.group, self.guild_id)
        embed = _build_level_embed(self.level_name, self.guild_id, active_group=self.group)
        await interaction.response.edit_message(embed=embed, view=view)


class LevelValueBackButton(discord.ui.Button):
    def __init__(self, level_name: str, group: str, guild_id: int):
        self.level_name = level_name
        self.group = group
        self.guild_id = guild_id
        super().__init__(label="← Back", style=discord.ButtonStyle.secondary, row=1)

    async def callback(self, interaction: discord.Interaction):
        view = LevelPermissionEditView(self.level_name, self.group, self.guild_id)
        embed = _build_level_embed(self.level_name, self.guild_id, active_group=self.group)
        await interaction.response.edit_message(embed=embed, view=view)


class LevelValueView(discord.ui.View):
    def __init__(self, level_name: str, group: str, attr: str, guild_id: int):
        super().__init__(timeout=60)
        self.add_item(LevelValueButton(level_name, group, attr, True,  guild_id))
        self.add_item(LevelValueButton(level_name, group, attr, False, guild_id))
        self.add_item(LevelValueButton(level_name, group, attr, None,  guild_id))
        self.add_item(LevelValueBackButton(level_name, group, guild_id))


# ---------------------------------------------------------------------------
# Confirmation UI — used by destructive delete commands
# ---------------------------------------------------------------------------

class ConfirmView(discord.ui.View):
    """Two-button (Confirm / Cancel) view for destructive operations."""

    def __init__(self):
        super().__init__(timeout=30.0)
        self.confirmed: bool | None = None
        self.button_interaction: discord.Interaction | None = None

    @discord.ui.button(label="Confirm", style=discord.ButtonStyle.danger, emoji="🗑️")
    async def confirm_btn(self, interaction: discord.Interaction, _button: discord.ui.Button):
        self.confirmed = True
        self.button_interaction = interaction
        self.stop()

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.secondary)
    async def cancel_btn(self, interaction: discord.Interaction, _button: discord.ui.Button):
        self.confirmed = False
        self.button_interaction = interaction
        self.stop()


# ---------------------------------------------------------------------------
# Cog
# ---------------------------------------------------------------------------

class AdminCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    # ==================================================================
    # /level group
    # ==================================================================

    level = app_commands.Group(
        name="level",
        description="Manage permission levels",
        default_permissions=discord.Permissions(administrator=True),
    )

    @level.command(name="list", description="List all permission levels")
    async def level_list(self, interaction: discord.Interaction):
        levels = local_store.get_permission_levels(interaction.guild_id)
        embed = discord.Embed(
            title="Permission Levels",
            description="\n".join(f"• **{name}**" for name in levels) or "*None defined*",
            color=discord.Color.blurple(),
        )
        embed.set_footer(text="Use /level view <name> to see the full permission breakdown")
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @level.command(name="view", description="Show all permissions for a level")
    @app_commands.describe(name="The permission level to view")
    async def level_view(self, interaction: discord.Interaction, name: str):
        if name not in local_store.get_permission_levels(interaction.guild_id):
            await interaction.response.send_message(
                f"Level **{name}** not found.", ephemeral=True
            )
            return
        await interaction.response.send_message(
            embed=_build_level_embed(name, interaction.guild_id), ephemeral=True
        )

    @level.command(
        name="edit",
        description="Interactively edit permissions for a level (group → permission → value)",
    )
    @app_commands.describe(name="The permission level to edit")
    async def level_edit(self, interaction: discord.Interaction, name: str):
        if name not in local_store.get_permission_levels(interaction.guild_id):
            await interaction.response.send_message(
                f"Level **{name}** not found.", ephemeral=True
            )
            return
        view = LevelGroupView(name, interaction.guild_id)
        embed = _build_level_embed(name, interaction.guild_id)
        await interaction.response.send_message(embed=embed, view=view, ephemeral=True)

    @level.command(name="set", description="Set one permission on a level directly")
    @app_commands.describe(
        name="Permission level name",
        permission="Discord permission attribute (e.g. send_messages)",
        value="Allow, Deny, or Neutral",
    )
    @app_commands.choices(value=[
        app_commands.Choice(name="Allow",   value="allow"),
        app_commands.Choice(name="Deny",    value="deny"),
        app_commands.Choice(name="Neutral", value="neutral"),
    ])
    async def level_set(
        self,
        interaction: discord.Interaction,
        name: str,
        permission: str,
        value: str,
    ):
        if permission not in ALL_PERMISSIONS:
            await interaction.response.send_message(
                f"`{permission}` is not a valid permission attribute.", ephemeral=True
            )
            return
        val_map = {"allow": True, "deny": False, "neutral": None}
        try:
            local_store.set_permission(interaction.guild_id, name, permission, val_map[value])
        except KeyError:
            await interaction.response.send_message(
                f"Level **{name}** not found.", ephemeral=True
            )
            return
        await interaction.response.send_message(
            f"Set **{name}** / `{permission}` → **{value.title()}**", ephemeral=True
        )

    @level.command(name="create", description="Create a new permission level")
    @app_commands.describe(
        name="Name for the new level",
        copy_from="Optional: clone settings from this existing level",
    )
    async def level_create(
        self,
        interaction: discord.Interaction,
        name: str,
        copy_from: str | None = None,
    ):
        try:
            local_store.create_level(interaction.guild_id, name, copy_from)
        except (ValueError, KeyError) as e:
            await interaction.response.send_message(str(e), ephemeral=True)
            return
        msg = f"Created level **{name}**"
        if copy_from:
            msg += f" (cloned from **{copy_from}**)"
        await interaction.response.send_message(msg + ".", ephemeral=True)

    @level.command(name="delete", description="Delete a permission level")
    @app_commands.describe(name="The level to delete")
    async def level_delete(self, interaction: discord.Interaction, name: str):
        if name not in local_store.get_permission_levels(interaction.guild_id):
            await interaction.response.send_message(
                f"Level **{name}** not found.", ephemeral=True
            )
            return
        view = ConfirmView()
        await interaction.response.send_message(
            f"Delete permission level **{name}**? This cannot be undone.",
            view=view, ephemeral=True,
        )
        await view.wait()
        if view.confirmed is None:
            await interaction.edit_original_response(content="Timed out.", view=None)
            return
        if not view.confirmed:
            await view.button_interaction.response.edit_message(content="Cancelled.", view=None)
            return
        try:
            local_store.delete_level(interaction.guild_id, name)
        except KeyError:
            await view.button_interaction.response.edit_message(
                content=f"Level **{name}** not found.", view=None
            )
            return
        await view.button_interaction.response.edit_message(
            content=f"Deleted level **{name}**.", view=None
        )

    @level.command(
        name="reset-defaults",
        description="Restore all permission levels to the built-in defaults",
    )
    async def level_reset_defaults(self, interaction: discord.Interaction):
        local_store.reset_levels_to_default(interaction.guild_id)
        await interaction.response.send_message(
            "Permission levels reset to defaults.", ephemeral=True
        )

    # Autocomplete for level name
    async def _level_name_autocomplete(
        self, interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[str]]:
        levels = local_store.get_permission_levels(interaction.guild_id)
        return [
            app_commands.Choice(name=n, value=n)
            for n in sorted(levels.keys())
            if current.lower() in n.lower()
        ][:25]

    @level_view.autocomplete("name")
    @level_edit.autocomplete("name")
    @level_delete.autocomplete("name")
    async def level_name_ac(self, interaction, current):
        return await self._level_name_autocomplete(interaction, current)

    @level_set.autocomplete("name")
    async def level_set_name_ac(self, interaction, current):
        return await self._level_name_autocomplete(interaction, current)

    @level_set.autocomplete("permission")
    async def level_set_perm_ac(
        self, interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[str]]:
        return [
            app_commands.Choice(name=p.replace("_", " ").title(), value=p)
            for p in ALL_PERMISSIONS
            if current.lower() in p.lower()
        ][:25]

    @level_create.autocomplete("copy_from")
    async def level_create_copy_ac(self, interaction, current):
        return await self._level_name_autocomplete(interaction, current)

    # ==================================================================
    # /bundle group
    # ==================================================================

    bundle = app_commands.Group(
        name="bundle",
        description="Manage role bundles",
        default_permissions=discord.Permissions(manage_roles=True),
    )

    @bundle.command(name="list", description="List all bundles and their roles")
    async def bundle_list(self, interaction: discord.Interaction):
        bundles = local_store.get_bundles(interaction.guild_id)
        if not bundles:
            await interaction.response.send_message("No bundles defined yet.", ephemeral=True)
            return
        embed = discord.Embed(title="Role Bundles", color=discord.Color.green())
        for name, role_strs in bundles.items():
            display = [_display_role(interaction.guild, rs) for rs in role_strs]
            embed.add_field(
                name=name,
                value=", ".join(display) if display else "*empty*",
                inline=False,
            )
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @bundle.command(name="view", description="Show the roles in a bundle")
    @app_commands.describe(name="The bundle to view")
    async def bundle_view(self, interaction: discord.Interaction, name: str):
        bundles = local_store.get_bundles(interaction.guild_id)
        if name not in bundles:
            await interaction.response.send_message(
                f"Bundle **{name}** not found.", ephemeral=True
            )
            return
        await interaction.response.send_message(
            embed=_build_bundle_embed(name, interaction.guild_id, interaction.guild), ephemeral=True
        )

    @bundle_view.autocomplete("name")
    async def bundle_view_ac(self, interaction, current):
        return await self._bundle_name_autocomplete(interaction, current)

    @bundle.command(name="create", description="Create a new empty bundle")
    @app_commands.describe(name="Name for the new bundle")
    async def bundle_create(self, interaction: discord.Interaction, name: str):
        try:
            local_store.create_bundle(interaction.guild_id, name)
        except ValueError as e:
            await interaction.response.send_message(str(e), ephemeral=True)
            return
        await interaction.response.send_message(
            f"Created bundle **{name}**. Use `/bundle add-role` to add roles.",
            ephemeral=True,
        )

    @bundle.command(name="delete", description="Delete a bundle")
    @app_commands.describe(name="The bundle to delete")
    async def bundle_delete(self, interaction: discord.Interaction, name: str):
        if name not in local_store.get_bundles(interaction.guild_id):
            await interaction.response.send_message(
                f"Bundle **{name}** not found.", ephemeral=True
            )
            return
        view = ConfirmView()
        await interaction.response.send_message(
            f"Delete bundle **{name}**? This cannot be undone.",
            view=view, ephemeral=True,
        )
        await view.wait()
        if view.confirmed is None:
            await interaction.edit_original_response(content="Timed out.", view=None)
            return
        if not view.confirmed:
            await view.button_interaction.response.edit_message(content="Cancelled.", view=None)
            return
        try:
            local_store.delete_bundle(interaction.guild_id, name)
        except KeyError:
            await view.button_interaction.response.edit_message(
                content=f"Bundle **{name}** not found.", view=None
            )
            return
        await view.button_interaction.response.edit_message(
            content=f"Deleted bundle **{name}**.", view=None
        )

    @bundle.command(name="add-role", description="Add one or more Discord roles to a bundle")
    @app_commands.describe(
        name="The bundle to add to",
        role1="Role to add",
        role2="Additional role",
        role3="Additional role",
        role4="Additional role",
        role5="Additional role",
    )
    async def bundle_add_role(
        self,
        interaction: discord.Interaction,
        name: str,
        role1: discord.Role,
        role2: discord.Role | None = None,
        role3: discord.Role | None = None,
        role4: discord.Role | None = None,
        role5: discord.Role | None = None,
    ):
        roles = [r for r in [role1, role2, role3, role4, role5] if r is not None]
        try:
            for role in roles:
                local_store.add_role_to_bundle(interaction.guild_id, name, str(role.id))
        except KeyError:
            await interaction.response.send_message(
                f"Bundle **{name}** not found.", ephemeral=True
            )
            return
        await interaction.response.send_message(
            embed=_build_bundle_embed(name, interaction.guild_id, interaction.guild), ephemeral=True
        )

    @bundle.command(name="remove-role", description="Remove a role from a bundle")
    @app_commands.describe(
        name="The bundle",
        role="The role to remove",
    )
    async def bundle_remove_role(
        self,
        interaction: discord.Interaction,
        name: str,
        role: discord.Role,
    ):
        bundles = local_store.get_bundles(interaction.guild_id)
        if name not in bundles:
            await interaction.response.send_message(
                f"Bundle **{name}** not found.", ephemeral=True
            )
            return
        # Find the stored entry matching this role (by ID or legacy name)
        stored = bundles[name]
        to_remove = next(
            (e for e in stored if e == str(role.id) or e == role.name),
            None,
        )
        if to_remove is None:
            await interaction.response.send_message(
                f"**{role.name}** is not in bundle **{name}**.", ephemeral=True
            )
            return
        local_store.remove_role_from_bundle(interaction.guild_id, name, to_remove)
        await interaction.response.send_message(
            embed=_build_bundle_embed(name, interaction.guild_id, interaction.guild), ephemeral=True
        )

    # Bundle name autocomplete
    async def _bundle_name_autocomplete(
        self, interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[str]]:
        bundles = local_store.get_bundles(interaction.guild_id)
        return [
            app_commands.Choice(name=n, value=n)
            for n in sorted(bundles.keys())
            if current.lower() in n.lower()
        ][:25]

    @bundle_delete.autocomplete("name")
    @bundle_add_role.autocomplete("name")
    @bundle_remove_role.autocomplete("name")
    async def bundle_name_ac(self, interaction, current):
        return await self._bundle_name_autocomplete(interaction, current)

    # ==================================================================
    # /exclusive-group group
    # ==================================================================

    exclusive_group = app_commands.Group(
        name="exclusive-group",
        description="Manage exclusive role groups (only one role per group can be held at a time)",
        default_permissions=discord.Permissions(administrator=True),
    )

    @exclusive_group.command(name="list", description="List all exclusive groups and their roles")
    async def eg_list(self, interaction: discord.Interaction):
        groups = local_store.get_exclusive_groups(interaction.guild_id)
        if not groups:
            await interaction.response.send_message(
                "No exclusive groups defined yet. Use `/exclusive-group create` to add one.",
                ephemeral=True,
            )
            return
        embed = discord.Embed(title="Exclusive Groups", color=discord.Color.orange())
        for name, role_strs in groups.items():
            display = [_display_role(interaction.guild, rs) for rs in role_strs]
            embed.add_field(
                name=name,
                value=", ".join(display) if display else "*no roles yet*",
                inline=False,
            )
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @exclusive_group.command(name="create", description="Create a new exclusive group")
    @app_commands.describe(name="Name for the new group (e.g. Membership Status)")
    async def eg_create(self, interaction: discord.Interaction, name: str):
        try:
            local_store.create_exclusive_group(interaction.guild_id, name)
        except ValueError as e:
            await interaction.response.send_message(str(e), ephemeral=True)
            return
        await interaction.response.send_message(
            f"Created exclusive group **{name}**. Use `/exclusive-group add-role` to add roles.",
            ephemeral=True,
        )

    @exclusive_group.command(name="delete", description="Delete an exclusive group")
    @app_commands.describe(name="The group to delete")
    async def eg_delete(self, interaction: discord.Interaction, name: str):
        if name not in local_store.get_exclusive_groups(interaction.guild_id):
            await interaction.response.send_message(
                f"Exclusive group **{name}** not found.", ephemeral=True
            )
            return
        view = ConfirmView()
        await interaction.response.send_message(
            f"Delete exclusive group **{name}**? This cannot be undone.",
            view=view, ephemeral=True,
        )
        await view.wait()
        if view.confirmed is None:
            await interaction.edit_original_response(content="Timed out.", view=None)
            return
        if not view.confirmed:
            await view.button_interaction.response.edit_message(content="Cancelled.", view=None)
            return
        try:
            local_store.delete_exclusive_group(interaction.guild_id, name)
        except KeyError:
            await view.button_interaction.response.edit_message(
                content=f"Exclusive group **{name}** not found.", view=None
            )
            return
        await view.button_interaction.response.edit_message(
            content=f"Deleted exclusive group **{name}**.", view=None
        )

    @exclusive_group.command(name="add-role", description="Add a Discord role to an exclusive group")
    @app_commands.describe(name="The exclusive group", role="Role to add")
    async def eg_add_role(self, interaction: discord.Interaction, name: str, role: discord.Role):
        try:
            local_store.add_role_to_exclusive_group(interaction.guild_id, name, str(role.id))
        except KeyError:
            await interaction.response.send_message(
                f"Exclusive group **{name}** not found.", ephemeral=True
            )
            return
        groups = local_store.get_exclusive_groups(interaction.guild_id)
        display = [_display_role(interaction.guild, rs) for rs in groups.get(name, [])]
        await interaction.response.send_message(
            f"**{name}**: {', '.join(display)}", ephemeral=True
        )

    @exclusive_group.command(name="remove-role", description="Remove a role from an exclusive group")
    @app_commands.describe(name="The exclusive group", role="The role to remove")
    async def eg_remove_role(self, interaction: discord.Interaction, name: str, role: discord.Role):
        groups = local_store.get_exclusive_groups(interaction.guild_id)
        if name not in groups:
            await interaction.response.send_message(
                f"Exclusive group **{name}** not found.", ephemeral=True
            )
            return
        # Find stored entry matching this role (by ID or legacy name)
        stored = groups[name]
        to_remove = next(
            (e for e in stored if e == str(role.id) or e == role.name),
            None,
        )
        if to_remove is None:
            await interaction.response.send_message(
                f"**{role.name}** is not in group **{name}**.", ephemeral=True
            )
            return
        local_store.remove_role_from_exclusive_group(interaction.guild_id, name, to_remove)
        groups = local_store.get_exclusive_groups(interaction.guild_id)
        display = [_display_role(interaction.guild, rs) for rs in groups.get(name, [])]
        await interaction.response.send_message(
            f"**{name}**: {', '.join(display) if display else '*empty*'}", ephemeral=True
        )

    # Autocomplete helpers for exclusive-group commands
    async def _eg_name_autocomplete(
        self, interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[str]]:
        groups = local_store.get_exclusive_groups(interaction.guild_id)
        return [
            app_commands.Choice(name=n, value=n)
            for n in sorted(groups.keys())
            if current.lower() in n.lower()
        ][:25]

    @eg_delete.autocomplete("name")
    @eg_add_role.autocomplete("name")
    @eg_remove_role.autocomplete("name")
    async def eg_name_ac(self, interaction, current):
        return await self._eg_name_autocomplete(interaction, current)


    # ==================================================================
    # /category group
    # ==================================================================

    category = app_commands.Group(
        name="category",
        description="Manage per-category @everyone baseline permissions",
        default_permissions=discord.Permissions(administrator=True),
    )

    @category.command(name="baseline-list", description="List all category baseline permissions")
    async def cat_baseline_list(self, interaction: discord.Interaction):
        baselines = local_store.get_category_baselines(interaction.guild_id)
        if not baselines:
            await interaction.response.send_message(
                "No category baselines set. Use `/category baseline-set` to configure one.",
                ephemeral=True,
            )
            return
        lines = []
        for cat_id_str, level in baselines.items():
            cat = interaction.guild.get_channel(int(cat_id_str))
            name = cat.name if cat else f"(deleted, ID {cat_id_str})"
            lines.append(f"• **{name}** → {level}")
        embed = discord.Embed(
            title="Category Baselines (@everyone)",
            description="\n".join(lines),
            color=discord.Color.blurple(),
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @category.command(
        name="baseline-set",
        description="Set the @everyone baseline permission for a category",
    )
    @app_commands.describe(
        category="The category to configure",
        level="Permission level to apply to @everyone",
    )
    async def cat_baseline_set(
        self,
        interaction: discord.Interaction,
        category: discord.CategoryChannel,
        level: str,
    ):
        levels = local_store.get_permission_levels(interaction.guild_id)
        if level not in levels:
            names = ", ".join(sorted(levels.keys()))
            await interaction.response.send_message(
                f"Level **{level}** not found. Available: {names}", ephemeral=True
            )
            return
        local_store.set_category_baseline(interaction.guild_id, str(category.id), level)
        await interaction.response.send_message(
            f"Set **{category.name}** baseline → **{level}** for @everyone.",
            ephemeral=True,
        )

    @category.command(
        name="baseline-clear",
        description="Remove the @everyone baseline from a category",
    )
    @app_commands.describe(category="The category to clear")
    async def cat_baseline_clear(
        self,
        interaction: discord.Interaction,
        category: discord.CategoryChannel,
    ):
        local_store.clear_category_baseline(interaction.guild_id, str(category.id))
        await interaction.response.send_message(
            f"Cleared baseline for **{category.name}**.",
            ephemeral=True,
        )

    @cat_baseline_set.autocomplete("level")
    async def cat_level_ac(
        self, interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[str]]:
        return await self._level_name_autocomplete(interaction, current)

    # ==================================================================
    # /access-rule group
    # ==================================================================

    access_rule = app_commands.Group(
        name="access-rule",
        description="Manage role-based channel/category access rules",
        default_permissions=discord.Permissions(administrator=True),
    )

    @access_rule.command(name="list", description="List all access rules")
    async def ar_list(self, interaction: discord.Interaction):
        data = local_store.get_access_rules_data(interaction.guild_id)
        rules = data.get("rules", [])
        if not rules:
            await interaction.response.send_message(
                "No access rules defined. Use `/access-rule add-category` or `/access-rule add-channel`.",
                ephemeral=True,
            )
            return
        lines = []
        for rule in rules:
            role_names = []
            for rid_str in rule["role_ids"]:
                r = interaction.guild.get_role(int(rid_str))
                role_names.append(r.name if r else f"(deleted {rid_str})")
            target_names = []
            for tid_str in rule["target_ids"]:
                t = interaction.guild.get_channel(int(tid_str))
                target_names.append(t.name if t else f"(deleted {tid_str})")
            target_type = rule["target_type"].title()
            overwrite = rule.get("overwrite", "Allow")
            lines.append(
                f"**#{rule['id']}** {', '.join(role_names)} → "
                f"{target_type}({', '.join(target_names)}) "
                f"[{rule['level']} / {overwrite}]"
            )
        embed = discord.Embed(
            title="Access Rules",
            description="\n".join(lines),
            color=discord.Color.blurple(),
        )
        embed.set_footer(text="Use /access-rule remove <id> to delete a rule")
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @access_rule.command(
        name="add-category",
        description="Grant a role a permission level for an entire category",
    )
    @app_commands.describe(
        role="The role to grant access",
        category="The category to apply the permission to",
        level="Permission level to grant",
        overwrite="Allow or Deny (default: Allow)",
    )
    @app_commands.choices(overwrite=[
        app_commands.Choice(name="Allow", value="Allow"),
        app_commands.Choice(name="Deny",  value="Deny"),
    ])
    async def ar_add_category(
        self,
        interaction: discord.Interaction,
        role: discord.Role,
        category: discord.CategoryChannel,
        level: str,
        overwrite: str = "Allow",
    ):
        levels = local_store.get_permission_levels(interaction.guild_id)
        if level not in levels:
            names = ", ".join(sorted(levels.keys()))
            await interaction.response.send_message(
                f"Level **{level}** not found. Available: {names}", ephemeral=True
            )
            return
        rule_id = local_store.add_access_rule(
            interaction.guild_id,
            role_ids=[str(role.id)],
            target_type="category",
            target_ids=[str(category.id)],
            level=level,
            overwrite=overwrite,
        )
        await interaction.response.send_message(
            f"Rule **#{rule_id}** added: **{role.name}** → **{category.name}** [{level} / {overwrite}]",
            ephemeral=True,
        )

    @access_rule.command(
        name="add-channel",
        description="Grant a role a permission level for a specific channel",
    )
    @app_commands.describe(
        role="The role to grant access",
        channel="The channel to apply the permission to",
        level="Permission level to grant",
        overwrite="Allow or Deny (default: Allow)",
    )
    @app_commands.choices(overwrite=[
        app_commands.Choice(name="Allow", value="Allow"),
        app_commands.Choice(name="Deny",  value="Deny"),
    ])
    async def ar_add_channel(
        self,
        interaction: discord.Interaction,
        role: discord.Role,
        channel: discord.abc.GuildChannel,
        level: str,
        overwrite: str = "Allow",
    ):
        if isinstance(channel, discord.CategoryChannel):
            await interaction.response.send_message(
                "That's a category — use `/access-rule add-category` instead.",
                ephemeral=True,
            )
            return
        levels = local_store.get_permission_levels(interaction.guild_id)
        if level not in levels:
            names = ", ".join(sorted(levels.keys()))
            await interaction.response.send_message(
                f"Level **{level}** not found. Available: {names}", ephemeral=True
            )
            return
        rule_id = local_store.add_access_rule(
            interaction.guild_id,
            role_ids=[str(role.id)],
            target_type="channel",
            target_ids=[str(channel.id)],
            level=level,
            overwrite=overwrite,
        )
        await interaction.response.send_message(
            f"Rule **#{rule_id}** added: **{role.name}** → **#{channel.name}** [{level} / {overwrite}]",
            ephemeral=True,
        )

    @access_rule.command(
        name="remove",
        description="Remove an access rule by its ID number",
    )
    @app_commands.describe(rule_id="The rule to remove (select from the list)")
    async def ar_remove(self, interaction: discord.Interaction, rule_id: int):
        data = local_store.get_access_rules_data(interaction.guild_id)
        rule = next((r for r in data.get("rules", []) if r["id"] == rule_id), None)
        if rule is None:
            await interaction.response.send_message(
                f"Access rule **#{rule_id}** not found.", ephemeral=True
            )
            return
        role_names = []
        for rid_str in rule["role_ids"]:
            r = interaction.guild.get_role(int(rid_str))
            role_names.append(r.name if r else f"(deleted {rid_str})")
        target_names = []
        for tid_str in rule["target_ids"]:
            t = interaction.guild.get_channel(int(tid_str))
            target_names.append(t.name if t else f"(deleted {tid_str})")
        summary = (
            f"**#{rule_id}** {', '.join(role_names)} → "
            f"{rule['target_type']}({', '.join(target_names)}) "
            f"[{rule['level']}/{rule.get('overwrite', 'Allow')}]"
        )
        view = ConfirmView()
        await interaction.response.send_message(
            f"Remove access rule {summary}?", view=view, ephemeral=True,
        )
        await view.wait()
        if view.confirmed is None:
            await interaction.edit_original_response(content="Timed out.", view=None)
            return
        if not view.confirmed:
            await view.button_interaction.response.edit_message(content="Cancelled.", view=None)
            return
        try:
            local_store.remove_access_rule(interaction.guild_id, rule_id)
        except KeyError:
            await view.button_interaction.response.edit_message(
                content=f"Access rule **#{rule_id}** not found.", view=None
            )
            return
        await view.button_interaction.response.edit_message(
            content=f"Deleted access rule **#{rule_id}**.", view=None
        )

    @ar_remove.autocomplete("rule_id")
    async def ar_remove_ac(
        self, interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[int]]:
        data = local_store.get_access_rules_data(interaction.guild_id)
        choices = []
        for rule in data.get("rules", []):
            role_names = []
            for rid_str in rule["role_ids"]:
                r = interaction.guild.get_role(int(rid_str))
                role_names.append(r.name if r else f"deleted:{rid_str}")
            target_names = []
            for tid_str in rule["target_ids"]:
                t = interaction.guild.get_channel(int(tid_str))
                target_names.append(t.name if t else f"deleted:{tid_str}")
            label = (
                f"#{rule['id']} {', '.join(role_names)} → "
                f"{', '.join(target_names)} [{rule['level']}]"
            )[:100]  # Discord choice names capped at 100 chars
            if current in str(rule["id"]) or current.lower() in label.lower():
                choices.append(app_commands.Choice(name=label, value=rule["id"]))
        return choices[:25]

    @access_rule.command(
        name="edit",
        description="Change the permission level or allow/deny on an existing access rule",
    )
    @app_commands.describe(
        rule_id="The rule to edit (select from the list)",
        level="New permission level (leave blank to keep current)",
        overwrite="New allow/deny direction (leave blank to keep current)",
    )
    @app_commands.choices(overwrite=[
        app_commands.Choice(name="Allow", value="Allow"),
        app_commands.Choice(name="Deny",  value="Deny"),
    ])
    async def ar_edit(
        self,
        interaction: discord.Interaction,
        rule_id: int,
        level: str | None = None,
        overwrite: str | None = None,
    ):
        data = local_store.get_access_rules_data(interaction.guild_id)
        rule = next((r for r in data.get("rules", []) if r["id"] == rule_id), None)
        if rule is None:
            await interaction.response.send_message(
                f"Access rule **#{rule_id}** not found.", ephemeral=True
            )
            return
        if level is None and overwrite is None:
            await interaction.response.send_message(
                "Nothing to change — provide a new `level` and/or `overwrite`.", ephemeral=True
            )
            return
        if level is not None:
            levels = local_store.get_permission_levels(interaction.guild_id)
            if level not in levels:
                names = ", ".join(sorted(levels.keys()))
                await interaction.response.send_message(
                    f"Level **{level}** not found. Available: {names}", ephemeral=True
                )
                return
            rule["level"] = level
        if overwrite is not None:
            rule["overwrite"] = overwrite
        local_store._save(
            local_store._guild_dir(interaction.guild_id) / "access_rules.json", data
        )
        await interaction.response.send_message(
            f"Rule **#{rule_id}** updated → level: **{rule['level']}**, "
            f"direction: **{rule['overwrite']}**.",
            ephemeral=True,
        )

    @ar_edit.autocomplete("rule_id")
    async def ar_edit_ac(
        self, interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[int]]:
        return await self.ar_remove_ac(interaction, current)

    @ar_edit.autocomplete("level")
    async def ar_edit_level_ac(
        self, interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[str]]:
        return await self._level_name_autocomplete(interaction, current)

    @access_rule.command(
        name="prune",
        description="Remove stale rules and baselines that reference deleted roles or channels",
    )
    async def ar_prune(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)

        guild = interaction.guild
        valid_role_ids: set[int]    = {r.id for r in guild.roles}
        valid_channel_ids: set[int] = {c.id for c in guild.channels}
        valid_category_ids: set[int] = {c.id for c in guild.categories}

        rules_removed    = local_store.prune_access_rules(
            interaction.guild_id, valid_role_ids, valid_channel_ids
        )
        baselines_removed = local_store.prune_category_baselines(
            interaction.guild_id, valid_category_ids
        )
        bundle_roles_removed = local_store.prune_bundle_roles(
            interaction.guild_id, valid_role_ids
        )
        eg_roles_removed = local_store.prune_exclusive_group_roles(
            interaction.guild_id, valid_role_ids
        )

        total = rules_removed + baselines_removed + bundle_roles_removed + eg_roles_removed
        if total == 0:
            await interaction.followup.send(
                "Nothing to prune — all references are valid.", ephemeral=True
            )
            return

        lines = []
        if rules_removed:
            lines.append(f"• **{rules_removed}** access rule(s) removed")
        if baselines_removed:
            lines.append(f"• **{baselines_removed}** category baseline(s) cleared")
        if bundle_roles_removed:
            lines.append(f"• **{bundle_roles_removed}** bundle role entry(s) removed")
        if eg_roles_removed:
            lines.append(f"• **{eg_roles_removed}** exclusive group role entry(s) removed")

        await interaction.followup.send(
            f"Pruned **{total}** stale reference(s):\n" + "\n".join(lines),
            ephemeral=True,
        )

    @ar_add_category.autocomplete("level")
    @ar_add_channel.autocomplete("level")
    async def ar_level_ac(
        self, interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[str]]:
        return await self._level_name_autocomplete(interaction, current)

    # ==================================================================
    # /status
    # ==================================================================

    @app_commands.command(
        name="status",
        description="Show a summary of all configured permission settings for this server",
    )
    @app_commands.default_permissions(administrator=True)
    async def status(self, interaction: discord.Interaction):
        gid = interaction.guild_id

        levels    = local_store.get_permission_levels(gid)
        bundles   = local_store.get_bundles(gid)
        groups    = local_store.get_exclusive_groups(gid)
        baselines = local_store.get_category_baselines(gid)
        rules_data = local_store.get_access_rules_data(gid)
        rules     = rules_data.get("rules", [])

        embed = discord.Embed(title="Permissions Manager — Status", color=discord.Color.blurple())
        embed.add_field(name="Permission Levels",    value=str(len(levels)),    inline=True)
        embed.add_field(name="Role Bundles",         value=str(len(bundles)),   inline=True)
        embed.add_field(name="Exclusive Groups",     value=str(len(groups)),    inline=True)
        embed.add_field(name="Category Baselines",   value=str(len(baselines)), inline=True)
        embed.add_field(name="Access Rules",         value=str(len(rules)),     inline=True)

        if baselines:
            bl_lines = []
            for cat_id_str, level in baselines.items():
                cat = interaction.guild.get_channel(int(cat_id_str))
                name = cat.name if cat else f"(deleted {cat_id_str})"
                bl_lines.append(f"• {name} → {level}")
            embed.add_field(
                name="Category Baselines Detail",
                value="\n".join(bl_lines),
                inline=False,
            )

        if rules:
            rule_lines = []
            for rule in rules[:10]:  # cap to avoid embed overflow
                role_names = []
                for rid_str in rule["role_ids"]:
                    r = interaction.guild.get_role(int(rid_str))
                    role_names.append(r.name if r else f"(deleted {rid_str})")
                target_names = []
                for tid_str in rule["target_ids"]:
                    t = interaction.guild.get_channel(int(tid_str))
                    target_names.append(t.name if t else f"(deleted {tid_str})")
                rule_lines.append(
                    f"#{rule['id']} {', '.join(role_names)} → "
                    f"{rule['target_type']}({', '.join(target_names)}) "
                    f"[{rule['level']}/{rule.get('overwrite','Allow')}]"
                )
            if len(rules) > 10:
                rule_lines.append(f"… and {len(rules) - 10} more")
            embed.add_field(
                name="Access Rules Detail",
                value="\n".join(rule_lines),
                inline=False,
            )

        embed.set_footer(text=(
            "Use /level list • /bundle list • /exclusive-group list • "
            "/category baseline-list • /access-rule list for full details"
        ))
        await interaction.response.send_message(embed=embed, ephemeral=True)


async def setup(bot: commands.Bot):
    await bot.add_cog(AdminCog(bot))
