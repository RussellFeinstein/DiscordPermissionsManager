"""
sync.py — builds a permission plan from local store, applies it to Discord.

Flow:
  1. build_permission_plan()  → produces a PermissionPlan (pure data, no Discord calls)
  2. apply_permission_plan()  → applies the plan to Discord (sets planned overwrites,
                                removes stale overwrites on planned channels)
  3. diff_permission_plan()   → returns a human-readable list of changes (for /preview)

Resolution strategy
-------------------
Discord objects (roles, categories, channels) are resolved by Discord ID, which is
stored directly in the local access rules and category baselines.

Permission level definitions come from local_store (config.py defaults + any edits).
Category baselines and access rules come from local_store.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
import discord

from services import local_store


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

@dataclass
class OverwriteEntry:
    target: discord.Role | discord.Member
    overwrite: discord.PermissionOverwrite
    source: str   # human label e.g. "@everyone baseline → None"


@dataclass
class PermissionPlan:
    """
    Maps each Discord category/channel id to the overwrites that should be set on it.
    plan.entries[channel_or_category_id] = [OverwriteEntry, ...]
    """
    entries: dict[int, list[OverwriteEntry]] = field(default_factory=dict)

    def add(self, target_id: int, entry: OverwriteEntry) -> None:
        self.entries.setdefault(target_id, []).append(entry)


# ---------------------------------------------------------------------------
# Permission level → discord.PermissionOverwrite
# ---------------------------------------------------------------------------

def level_to_overwrite(level_name: str, guild_id: int) -> discord.PermissionOverwrite:
    """
    Look up a named permission level from local_store and convert to a
    discord.PermissionOverwrite.
      True  → explicitly allow
      False → explicitly deny
      key missing → neutral (inherit; discord.py default is None)
    """
    levels = local_store.get_permission_levels(guild_id)
    perms = levels.get(level_name, {})
    return discord.PermissionOverwrite(**perms)


# ---------------------------------------------------------------------------
# Plan builder
# ---------------------------------------------------------------------------

def build_permission_plan(guild: discord.Guild) -> PermissionPlan:
    """
    Reads category baselines and access rules from local store, then produces
    a PermissionPlan describing exactly what overwrites should exist on every
    category and channel.

    No Discord API write calls are made here.
    """
    plan = PermissionPlan()

    discord_roles_by_id: dict[int, discord.Role] = {r.id: r for r in guild.roles}
    discord_cats_by_id: dict[int, discord.CategoryChannel] = {c.id: c for c in guild.categories}
    discord_channels_by_id: dict[int, discord.abc.GuildChannel] = {
        c.id: c for c in guild.channels if not isinstance(c, discord.CategoryChannel)
    }

    everyone = guild.default_role

    # ------------------------------------------------------------------
    # 1. @everyone baseline for every category
    # ------------------------------------------------------------------
    baselines = local_store.get_category_baselines(guild.id)
    for cat_id_str, level_name in baselines.items():
        try:
            cat_id = int(cat_id_str)
        except ValueError:
            print(f"[sync] WARNING: invalid category ID '{cat_id_str}' in baselines — skipping")
            continue

        discord_cat = discord_cats_by_id.get(cat_id)
        if not discord_cat:
            print(f"[sync] WARNING: category {cat_id_str} not found in Discord — skipping baseline")
            continue

        plan.add(discord_cat.id, OverwriteEntry(
            target=everyone,
            overwrite=level_to_overwrite(level_name, guild.id),
            source=f"@everyone baseline → {level_name}",
        ))

    # ------------------------------------------------------------------
    # 2. Role-specific overwrites from access rules
    # ------------------------------------------------------------------
    rules_data = local_store.get_access_rules_data(guild.id)
    for rule in rules_data.get("rules", []):
        level_name: str = rule["level"]

        final_overwrite = level_to_overwrite(level_name, guild.id)

        # Resolve target channels/categories
        targets: list[discord.abc.GuildChannel] = []
        if rule["target_type"] == "category":
            for tid_str in rule.get("target_ids", []):
                try:
                    tid = int(tid_str)
                except ValueError:
                    continue
                dc = discord_cats_by_id.get(tid)
                if dc:
                    targets.append(dc)
                else:
                    print(f"[sync] WARNING: category {tid_str} not found in Discord — skipping")
        elif rule["target_type"] == "channel":
            for tid_str in rule.get("target_ids", []):
                try:
                    tid = int(tid_str)
                except ValueError:
                    continue
                dc = discord_channels_by_id.get(tid)
                if dc:
                    targets.append(dc)
                else:
                    print(f"[sync] WARNING: channel {tid_str} not found in Discord — skipping")

        # Resolve roles and add entries
        for rid_str in rule.get("role_ids", []):
            try:
                rid = int(rid_str)
            except ValueError:
                continue
            discord_role = discord_roles_by_id.get(rid)
            if not discord_role:
                print(f"[sync] WARNING: role {rid_str} not found in Discord — skipping")
                continue

            for target in targets:
                plan.add(target.id, OverwriteEntry(
                    target=discord_role,
                    overwrite=final_overwrite,
                    source=f"{discord_role.name} → {level_name}",
                ))

    # ------------------------------------------------------------------
    # 3. Propagate category @everyone baseline to unsynced channels
    # ------------------------------------------------------------------
    # Channels not synced to their parent category don't inherit the
    # category's @everyone overwrite automatically.  Any such channel
    # that already has plan entries (from an access rule) needs the
    # baseline applied explicitly, or @everyone falls back to the
    # server default rather than the configured level.
    for chan_id, entries in list(plan.entries.items()):
        channel = discord_channels_by_id.get(chan_id)
        if channel is None:
            continue  # it's a category entry — skip
        if getattr(channel, "permissions_synced", True):
            continue  # synced channels inherit from category — nothing to do
        if channel.category_id is None:
            continue  # no parent category

        # Skip if @everyone is already explicitly planned for this channel
        if any(entry.target == everyone for entry in entries):
            continue

        cat_level = baselines.get(str(channel.category_id))
        if cat_level is None:
            continue

        plan.add(chan_id, OverwriteEntry(
            target=everyone,
            overwrite=level_to_overwrite(cat_level, guild.id),
            source=f"@everyone baseline (category) → {cat_level}",
        ))

    return plan


# ---------------------------------------------------------------------------
# Rate-limit helper
# ---------------------------------------------------------------------------

# Brief pause between Discord permission writes to stay well inside the
# global rate limit (50 req/s).  discord.py handles per-route limits
# automatically; this guards against bulk syncs on large servers.
_WRITE_DELAY = 0.1   # seconds


async def _set_with_backoff(
    channel: discord.abc.GuildChannel,
    target: discord.Role | discord.Member,
    overwrite: discord.PermissionOverwrite | None,
    max_retries: int = 3,
) -> bool:
    """
    Call channel.set_permissions with exponential backoff on 429s.
    overwrite=None removes the overwrite (stale-cleanup path).
    Returns True on success, False after all retries are exhausted.
    """
    delay = 1.0
    for attempt in range(max_retries):
        try:
            await channel.set_permissions(target, overwrite=overwrite)
            await asyncio.sleep(_WRITE_DELAY)
            return True
        except discord.HTTPException as e:
            if e.status == 429:
                retry_after = float(getattr(e, "retry_after", delay))
                print(
                    f"[sync] Rate limited on #{channel.name} — "
                    f"retrying in {retry_after:.1f}s (attempt {attempt + 1}/{max_retries})"
                )
                await asyncio.sleep(retry_after)
                delay *= 2
            else:
                print(f"[sync] HTTP {e.status} on #{channel.name} for {target}: {e.text}")
                return False
    print(f"[sync] Gave up on #{channel.name} for {target} after {max_retries} attempts")
    return False


# ---------------------------------------------------------------------------
# Apply plan
# ---------------------------------------------------------------------------

def _is_managed(channel: discord.abc.GuildChannel) -> bool:
    """
    Return True if this channel/category is fully managed by the plan.
    Non-category channels that are synced to their parent category are
    skipped — Discord propagates the category's overwrites automatically.
    """
    if isinstance(channel, discord.CategoryChannel):
        return True
    return not getattr(channel, "permissions_synced", True)


async def apply_permission_plan(
    plan: PermissionPlan,
    guild: discord.Guild,
) -> tuple[int, int, int]:
    """
    For every managed channel/category in the guild:
      - If it has plan entries: remove stale overwrites, apply planned ones.
      - If it has NO plan entries: remove all existing overwrites so nothing
        outside the bot's configuration lingers.

    Channels that are synced to their parent category are left alone —
    Discord handles them automatically when the category is updated.

    Returns (applied_count, removed_count, error_count).
    """
    channels_by_id: dict[int, discord.abc.GuildChannel] = {
        c.id: c for c in guild.channels
    }
    applied = 0
    removed = 0
    errors = 0

    # --- Channels/categories that ARE in the plan ---
    for target_id, entries in plan.entries.items():
        channel = channels_by_id.get(target_id)
        if not channel:
            continue

        planned_targets = {entry.target for entry in entries}

        # Remove stale overwrites: exist on Discord, not in the plan for this channel.
        for existing_target in list(channel.overwrites):
            if existing_target not in planned_targets:
                ok = await _set_with_backoff(channel, existing_target, None)
                if ok:
                    removed += 1
                    print(f"[sync] Removed stale overwrite: #{channel.name} / {existing_target.name}")
                else:
                    errors += 1

        # Apply planned overwrites.
        for entry in entries:
            ok = await _set_with_backoff(channel, entry.target, entry.overwrite)
            if ok:
                applied += 1
            else:
                errors += 1

    # --- Channels/categories NOT in the plan ---
    # Strip all their overwrites so leftover manual permissions don't muddy
    # the bot's configuration.  Synced channels are skipped — they inherit
    # from their parent category and don't need independent cleanup.
    for channel in guild.channels:
        if channel.id in plan.entries:
            continue  # already handled above
        if not _is_managed(channel):
            continue  # synced channel — leave it alone

        for existing_target in list(channel.overwrites):
            ok = await _set_with_backoff(channel, existing_target, None)
            if ok:
                removed += 1
                print(f"[sync] Removed unmanaged overwrite: #{channel.name} / {existing_target.name}")
            else:
                errors += 1

    return applied, removed, errors


# ---------------------------------------------------------------------------
# Diff / preview
# ---------------------------------------------------------------------------

def diff_permission_plan(
    plan: PermissionPlan,
    guild: discord.Guild,
) -> list[str]:
    """
    Compare the plan against current Discord state.
    Returns human-readable change lines:
      "📝 #phoenix-raid-chat  |  Phoenix Raid Team  →  Chat"
      "✅ #general            |  @everyone           →  Chat (no change)"
      "🗑️  #general            |  OldRole             →  (removed — not in plan)"
      "🗑️  #old-channel        |  SomeRole            →  (removed — channel unmanaged)"
    """
    channels_by_id: dict[int, discord.abc.GuildChannel] = {
        c.id: c for c in guild.channels
    }
    lines: list[str] = []

    # --- Channels/categories in the plan ---
    for target_id, entries in plan.entries.items():
        channel = channels_by_id.get(target_id)
        if not channel:
            lines.append(f"⚠️  Channel/category ID {target_id} not found in Discord")
            continue

        current_overwrites = dict(channel.overwrites)
        planned_targets = {entry.target for entry in entries}

        # Stale overwrites that will be removed.
        for existing_target in current_overwrites:
            if existing_target not in planned_targets:
                lines.append(
                    f"🗑️  #{channel.name}  |  {existing_target.name}  →  (removed — not in plan)"
                )

        # Planned overwrites (changed or unchanged).
        for entry in entries:
            current = current_overwrites.get(entry.target)
            status = "✅" if current == entry.overwrite else "📝"
            lines.append(
                f"{status}  #{channel.name}  |  {entry.target.name}  →  {entry.source}"
            )

    # --- Channels/categories NOT in the plan ---
    # Show any overwrites that will be stripped during apply.
    for channel in guild.channels:
        if channel.id in plan.entries:
            continue
        if not _is_managed(channel):
            continue  # synced — inherits from category, won't be touched

        for existing_target in channel.overwrites:
            lines.append(
                f"🗑️  #{channel.name}  |  {existing_target.name}  →  (removed — channel unmanaged)"
            )

    return lines
