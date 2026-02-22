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
  /exclusive-group add-role <group> <role>    — add one or more roles to a group
  /exclusive-group remove-role <group> <role> — remove a role from a group

Category baseline commands  (/category ...)
  /category baseline-list                      — list @everyone baselines per category
  /category baseline-set <category> <level>    — set @everyone baseline for a category
  /category baseline-clear <category>          — remove baseline from a category

Access rule commands  (/access-rule ...)
  /access-rule list                                     — list all rules
  /access-rule add-category <role> <category> <level>   — rule targeting a category
  /access-rule add-channel  <role> <channel>  <level>   — rule targeting one or more channels
  /access-rule remove <id>                              — delete a rule by its ID
  /access-rule edit <id> <level>                        — change the permission level
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

_EMBED_FIELD_MAX = 1024


def _truncate_field(lines: list[str], limit: int = _EMBED_FIELD_MAX) -> str:
    """Join lines and hard-truncate to Discord's embed field character limit."""
    text = "\n".join(lines)
    if len(text) <= limit:
        return text
    cut = text.rfind("\n", 0, limit - 20)
    if cut == -1:
        cut = limit - 20
    return text[:cut] + "\n… (truncated)"


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


# Canonical hierarchy order for the five built-in permission levels.
# Custom levels (any name not in this dict) sort after all defaults, alphabetically.
_LEVEL_ORDER: dict[str, int] = {
    name: i for i, name in enumerate(["None", "View", "Chat", "Mod", "Admin"])
}


def _level_sort_key(name: str) -> tuple[int, str]:
    return (_LEVEL_ORDER.get(name, len(_LEVEL_ORDER)), name.lower())


