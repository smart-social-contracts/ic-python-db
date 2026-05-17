# Schema Versioning & Upgrade Safety

## What Problem Does This Solve?

Your canister stores data. When you change your code and redeploy, the old data is still in the old format. For example:

```python
# Before: your entity has two fields
class Product(Entity):
    name = String()
    price = Integer()   # price in cents

# After: you rename the field and change the type
class Product(Entity):
    name = String()
    cost = Float()      # price in dollars
```

The canister still has `{"name": "Widget", "price": 999}` saved. Your new code expects `cost` (a float), but the data has `price` (an integer). Without protection, this data gets silently lost or causes errors.

ic-python-db prevents this by:
1. Remembering what the schema looked like before the upgrade
2. Comparing it to the new schema after the upgrade
3. Blocking the upgrade if something dangerous changed and you didn't write migration code

## The Three Layers

### Layer 1: Auto-Migration (You Write Nothing)

If all you did was **add a field with a default value**, ic-python-db handles it for you:

```python
# v1
class Product(Entity):
    __version__ = 1
    name = String()

# v2 — added a field with a default
class Product(Entity):
    __version__ = 2
    name = String()
    active = Boolean(default=True)
```

When your canister loads an old Product that doesn't have `active`, ic-python-db sees the field is missing, sees it has `default=True`, and fills it in. No migration code needed.

### Layer 2: Custom Migration (You Write `migrate()`)

For anything more complex — renaming a field, changing a type, computing new values from old ones — you override `migrate()`:

```python
class Product(Entity):
    __version__ = 2
    name = String()
    price_dollars = Float()

    @classmethod
    def migrate(cls, obj, from_version, to_version):
        if from_version == 1:
            # v1 stored price in cents as an integer
            # v2 stores it in dollars as a float
            cents = obj.pop("price_cents", 0)
            obj["price_dollars"] = cents / 100.0
        return obj
```

`migrate()` receives the raw stored data as a dictionary. You transform it and return it. ic-python-db calls `migrate()` first, then fills in any remaining missing defaults.

### Layer 3: Upgrade Enforcement (The Safety Net)

This is the guard that prevents you from deploying a dangerous change you forgot to handle. You enable it by calling `check_upgrade_compatibility()` in your canister's `post_upgrade`:

```python
from basilisk import post_upgrade
from ic_python_db import Database

@post_upgrade
def on_post_upgrade():
    db = Database.get_instance()
    db.check_upgrade_compatibility()
```

Here's what it does, step by step:

1. Loads the schema that was saved during the previous deploy
2. Looks at your current Entity classes and builds a new schema
3. Compares the two and lists every difference
4. For each dangerous difference, checks: "Does this entity have a `migrate()` method?"
5. If any dangerous change has no `migrate()` → the upgrade is **rejected**

When the upgrade is rejected, `post_upgrade` crashes (raises `SchemaIncompatibleError`). The IC sees the crash and **rolls back the entire upgrade** — your canister goes back to the previous code and data, as if nothing happened. Your data is safe.

## What Counts as "Safe" vs. "Dangerous"?

| What You Changed | Safe? | Why |
|---|---|---|
| Added a field **with** a default value | Yes | Old data gets the default automatically |
| Added a field **without** a default | **No** | Old data has no value for this field |
| Removed a field | Yes | Old data is just ignored |
| Changed a field's type (e.g. `Integer` → `String`) | **No** | Old data is the wrong type |
| Changed constraints (e.g. `max_length`) | Yes | Only affects future writes |
| Added a new Entity class | Yes | No old data exists |
| Removed an Entity class | Yes | Old data stays but is unused |
| Added a relationship | Yes | No old references exist |
| Removed a relationship | Yes | Old references are just ignored |
| Changed relationship type (e.g. `OneToOne` → `ManyToMany`) | **No** | Old references have the wrong structure |

**Safe** = ic-python-db handles it automatically. No action needed.

**Dangerous** = You must write a `migrate()` method. If you don't, the upgrade is blocked.

## First Deploy

On the very first deploy, there's no previous schema to compare against. `check_upgrade_compatibility()` just saves the current schema as the starting point and moves on.

## Schema Hash

Every time the schema is saved, a SHA-256 hash is saved alongside it. This is useful for detecting whether someone else upgraded the canister between when you last checked and when you deploy:

```python
db = Database.get_instance()
current_hash = db.get_schema_hash()
```

## Inspecting the Schema

You normally don't need to call these directly — `check_upgrade_compatibility()` does it all for you. But they're available if you want to build tooling around schema changes, for example a pre-deploy CLI check that fetches the stored schema from a canister and compares it to your local code:

```python
from ic_python_db import build_schema, diff_schemas

# old_schema: fetched from the canister (e.g. via __browse__ or a query call)
# new_schema: built from your local Entity classes
changes = diff_schemas(old_schema, new_schema)
for change in changes:
    print(f"{change.entity_type}.{change.field}: {change.reason}")
    print(f"  Safe: {change.safe}")
```

## Quick Reference

| Function / Method | What It Does |
|---|---|
| `db.check_upgrade_compatibility()` | Compare old vs. new schema, block if unsafe. Call this from `post_upgrade`. |
| `db.build_schema_from_entities()` | Get a dictionary describing all your Entity classes |
| `db.get_schema_hash()` | Get the SHA-256 hash of the stored schema |
| `db.save_schema()` | Save the current schema without checking compatibility |
| `diff_schemas(old, new)` | Compare two schema dictionaries, returns list of changes |
| `schema_hash(schema)` | Compute the hash of a schema dictionary |
| `Entity.migrate(cls, obj, from_version, to_version)` | Override this to handle breaking changes |
| `SchemaIncompatibleError` | Raised when a dangerous change has no `migrate()` |

## See Also

- [README.md](../README.md) — Quick start and examples
- [ACCESS_CONTROL.md](ACCESS_CONTROL.md) — User context and permissions
- [HOOKS.md](HOOKS.md) — Entity lifecycle hooks
