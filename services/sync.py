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
        overwrite_dir: str = rule.get("overwrite", "Allow")

        base_overwrite = level_to_overwrite(level_name, guild.id)

        # Deny rules flip every explicit allow → explicit deny.
        if overwrite_dir == "Deny":
            flipped: dict[str, bool] = {}
            for attr, val in base_overwrite:
                if val is True:
                    flipped[attr] = False
                elif val is False:
                    flipped[attr] = True
            final_overwrite = discord.PermissionOverwrite(**flipped)
        else:
            final_overwrite = base_overwrite

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
                    source=f"{discord_role.name} → {level_name} ({overwrite_dir})",
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

async def apply_permission_plan(
    plan: PermissionPlan,
    guild: discord.Guild,
) -> tuple[int, int, int]:
    """
    For every channel/category in the plan:
      - Remove overwrites that exist in Discord but are NOT in the plan (stale).
      - Apply every overwrite that IS in the plan.

    Returns (applied_count, removed_count, error_count).
    """
    channels_by_id: dict[int, discord.abc.GuildChannel] = {
        c.id: c for c in guild.channels
    }
    applied = 0
    removed = 0
    errors = 0

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
      "📝 #phoenix-raid-chat  |  Phoenix Raid Team  →  Chat (Allow)"
      "✅ #general            |  @everyone           →  Chat (no change)"
      "🗑️  #general            |  OldRole             →  (removed — not in plan)"
    """
    channels_by_id: dict[int, discord.abc.GuildChannel] = {
        c.id: c for c in guild.channels
    }
    lines: list[str] = []

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

    return lines
