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
        with open(path, encoding="utf-8") as f:
            return json.load(f)
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
