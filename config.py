# ---------------------------------------------------------------------------
# Permission level defaults — these are the factory definitions.
# Edits made via /level commands are persisted to data/permission_levels.json
# and take precedence over these defaults.
#
# Keys are discord.py PermissionOverwrite attribute names.
# True = explicitly allow, False = explicitly deny, omitted = neutral (inherit).
# ---------------------------------------------------------------------------
PERMISSION_LEVELS_DEFAULT: dict[str, dict[str, bool]] = {
    "None": {
        "view_channel": False,
    },
    "View": {
        "view_channel":               True,
        "read_message_history":       True,
        "send_messages":              False,
        "send_messages_in_threads":   False,
        "add_reactions":              False,
        "connect":                    False,
        "speak":                      False,
        "stream":                     False,
        "use_soundboard":             False,
    },
    "Chat": {
        "change_nickname":            True,
        "view_channel":               True,
        "read_message_history":       True,
        "send_messages":              True,
        "send_messages_in_threads":   True,
        "embed_links":                True,
        "attach_files":               True,
        "add_reactions":              True,
        "use_external_emojis":        True,
        "use_external_stickers":      True,
        "use_application_commands":   True,
        "send_voice_messages":        True,
        "connect":                    True,
        "speak":                      True,
        "use_voice_activation":       True,
        "stream":                     True,
        "use_soundboard":             True,
        "use_external_sounds":        True,
        "use_embedded_activities":    True,
    },
    "Mod": {
        "view_channel":               True,
        "read_message_history":       True,
        "send_messages":              True,
        "send_messages_in_threads":   True,
        "create_public_threads":      True,
        "create_private_threads":     True,
        "embed_links":                True,
        "attach_files":               True,
        "add_reactions":              True,
        "use_external_emojis":        True,
        "use_external_stickers":      True,
        "use_application_commands":   True,
        "send_voice_messages":        True,
        "connect":                    True,
        "speak":                      True,
        "use_voice_activation":       True,
        "stream":                     True,
        "use_soundboard":             True,
        "use_external_sounds":        True,
        "use_embedded_activities":    True,
        # Moderation
        "manage_messages":            True,
        "manage_threads":             True,
        "mute_members":               True,
        "deafen_members":             True,
        "move_members":               True,
        "manage_channels":            True,
        "moderate_members":           True,
        "kick_members":               True,
        "manage_nicknames":           True,
        "mention_everyone":           True,
    },
    "Admin": {
        "administrator":              True,
    },
}

# Empty by default — create bundles via /bundle commands
BUNDLES_DEFAULT: dict[str, list[str]] = {}


# ---------------------------------------------------------------------------
# Permission groupings for the /level edit UI
# Values are discord.py PermissionOverwrite attribute names.
# ---------------------------------------------------------------------------
PERMISSION_GROUPS: dict[str, list[str]] = {
    "General": [
        "administrator",
        "view_audit_log",
        "manage_guild",
        "manage_roles",
        "manage_channels",
        "kick_members",
        "ban_members",
        "create_instant_invite",
        "change_nickname",
        "manage_nicknames",
        "manage_emojis_and_stickers",
        "manage_webhooks",
        "manage_events",
        "view_channel",
        "moderate_members",
        "view_guild_insights",
    ],
    "Text": [
        "send_messages",
        "send_messages_in_threads",
        "create_public_threads",
        "create_private_threads",
        "embed_links",
        "attach_files",
        "add_reactions",
        "use_external_emojis",
        "use_external_stickers",
        "mention_everyone",
        "manage_messages",
        "manage_threads",
        "read_message_history",
        "send_tts_messages",
        "use_application_commands",
        "send_voice_messages",
    ],
    "Voice": [
        "connect",
        "speak",
        "stream",
        "use_soundboard",
        "use_external_sounds",
        "mute_members",
        "deafen_members",
        "move_members",
        "use_voice_activation",
        "priority_speaker",
        "request_to_speak",
        "use_embedded_activities",
    ],
}

# Flat list of all permission attribute names, used for autocomplete
ALL_PERMISSIONS: list[str] = [p for group in PERMISSION_GROUPS.values() for p in group]
