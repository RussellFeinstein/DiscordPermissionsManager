"""
access.py — scope-based bot access control.

Scopes map named command groups to a permission level a server admin
can grant to any Discord role.  Admins always bypass scope checks.

Usage inside a Cog.interaction_check or command callback:

    from services.access import check_scope

    async def interaction_check(self, interaction):
        return await check_scope(interaction)

The required scope is inferred automatically from
interaction.command.qualified_name.
"""

from __future__ import annotations

import discord
from services import local_store


# ---------------------------------------------------------------------------
# Scope definitions
# ---------------------------------------------------------------------------

ALL_SCOPES: list[str] = [
    "assign",        # /assign, /remove
    "bundles",       # /bundle ...
    "groups",        # /exclusive-group ...
    "access-rules",  # /category ..., /access-rule ...
    "levels",        # /level ...
    "sync",          # /preview-permissions, /sync-permissions
    "status",        # /status
]

SCOPE_LABELS: dict[str, str] = {
    "assign":        "Role Assignment (/assign, /remove)",
    "bundles":       "Bundle Management (/bundle ...)",
    "groups":        "Exclusive Groups (/exclusive-group ...)",
    "access-rules":  "Access Rules & Category Baselines (/access-rule ..., /category ...)",
    "levels":        "Permission Levels (/level ...)",
    "sync":          "Sync Permissions (/preview-permissions, /sync-permissions)",
    "status":        "Status Overview (/status)",
}

# Maps the first word of a command's qualified_name → required scope.
# /bot-access is not listed here — it's always administrator-only via
# default_permissions and admins bypass scope checks unconditionally.
CMD_SCOPE: dict[str, str] = {
    "assign":                "assign",
    "remove":                "assign",
    "bundle":                "bundles",
    "exclusive-group":       "groups",
    "category":              "access-rules",
    "access-rule":           "access-rules",
    "level":                 "levels",
    "preview-permissions":   "sync",
    "sync-permissions":      "sync",
    "status":                "status",
}


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def user_has_scope(interaction: discord.Interaction, scope: str) -> bool:
    """
    Return True if the interaction user may use commands requiring this scope.
    Admins always pass.  Others need the scope granted on at least one role.
    """
    if interaction.user.guild_permissions.administrator:
        return True
    bot_access = local_store.get_bot_access(interaction.guild_id)
    user_role_ids = {str(r.id) for r in interaction.user.roles}
    for role_id in user_role_ids:
        if scope in bot_access.get(role_id, []):
            return True
    return False


async def check_scope(interaction: discord.Interaction) -> bool:
    """
    Gate-check for Cog.interaction_check.

    Infers the required scope from the command being invoked, checks
    whether the user is permitted, and sends an ephemeral error message
    if not.

    Returns True to allow the command, False to deny it (error already sent).
    """
    if interaction.guild is None:
        await interaction.response.send_message(
            "This command can only be used inside a server.", ephemeral=True
        )
        return False

    if interaction.user.guild_permissions.administrator:
        return True

    cmd_first = interaction.command.qualified_name.split()[0]
    scope = CMD_SCOPE.get(cmd_first)

    if scope is None:
        # /bot-access falls here for non-admins, but default_permissions
        # prevents them from ever reaching this point.  Deny anything else.
        await interaction.response.send_message(
            "You don't have permission to use this command.", ephemeral=True
        )
        return False

    if user_has_scope(interaction, scope):
        return True

    await interaction.response.send_message(
        f"You don't have permission to use this command.\n"
        f"A server administrator can grant your role the **{scope}** scope "
        f"via `/bot-access grant`.",
        ephemeral=True,
    )
    return False
