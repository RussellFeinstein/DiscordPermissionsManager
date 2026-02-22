"""
local_store.py — per-guild persistence for permission levels and bundles.

Data lives in data/{guild_id}/ (gitignored).
Falls back to config.py defaults when no file exists yet for that guild.

Concurrency notes
-----------------
All mutating functions acquire a per-guild threading.Lock before doing their
read-modify-write cycle, so concurrent bot commands on the same guild cannot
race and overwrite each other's changes.

_save() writes to a temporary file first, then replaces the target atomically
(os.replace), so a crash mid-write cannot leave a corrupt JSON file.

For multi-instance deployments (e.g. multiple Railway workers sharing a
volume) you would need a cross-process lock or a proper database instead.
"""

import copy
import json
import os
import tempfile
import threading
from pathlib import Path

from config import PERMISSION_LEVELS_DEFAULT, BUNDLES_DEFAULT

_DATA_DIR = Path(os.environ.get("DATA_DIR") or Path(__file__).parent.parent / "data")

# Per-guild locks — prevents concurrent read-modify-write races within one process.
_locks: dict[int, threading.Lock] = {}
_locks_meta = threading.Lock()   # guards the _locks dict itself


def _get_lock(guild_id: int) -> threading.Lock:
    with _locks_meta:
        if guild_id not in _locks:
            _locks[guild_id] = threading.Lock()
        return _locks[guild_id]


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _guild_dir(guild_id: int) -> Path:
    d = _DATA_DIR / str(guild_id)
    d.mkdir(parents=True, exist_ok=True)
    return d


def _load(path: Path, default: dict) -> dict:
    if path.exists():
        try:
            with open(path, encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError) as e:
            print(f"[local_store] WARNING: could not read {path}: {e} — using defaults")
    return copy.deepcopy(default)


