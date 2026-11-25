# Access Control with Context Management

This guide shows how to implement access control using the `on_event` hooks combined with the `as_user()` context manager.

## Overview

`ic-python-db` provides context management for tracking the current user:

- **`db.as_user(user_id)`** - Context manager for running operations as a specific user
- **`get_caller_id()`** - Get the current user ID
- **`set_caller_id(user_id)`** - Set the current user ID

> **Note:** The context system uses a simple module-level variable, which is safe for IC canisters since they are single-threaded. It is **not** thread-safe for multi-threaded environments outside the IC.

## Basic Usage

```python
from ic_python_db import Database, Entity, String
from ic_python_db.mixins import TimestampedMixin

db = Database.get_instance()

class Document(Entity, TimestampedMixin):
    title = String()

# Create documents as different users
with db.as_user("alice"):
    doc1 = Document(title="Alice's Doc")  # Owner: alice

with db.as_user("bob"):
    doc2 = Document(title="Bob's Doc")  # Owner: bob
```

`TimestampedMixin` raises `PermissionError` when an entity is saved by a caller other than its owner. The owner is stored with the row and read back when the row is loaded, so the check holds on an entity loaded in a later message as on the instance that created it.

Who may claim and write:

- The first save of an entity claims it: the caller becomes its creator and owner.
- After that, only the owner may write it, delete it, or give it away. A write by anyone else raises `PermissionError` before it changes anything: no hook runs, and no value, index entry or relation moves. `entity.set_owner(new_owner)` is such a write; it stores the transfer at once and records the caller as the updater.
- A caller id and an owner are non-empty strings. `set_caller_id`, `as_user` and `set_owner` raise `ValueError` for `None` or `""`: inside the mixin, no owner means "not claimed yet", which any caller may write.
- `deserialize()` ignores the `owner`, `creator`, `updater`, `timestamp_created` and `timestamp_updated` in the data. An import claims a new row for the caller, and an update records the caller as the updater, as any other write does.

What the check does not do:

- It trusts the caller id your code sets (`set_caller_id`, `as_user`).
- The state is kept in plain attributes (`_owner`, `_creator`, `_updater`). Python cannot stop code that holds the instance from assigning them; the check covers the library's own paths, not that.
- A class that declares its own field named `owner`, `creator`, `updater`, `timestamp_created` or `timestamp_updated` keeps that field's value in the row, so the mixin's state is not restored on load: for such a class the check does not hold after a load.

A row stored before its class had the mixin has no owner, and the first caller to save it (or to `set_owner` it) becomes its creator. Loading a row, even one that has to be migrated, claims no ownership and sets no updater or timestamp.

## Access Control Pattern

With `TimestampedMixin`, writing and deleting another caller's entity is already refused, before any hook runs:

```python
from ic_python_db import Entity, String
from ic_python_db.mixins import TimestampedMixin

class ProtectedDocument(Entity, TimestampedMixin):
    title = String()
    content = String()

with db.as_user("alice"):
    doc = ProtectedDocument(title="Alice's")

with db.as_user("bob"):
    doc.title = "Bob's"  # PermissionError; the row and its indexes are unchanged
```

`on_event` hooks add rules on top of that, for the owner's own writes: see Field-Level Access below.

## Nested Contexts

Contexts can be nested and automatically reset:

```python
with db.as_user("admin"):
    admin_doc = Document(title="Admin Doc")  # Owner: admin
    
    with db.as_user("user"):
        user_doc = Document(title="User Doc")  # Owner: user
    
    # Back to admin context
    another_doc = Document(title="Another Admin")  # Owner: admin
```

## Benefits

### Automatic Cleanup
Context manager ensures caller ID is reset even if exceptions occur:

```python
try:
    with db.as_user("alice"):
        doc = Document(title="Test")
        raise RuntimeError("Error!")
except RuntimeError:
    pass

# Caller is automatically reset to previous value
print(get_caller_id())  # 'system'
```

### Clear Scope
Easy to see which operations run as which user:

```python
# Everything in this block runs as alice
with db.as_user("alice"):
    doc = Document(title="Doc 1")
    doc.content = "Updated"
    doc.save()
```

## Advanced Patterns

### Admin Override

There is none with `TimestampedMixin`: the owner check runs before the hook, so a hook cannot let anyone but the owner write, and acting as the owner (`as_user(entity._owner)`) would record the owner as the updater of a change they did not make. An administrator who must change another caller's entity has its owner hand it over with `set_owner` first.

### Field-Level Access

```python
class Employee(Entity, TimestampedMixin):
    name = String()
    salary = Integer()
    
    @staticmethod
    def on_event(entity, field_name, old_value, new_value, action):
        caller = get_caller_id()
        
        # Salary requires HR permission
        if field_name == "salary" and action == ACTION_MODIFY:
            if caller not in ["hr_manager", "admin"]:
                return False, None
        
        return True, new_value
```

### Audit Trail

```python
class AuditedEntity(Entity, TimestampedMixin):
    value = String()
    
    audit_log = []
    
    @staticmethod
    def on_event(entity, field_name, old_value, new_value, action):
        caller = get_caller_id()
        
        # Log all access attempts
        AuditedEntity.audit_log.append({
            "caller": caller,
            "action": action,
            "field": field_name,
            "old": old_value,
            "new": new_value
        })
        
        return True, new_value
```

## Migration from os.environ

If you were using `os.environ.get("CALLER_ID")`, migrate to the new context approach:

### Before (Not Thread-Safe)
```python
import os

os.environ["CALLER_ID"] = "alice"
doc = Document(title="My Doc")
os.environ["CALLER_ID"] = "system"  # Easy to forget!
```

### After (Thread-Safe)
```python
with db.as_user("alice"):
    doc = Document(title="My Doc")
# Automatically resets
```

## See Also

- [HOOKS.md](HOOKS.md) - Complete hooks documentation
- [examples/simple_access_control.py](../examples/simple_access_control.py) - Working example
