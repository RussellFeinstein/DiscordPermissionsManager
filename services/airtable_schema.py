"""
airtable_schema.py — validates and creates the Airtable schema for the bot.

Required tables: Roles, Categories, Channels, Access Rules.
Created in dependency order so linked-record fields resolve correctly.

Requires the Airtable token to have schema.bases:write scope for creation.
"""

from __future__ import annotations

from pyairtable import Api


_LEVEL_CHOICES = [
    {"name": "None"}, {"name": "View"}, {"name": "Chat"}, {"name": "Mod"}, {"name": "Admin"},
]

_ROLES_FIELDS = [
    {"name": "Role Name",       "type": "singleLineText"},
    {"name": "Discord ID",      "type": "singleLineText"},
    {"name": "Exclusive Group", "type": "singleSelect",
     "options": {"choices": [
         {"name": "None"}, {"name": "Leadership"}, {"name": "Team Officer"},
         {"name": "Membership Status"}, {"name": "Team Assignment"},
     ]}},
]

_CATEGORIES_FIELDS = [
    {"name": "Category Name",       "type": "singleLineText"},
    {"name": "Discord ID",          "type": "singleLineText"},
    {"name": "Baseline Permission", "type": "singleSelect",
     "options": {"choices": _LEVEL_CHOICES}},
]


def _get_table_map(token: str, base_id: str) -> dict[str, str]:
    """Return {table_name: table_id} for all existing tables in the base."""
    api = Api(token)
    base = api.base(base_id)
    try:
        schema = base.schema()
        return {t.name: t.id for t in schema.tables}
    except AttributeError:
        # Older pyairtable API
        tables = base.tables()
        return {t.name: t.id for t in tables}


def check_missing(token: str, base_id: str) -> list[str]:
    """Return sorted list of required table names that do not yet exist."""
    required = {"Roles", "Categories", "Channels", "Access Rules"}
    existing = set(_get_table_map(token, base_id).keys())
    return sorted(required - existing)


def create_missing(token: str, base_id: str) -> list[str]:
    """
    Create any missing required tables. Returns list of created table names.
    Tables are created in dependency order (Roles → Categories → Channels → Access Rules).
    """
    api = Api(token)
    base = api.base(base_id)
    table_ids = _get_table_map(token, base_id)
    created: list[str] = []

    if "Roles" not in table_ids:
        t = base.create_table("Roles", _ROLES_FIELDS)
        table_ids["Roles"] = t.id
        created.append("Roles")

    if "Categories" not in table_ids:
        t = base.create_table("Categories", _CATEGORIES_FIELDS)
        table_ids["Categories"] = t.id
        created.append("Categories")

    if "Channels" not in table_ids:
        t = base.create_table("Channels", [
            {"name": "Channel Name", "type": "singleLineText"},
            {"name": "Discord ID",   "type": "singleLineText"},
        ])
        table_ids["Channels"] = t.id
        created.append("Channels")

    if "Access Rules" not in table_ids:
        base.create_table("Access Rules", [
            {"name": "ID",                 "type": "autoNumber"},
            {"name": "Roles",              "type": "multipleRecordLinks",
             "options": {"linkedTableId": table_ids["Roles"]}},
            {"name": "Channel/Category",   "type": "singleSelect",
             "options": {"choices": [{"name": "Category"}, {"name": "Channel"}]}},
            {"name": "Channel Categories", "type": "multipleRecordLinks",
             "options": {"linkedTableId": table_ids["Categories"]}},
            {"name": "Channels",           "type": "multipleRecordLinks",
             "options": {"linkedTableId": table_ids["Channels"]}},
            {"name": "Permission Level",   "type": "singleSelect",
             "options": {"choices": _LEVEL_CHOICES}},
            {"name": "Overwrite",          "type": "singleSelect",
             "options": {"choices": [{"name": "Allow"}, {"name": "Deny"}]}},
        ])
        created.append("Access Rules")

    return created


def ensure_discord_id_fields(token: str, base_id: str) -> list[str]:
    """
    Add a 'Discord ID' field (singleLineText) to Roles, Categories, and Channels
    if those tables exist but are missing the field.

    Returns a list of table names that were updated.
    Called automatically by /setup import-discord before writing IDs.
    """
    api = Api(token)
    base = api.base(base_id)
    updated: list[str] = []

    for table_name in ("Roles", "Categories", "Channels"):
        try:
            table = base.table(table_name)
            schema = table.schema()
            existing_fields = {f.name for f in schema.fields}
            if "Discord ID" not in existing_fields:
                table.create_field("Discord ID", "singleLineText")
                updated.append(table_name)
                print(f"[schema] Added 'Discord ID' field to {table_name}")
        except Exception as e:
            print(f"[schema] Could not add 'Discord ID' to {table_name}: {e}")

    return updated