def _save(path: Path, data: dict) -> None:
    """Atomically write data to path via a temp file + os.replace."""
    dir_ = path.parent
    fd, tmp = tempfile.mkstemp(dir=dir_, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


# ---------------------------------------------------------------------------
# Permission levels
# ---------------------------------------------------------------------------

def get_permission_levels(guild_id: int) -> dict[str, dict[str, bool]]:
    """
    Returns {level_name: {discord_attr: True | False}}.
    Omitted keys mean neutral (inherit from role/server defaults).
    """
    return _load(_guild_dir(guild_id) / "permission_levels.json", PERMISSION_LEVELS_DEFAULT)


def set_permission(guild_id: int, level_name: str, attr: str, value: bool | None) -> None:
    """
    Set a single permission attribute on a level.
    value=None removes the key (neutral/inherit).
    Raises KeyError if level_name does not exist.
    """
    with _get_lock(guild_id):
        levels = get_permission_levels(guild_id)
        if level_name not in levels:
            raise KeyError(f"Permission level '{level_name}' not found")
        if value is None:
            levels[level_name].pop(attr, None)
        else:
            levels[level_name][attr] = value
        _save(_guild_dir(guild_id) / "permission_levels.json", levels)


def create_level(guild_id: int, name: str, copy_from: str | None = None) -> None:
    """Create a new permission level, optionally cloning an existing one."""
    with _get_lock(guild_id):
        levels = get_permission_levels(guild_id)
        if name in levels:
            raise ValueError(f"Permission level '{name}' already exists")
        levels[name] = dict(levels[copy_from]) if copy_from else {}
        _save(_guild_dir(guild_id) / "permission_levels.json", levels)


def delete_level(guild_id: int, name: str) -> None:
    with _get_lock(guild_id):
        levels = get_permission_levels(guild_id)
        if name not in levels:
            raise KeyError(f"Permission level '{name}' not found")
        del levels[name]
        _save(_guild_dir(guild_id) / "permission_levels.json", levels)


def reset_levels_to_default(guild_id: int) -> None:
    """Overwrite the JSON file with the factory defaults from config.py."""
    with _get_lock(guild_id):
        _save(_guild_dir(guild_id) / "permission_levels.json", copy.deepcopy(PERMISSION_LEVELS_DEFAULT))


# ---------------------------------------------------------------------------
# Bundles
# ---------------------------------------------------------------------------

def get_bundles(guild_id: int) -> dict[str, list[str]]:
    """Returns {bundle_name: [role_name, ...]}."""
    return _load(_guild_dir(guild_id) / "bundles.json", BUNDLES_DEFAULT)


def create_bundle(guild_id: int, name: str) -> None:
    with _get_lock(guild_id):
        bundles = get_bundles(guild_id)
        if name in bundles:
            raise ValueError(f"Bundle '{name}' already exists")
        bundles[name] = []
        _save(_guild_dir(guild_id) / "bundles.json", bundles)


def delete_bundle(guild_id: int, name: str) -> None:
    with _get_lock(guild_id):
        bundles = get_bundles(guild_id)
        if name not in bundles:
            raise KeyError(f"Bundle '{name}' not found")
        del bundles[name]
        _save(_guild_dir(guild_id) / "bundles.json", bundles)


def add_role_to_bundle(guild_id: int, bundle_name: str, role_name: str) -> None:
    with _get_lock(guild_id):
        bundles = get_bundles(guild_id)
        if bundle_name not in bundles:
            raise KeyError(f"Bundle '{bundle_name}' not found")
        if role_name not in bundles[bundle_name]:
            bundles[bundle_name].append(role_name)
            _save(_guild_dir(guild_id) / "bundles.json", bundles)


def remove_role_from_bundle(guild_id: int, bundle_name: str, role_name: str) -> None:
    with _get_lock(guild_id):
        bundles = get_bundles(guild_id)
        if bundle_name not in bundles:
            raise KeyError(f"Bundle '{bundle_name}' not found")
        bundles[bundle_name] = [r for r in bundles[bundle_name] if r != role_name]
        _save(_guild_dir(guild_id) / "bundles.json", bundles)


# ---------------------------------------------------------------------------
# Exclusive groups
# ---------------------------------------------------------------------------

def get_exclusive_groups(guild_id: int) -> dict[str, list[str]]:
    """Returns {group_name: [role_name, ...]}."""
    return _load(_guild_dir(guild_id) / "exclusive_groups.json", {})


def create_exclusive_group(guild_id: int, name: str) -> None:
    with _get_lock(guild_id):
        groups = get_exclusive_groups(guild_id)
        if name in groups:
            raise ValueError(f"Exclusive group '{name}' already exists")
        groups[name] = []
        _save(_guild_dir(guild_id) / "exclusive_groups.json", groups)


def delete_exclusive_group(guild_id: int, name: str) -> None:
    with _get_lock(guild_id):
        groups = get_exclusive_groups(guild_id)
        if name not in groups:
            raise KeyError(f"Exclusive group '{name}' not found")
        del groups[name]
        _save(_guild_dir(guild_id) / "exclusive_groups.json", groups)


def add_role_to_exclusive_group(guild_id: int, group_name: str, role_name: str) -> None:
    with _get_lock(guild_id):
        groups = get_exclusive_groups(guild_id)
        if group_name not in groups:
            raise KeyError(f"Exclusive group '{group_name}' not found")
        if role_name not in groups[group_name]:
            groups[group_name].append(role_name)
            _save(_guild_dir(guild_id) / "exclusive_groups.json", groups)


def remove_role_from_exclusive_group(guild_id: int, group_name: str, role_name: str) -> None:
    with _get_lock(guild_id):
        groups = get_exclusive_groups(guild_id)
        if group_name not in groups:
            raise KeyError(f"Exclusive group '{group_name}' not found")
        groups[group_name] = [r for r in groups[group_name] if r != role_name]
        _save(_guild_dir(guild_id) / "exclusive_groups.json", groups)


# ---------------------------------------------------------------------------
# Category baseline permissions
# ---------------------------------------------------------------------------

def get_category_baselines(guild_id: int) -> dict[str, str]:
    """Returns {category_discord_id: level_name}."""
    return _load(_guild_dir(guild_id) / "category_baselines.json", {})


def set_category_baseline(guild_id: int, category_id: str, level_name: str) -> None:
    with _get_lock(guild_id):
        baselines = get_category_baselines(guild_id)
        baselines[category_id] = level_name
        _save(_guild_dir(guild_id) / "category_baselines.json", baselines)


def clear_category_baseline(guild_id: int, category_id: str) -> None:
    with _get_lock(guild_id):
        baselines = get_category_baselines(guild_id)
        baselines.pop(category_id, None)
        _save(_guild_dir(guild_id) / "category_baselines.json", baselines)


# ---------------------------------------------------------------------------
# Access rules
# ---------------------------------------------------------------------------

def get_access_rules_data(guild_id: int) -> dict:
    """
    Returns {"next_id": int, "rules": [...]}.
    Each rule: {"id": int, "role_ids": [str], "target_type": "category"|"channel",
                "target_ids": [str], "level": str, "overwrite": "Allow"|"Deny"}.
    """
    return _load(_guild_dir(guild_id) / "access_rules.json", {"next_id": 1, "rules": []})


def add_access_rule(
    guild_id: int,
    role_ids: list[str],
    target_type: str,
    target_ids: list[str],
    level: str,
    overwrite: str = "Allow",
) -> int:
    """Add an access rule. Returns the new rule's integer ID."""
    with _get_lock(guild_id):
        data = get_access_rules_data(guild_id)
        rule_id = data["next_id"]
        data["rules"].append({
            "id": rule_id,
            "role_ids": role_ids,
            "target_type": target_type,
            "target_ids": target_ids,
            "level": level,
            "overwrite": overwrite,
        })
        data["next_id"] = rule_id + 1
        _save(_guild_dir(guild_id) / "access_rules.json", data)
        return rule_id


def remove_access_rule(guild_id: int, rule_id: int) -> None:
    with _get_lock(guild_id):
        data = get_access_rules_data(guild_id)
        before = len(data["rules"])
        data["rules"] = [r for r in data["rules"] if r["id"] != rule_id]
        if len(data["rules"]) == before:
            raise KeyError(f"Access rule #{rule_id} not found")
        _save(_guild_dir(guild_id) / "access_rules.json", data)


def update_access_rule(
    guild_id: int,
    rule_id: int,
    *,
    level: str | None = None,
    overwrite: str | None = None,
) -> dict:
    """
    Update an existing access rule in-place.
    Only fields that are not None are modified; others are preserved.
    Returns a copy of the updated rule.
    Raises KeyError if the rule is not found.
    """
    with _get_lock(guild_id):
        data = get_access_rules_data(guild_id)
        rule = next((r for r in data["rules"] if r["id"] == rule_id), None)
        if rule is None:
            raise KeyError(f"Access rule #{rule_id} not found")
        if level is not None:
            rule["level"] = level
        if overwrite is not None:
            rule["overwrite"] = overwrite
        _save(_guild_dir(guild_id) / "access_rules.json", data)
        return dict(rule)


# ---------------------------------------------------------------------------
# Prune helpers (remove stale references to deleted Discord objects)
# ---------------------------------------------------------------------------

def _prune_role_list(role_strs: list[str], valid_role_ids: set[int]) -> tuple[list[str], int]:
    """
    Filter a list of stored role strings (ID or legacy name).
    Integer-ID entries that are no longer in valid_role_ids are dropped.
    Legacy name strings are always kept (cannot validate without the Discord API).
    Returns (kept_list, removed_count).
    """
    kept, removed = [], 0
    for rs in role_strs:
        try:
            if int(rs) in valid_role_ids:
                kept.append(rs)
            else:
                removed += 1
        except ValueError:
            kept.append(rs)  # legacy name — cannot validate, keep it
    return kept, removed


def prune_access_rules(
    guild_id: int,
    valid_role_ids: set[int],
    valid_channel_ids: set[int],
) -> int:
    """
    Remove access rules where any stored role ID or target ID no longer
    exists in Discord.  Legacy non-integer entries are kept.
    Returns the number of rules removed.
    """
    def _rule_valid(rule: dict) -> bool:
        for rid in rule["role_ids"]:
            try:
                if int(rid) not in valid_role_ids:
                    return False
            except ValueError:
                pass  # legacy string — keep
        for tid in rule["target_ids"]:
            try:
                if int(tid) not in valid_channel_ids:
                    return False
            except ValueError:
                pass
        return True

    with _get_lock(guild_id):
        data = get_access_rules_data(guild_id)
        before = len(data["rules"])
        data["rules"] = [r for r in data["rules"] if _rule_valid(r)]
        removed = before - len(data["rules"])
        if removed:
            _save(_guild_dir(guild_id) / "access_rules.json", data)
        return removed


def prune_category_baselines(guild_id: int, valid_category_ids: set[int]) -> int:
    """
    Remove baselines whose category no longer exists in Discord.
    Returns the number of baselines removed.
    """
    with _get_lock(guild_id):
        baselines = get_category_baselines(guild_id)
        before = len(baselines)
        kept = {k: v for k, v in baselines.items() if int(k) in valid_category_ids}
        removed = before - len(kept)
        if removed:
            _save(_guild_dir(guild_id) / "category_baselines.json", kept)
        return removed


def prune_bundle_roles(guild_id: int, valid_role_ids: set[int]) -> int:
    """
    Remove deleted role IDs from all bundles.
    Returns the total number of role entries removed.
    """
    with _get_lock(guild_id):
        bundles = get_bundles(guild_id)
        total_removed, changed = 0, False
        for name, role_strs in bundles.items():
            kept, count = _prune_role_list(role_strs, valid_role_ids)
            if count:
                bundles[name] = kept
                total_removed += count
                changed = True
        if changed:
            _save(_guild_dir(guild_id) / "bundles.json", bundles)
        return total_removed


def prune_exclusive_group_roles(guild_id: int, valid_role_ids: set[int]) -> int:
    """
    Remove deleted role IDs from all exclusive groups.
    Returns the total number of role entries removed.
    """
    with _get_lock(guild_id):
        groups = get_exclusive_groups(guild_id)
        total_removed, changed = 0, False
        for name, role_strs in groups.items():
            kept, count = _prune_role_list(role_strs, valid_role_ids)
            if count:
                groups[name] = kept
                total_removed += count
                changed = True
        if changed:
            _save(_guild_dir(guild_id) / "exclusive_groups.json", groups)
        return total_removed