def _desc_sections(title: str, lines: list[str], hint: str = "") -> list[str]:
    """Return one or more description-block strings for a status section.

    Each block starts with a '## __Title__' heading (renders at heading size in
    Discord embed descriptions).  If content exceeds MAX_CONTENT chars the section
    is split into continuation blocks labelled '## __Title (cont.)__'.
    The hint (italic) is appended only to the last block.
    """
    hint_text = f"\n*{hint}*" if hint else ""
    if not lines:
        return [f"## __{title}__\n*(none)*{hint_text}"]
    MAX_CONTENT = 3500  # leaves headroom inside Discord's 4096-char description limit
    blocks: list[str] = []
    chunk: list[str] = []
    chars = 0
    first = True
    for line in lines:
        if chars + len(line) + 1 > MAX_CONTENT and chunk:
            t = title if first else f"{title} (cont.)"
            blocks.append(f"## __{t}__\n" + "\n".join(chunk))
            first, chunk, chars = False, [], 0
        chunk.append(line)
        chars += len(line) + 1
    if chunk:
        t = title if first else f"{title} (cont.)"
        blocks.append(f"## __{t}__\n" + "\n".join(chunk) + hint_text)
    return blocks


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
        view = ConfirmView()
        await interaction.response.send_message(
            "Reset **all** permission levels to built-in defaults? "
            "Any custom levels or edits will be lost. This cannot be undone.",
            view=view, ephemeral=True,
        )
        await view.wait()
        if view.confirmed is None:
            await interaction.edit_original_response(content="Timed out.", view=None)
            return
        if not view.confirmed:
            await view.button_interaction.response.edit_message(content="Cancelled.", view=None)
            return
        local_store.reset_levels_to_default(interaction.guild_id)
        await view.button_interaction.response.edit_message(
            content="Permission levels reset to defaults.", view=None
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
        lines = []
        for name, role_strs in bundles.items():
            display = [_display_role(interaction.guild, rs) for rs in role_strs]
            lines.append(f"**{name}**: {', '.join(display) if display else '*empty*'}")
        embed = discord.Embed(
            title="Role Bundles",
            description=_truncate_field(lines, limit=4096),
            color=discord.Color.green(),
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
        lines = []
        for name, role_strs in groups.items():
            display = [_display_role(interaction.guild, rs) for rs in role_strs]
            lines.append(f"**{name}**: {', '.join(display) if display else '*no roles yet*'}")
        embed = discord.Embed(
            title="Exclusive Groups",
            description=_truncate_field(lines, limit=4096),
            color=discord.Color.orange(),
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

    @exclusive_group.command(name="add-role", description="Add one or more Discord roles to an exclusive group")
    @app_commands.describe(
        name="The exclusive group",
        role1="Role to add",
        role2="Additional role",
        role3="Additional role",
        role4="Additional role",
        role5="Additional role",
    )
    async def eg_add_role(
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
        baselines = local_store.get_category_baselines(interaction.guild_id)
        if str(category.id) not in baselines:
            await interaction.response.send_message(
                f"**{category.name}** has no baseline set.", ephemeral=True
            )
            return
        local_store.clear_category_baseline(interaction.guild_id, str(category.id))
        await interaction.response.send_message(
            f"Cleared baseline for **{category.name}**.", ephemeral=True
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
            role_names = [_display_role(interaction.guild, rid_str) for rid_str in rule["role_ids"]]
            target_names = []
            for tid_str in rule["target_ids"]:
                t = interaction.guild.get_channel(int(tid_str))
                target_names.append(t.name if t else f"(deleted {tid_str})")
            target_type = rule["target_type"].title()
            lines.append(
                f"**#{rule['id']}** {', '.join(role_names)} → "
                f"{target_type}({', '.join(target_names)}) "
                f"[{rule['level']}]"
            )
        embed = discord.Embed(
            title="Access Rules",
            description="\n".join(lines),
            color=discord.Color.blurple(),
        )
        embed.set_footer(text="Rule IDs are permanent — gaps after deletion are normal.")
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @access_rule.command(
        name="add-category",
        description="Set one or more roles' permission level for an entire category",
    )
    @app_commands.describe(
        role1="The role to configure",
        category="The category to apply the permission to",
        level="Permission level to apply",
        role2="Additional role",
        role3="Additional role",
        role4="Additional role",
        role5="Additional role",
    )
    async def ar_add_category(
        self,
        interaction: discord.Interaction,
        role1: discord.Role,
        category: discord.CategoryChannel,
        level: str,
        role2: discord.Role | None = None,
        role3: discord.Role | None = None,
        role4: discord.Role | None = None,
        role5: discord.Role | None = None,
    ):
        roles = list(dict.fromkeys(r for r in [role1, role2, role3, role4, role5] if r is not None))
        levels = local_store.get_permission_levels(interaction.guild_id)
        if level not in levels:
            names = ", ".join(sorted(levels.keys()))
            await interaction.response.send_message(
                f"Level **{level}** not found. Available: {names}", ephemeral=True
            )
            return
        added = []
        for role in roles:
            rule_id = local_store.add_access_rule(
                interaction.guild_id,
                role_ids=[str(role.id)],
                target_type="category",
                target_ids=[str(category.id)],
                level=level,
            )
            added.append(f"• **#{rule_id}** {role.name} → {category.name}")
        await interaction.response.send_message(
            f"Added {len(added)} rule(s) [{level}]:\n" + "\n".join(added),
            ephemeral=True,
        )

    @ar_add_category.autocomplete("level")
    async def ar_add_category_level_ac(
        self, interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[str]]:
        return await self._level_name_autocomplete(interaction, current)

    @access_rule.command(
        name="add-channel",
        description="Set one or more roles' permission level for one or more specific channels",
    )
    @app_commands.describe(
        role1="The role to configure",
        channel1="The channel to apply the permission to",
        level="Permission level to apply",
        role2="Additional role",
        role3="Additional role",
        role4="Additional role",
        role5="Additional role",
        channel2="Additional channel",
        channel3="Additional channel",
        channel4="Additional channel",
        channel5="Additional channel",
    )
    async def ar_add_channel(
        self,
        interaction: discord.Interaction,
        role1: discord.Role,
        channel1: discord.abc.GuildChannel,
        level: str,
        role2: discord.Role | None = None,
        role3: discord.Role | None = None,
        role4: discord.Role | None = None,
        role5: discord.Role | None = None,
        channel2: discord.abc.GuildChannel | None = None,
        channel3: discord.abc.GuildChannel | None = None,
        channel4: discord.abc.GuildChannel | None = None,
        channel5: discord.abc.GuildChannel | None = None,
    ):
        roles = list(dict.fromkeys(r for r in [role1, role2, role3, role4, role5] if r is not None))
        channels = list(dict.fromkeys(c for c in [channel1, channel2, channel3, channel4, channel5] if c is not None))

        bad = [c.name for c in channels if isinstance(c, discord.CategoryChannel)]
        if bad:
            await interaction.response.send_message(
                f"{', '.join(f'**{n}**' for n in bad)} is a category — "
                "use `/access-rule add-category` instead.",
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

        added = []
        for role in roles:
            for channel in channels:
                rule_id = local_store.add_access_rule(
                    interaction.guild_id,
                    role_ids=[str(role.id)],
                    target_type="channel",
                    target_ids=[str(channel.id)],
                    level=level,
                )
                added.append(f"• **#{rule_id}** {role.name} → #{channel.name}")

        await interaction.response.send_message(
            f"Added {len(added)} rule(s) [{level}]:\n" + "\n".join(added),
            ephemeral=True,
        )

    @ar_add_channel.autocomplete("level")
    async def ar_add_channel_level_ac(
        self, interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[str]]:
        return await self._level_name_autocomplete(interaction, current)

    @access_rule.command(
        name="remove",
        description="Remove one or more access rules by ID",
    )
    @app_commands.describe(
        rule_id1="Rule to remove",
        rule_id2="Additional rule to remove",
        rule_id3="Additional rule to remove",
        rule_id4="Additional rule to remove",
        rule_id5="Additional rule to remove",
    )
    async def ar_remove(
        self,
        interaction: discord.Interaction,
        rule_id1: int,
        rule_id2: int | None = None,
        rule_id3: int | None = None,
        rule_id4: int | None = None,
        rule_id5: int | None = None,
    ):
        ids = list(dict.fromkeys(
            rid for rid in [rule_id1, rule_id2, rule_id3, rule_id4, rule_id5]
            if rid is not None
        ))
        data = local_store.get_access_rules_data(interaction.guild_id)
        rules_map = {r["id"]: r for r in data.get("rules", [])}

        found = [rules_map[rid] for rid in ids if rid in rules_map]
        missing = [rid for rid in ids if rid not in rules_map]

        if not found:
            await interaction.response.send_message(
                f"No rules found with ID(s): {', '.join(f'**#{i}**' for i in missing)}",
                ephemeral=True,
            )
            return

        lines = []
        for rule in found:
            role_names = [_display_role(interaction.guild, rid_str) for rid_str in rule["role_ids"]]
            target_names = []
            for tid_str in rule["target_ids"]:
                t = interaction.guild.get_channel(int(tid_str))
                target_names.append(t.name if t else f"(deleted {tid_str})")
            lines.append(
                f"• **#{rule['id']}** {', '.join(role_names)} → "
                f"{rule['target_type']}({', '.join(target_names)}) [{rule['level']}]"
            )
        if missing:
            lines.append(f"*(not found: {', '.join(f'#{i}' for i in missing)} — will be skipped)*")

        view = ConfirmView()
        await interaction.response.send_message(
            f"Remove {len(found)} access rule(s)?\n" + "\n".join(lines),
            view=view,
            ephemeral=True,
        )
        await view.wait()
        if view.confirmed is None:
            await interaction.edit_original_response(content="Timed out.", view=None)
            return
        if not view.confirmed:
            await view.button_interaction.response.edit_message(content="Cancelled.", view=None)
            return

        removed = []
        for rule in found:
            try:
                local_store.remove_access_rule(interaction.guild_id, rule["id"])
                removed.append(f"**#{rule['id']}**")
            except KeyError:
                pass
        await view.button_interaction.response.edit_message(
            content=f"Deleted {len(removed)} rule(s): {', '.join(removed)}.", view=None
        )

    async def _rule_id_autocomplete(
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
            )[:100]
            if current in str(rule["id"]) or current.lower() in label.lower():
                choices.append(app_commands.Choice(name=label, value=rule["id"]))
        return choices[:25]

    @ar_remove.autocomplete("rule_id1")
    async def ar_remove_ac1(self, interaction: discord.Interaction, current: str) -> list[app_commands.Choice[int]]:
        return await self._rule_id_autocomplete(interaction, current)

    @ar_remove.autocomplete("rule_id2")
    async def ar_remove_ac2(self, interaction: discord.Interaction, current: str) -> list[app_commands.Choice[int]]:
        return await self._rule_id_autocomplete(interaction, current)

    @ar_remove.autocomplete("rule_id3")
    async def ar_remove_ac3(self, interaction: discord.Interaction, current: str) -> list[app_commands.Choice[int]]:
        return await self._rule_id_autocomplete(interaction, current)

    @ar_remove.autocomplete("rule_id4")
    async def ar_remove_ac4(self, interaction: discord.Interaction, current: str) -> list[app_commands.Choice[int]]:
        return await self._rule_id_autocomplete(interaction, current)

    @ar_remove.autocomplete("rule_id5")
    async def ar_remove_ac5(self, interaction: discord.Interaction, current: str) -> list[app_commands.Choice[int]]:
        return await self._rule_id_autocomplete(interaction, current)

    @access_rule.command(
        name="edit",
        description="Change the permission level on an existing access rule",
    )
    @app_commands.describe(
        rule_id="The rule to edit (select from the list)",
        level="New permission level",
    )
    async def ar_edit(
        self,
        interaction: discord.Interaction,
        rule_id: int,
        level: str,
    ):
        levels = local_store.get_permission_levels(interaction.guild_id)
        if level not in levels:
            names = ", ".join(sorted(levels.keys()))
            await interaction.response.send_message(
                f"Level **{level}** not found. Available: {names}", ephemeral=True
            )
            return
        try:
            updated = local_store.update_access_rule(
                interaction.guild_id, rule_id, level=level
            )
        except KeyError:
            await interaction.response.send_message(
                f"Access rule **#{rule_id}** not found.", ephemeral=True
            )
            return
        await interaction.response.send_message(
            f"Rule **#{rule_id}** updated → level: **{updated['level']}**.",
            ephemeral=True,
        )

    @ar_edit.autocomplete("rule_id")
    async def ar_edit_ac(
        self, interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[int]]:
        return await self._rule_id_autocomplete(interaction, current)

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

    # ==================================================================
    # /status
    # ==================================================================

    @app_commands.command(
        name="status",
        description="Show all configured permission settings for this server",
    )
    @app_commands.default_permissions(administrator=True)
    async def status(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        gid   = interaction.guild_id
        guild = interaction.guild

        levels    = local_store.get_permission_levels(gid)
        bundles   = local_store.get_bundles(gid)
        groups    = local_store.get_exclusive_groups(gid)
        baselines = local_store.get_category_baselines(gid)
        rules     = local_store.get_access_rules_data(gid).get("rules", [])

        # --- Permission Levels: hierarchy order, one bullet per line ---
        sorted_level_names = sorted(levels.keys(), key=_level_sort_key)
        level_lines = [f"• {name}" for name in sorted_level_names]

        # --- Role Bundles ---
        bundle_lines = []
        for name, role_strs in bundles.items():
            display = [_display_role(guild, rs) for rs in role_strs]
            bundle_lines.append(f"**{name}**: {', '.join(display) if display else '*empty*'}")

        # --- Exclusive Groups ---
        eg_lines = []
        for name, role_strs in groups.items():
            display = [_display_role(guild, rs) for rs in role_strs]
            eg_lines.append(f"**{name}**: {', '.join(display) if display else '*empty*'}")

        # --- Category Baselines ---
        bl_lines = []
        for cat_id_str, level in baselines.items():
            cat = guild.get_channel(int(cat_id_str))
            cat_name = cat.name if cat else f"(deleted {cat_id_str})"
            bl_lines.append(f"• **{cat_name}** → {level}")

        # --- Access Rules: grouped by target (categories, then channels) ---
        # bucket[target_id_str] = list of rules whose target_ids contains that id
        cat_bucket: dict[str, list] = {}
        ch_bucket:  dict[str, list] = {}
        for rule in rules:
            bucket = cat_bucket if rule["target_type"] == "category" else ch_bucket
            for tid_str in rule["target_ids"]:
                bucket.setdefault(tid_str, []).append(rule)

        def _target_name(tid_str: str) -> str:
            ch = guild.get_channel(int(tid_str))
            return ch.name if ch else f"deleted:{tid_str}"

        def _rule_sort_key(rule: dict) -> tuple:
            """Sort within a target: @everyone first, then alpha by primary role, then level, then ID."""
            role_names = [_display_role(guild, rid) for rid in rule["role_ids"]]
            primary = role_names[0] if role_names else ""
            return (
                0 if primary == "@everyone" else 1,
                primary.lower(),
                _level_sort_key(rule["level"]),
                rule["id"],
            )

        def _rule_group_lines(bucket: dict[str, list]) -> list[str]:
            """Return display lines for one bucket.

            Targets sorted alphabetically; blank line between groups for readability.
            Rules within each target sorted by: @everyone first, then role name, then level.
            """
            lines: list[str] = []
            sorted_targets = sorted(bucket, key=lambda t: _target_name(t).lower())
            for i, tid_str in enumerate(sorted_targets):
                if i > 0:
                    lines.append("")  # visual gap between target groups
                lines.append(f"**{_target_name(tid_str)}**")
                for rule in sorted(bucket[tid_str], key=_rule_sort_key):
                    role_names = [_display_role(guild, rid) for rid in rule["role_ids"]]
                    lines.append(f"  › #{rule['id']}  {', '.join(role_names)} [{rule['level']}]")
            return lines

        cat_rule_lines = _rule_group_lines(cat_bucket)
        ch_rule_lines  = _rule_group_lines(ch_bucket)

        # Accurate counts: rules (total entries) vs targets (distinct channels/categories).
        n_cat_rules, n_cat_targets = sum(len(v) for v in cat_bucket.values()), len(cat_bucket)
        n_ch_rules,  n_ch_targets  = sum(len(v) for v in ch_bucket.values()),  len(ch_bucket)

        _AR_HINT = "/access-rule add-category • /access-rule add-channel • /access-rule edit • /access-rule remove • /access-rule prune • /sync-permissions"

        all_blocks: list[str] = [
            *_desc_sections(
                f"Permission Levels ({len(levels)})", level_lines,
                hint="/level view • /level create • /level edit • /level delete • /level reset-defaults",
            ),
            *_desc_sections(
                f"Role Bundles ({len(bundles)})", bundle_lines,
                hint="/bundle view • /bundle create • /bundle add-role • /bundle remove-role • /bundle delete • /assign • /remove",
            ),
            *_desc_sections(
                f"Exclusive Groups ({len(groups)})", eg_lines,
                hint="/exclusive-group create • /exclusive-group add-role • /exclusive-group remove-role • /exclusive-group delete",
            ),
            *_desc_sections(
                f"Category Baselines ({len(baselines)})", bl_lines,
                hint="/category baseline-set • /category baseline-clear",
            ),
            # Access rules: separate blocks for category and channel targets.
            # Hint goes on the last block only.
            *_desc_sections(
                f"Category Rules ({n_cat_rules} rules / {n_cat_targets} targets)",
                cat_rule_lines,
            ),
            *_desc_sections(
                f"Channel Rules ({n_ch_rules} rules / {n_ch_targets} targets)",
                ch_rule_lines,
                hint=_AR_HINT,
            ),
        ]

        # --- pack blocks into embeds (≤4000 chars per description) ---
        _DESC_MAX = 4000
        embeds: list[discord.Embed] = []
        parts: list[str] = []
        size = 0

        for block in all_blocks:
            block_size = len(block) + 2  # +2 for the \n\n separator
            if size + block_size > _DESC_MAX and parts:
                e = discord.Embed(color=discord.Color.blurple())
                e.description = "\n\n".join(parts)
                embeds.append(e)
                parts, size = [], 0
            parts.append(block)
            size += block_size

        if parts:
            e = discord.Embed(color=discord.Color.blurple())
            e.description = "\n\n".join(parts)
            embeds.append(e)

        if embeds:
            embeds[0].title = "Permissions Manager — Status"

        await interaction.followup.send(embeds=embeds[:10], ephemeral=True)


async def setup(bot: commands.Bot):
    await bot.add_cog(AdminCog(bot))
