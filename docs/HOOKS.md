# Entity Hooks

Intercept and control entity lifecycle events (create, modify, delete) by defining an `on_event` static method on your Entity class.

## How It Works

When an entity is created, modified, or deleted, ic-python-db calls the `on_event` hook (if defined) **before** applying the change. The hook can:

- **Allow or reject** the change
- **Transform** the value before it's stored
- **Log** changes for auditing

## Signature

```python
@staticmethod
def on_event(entity, field_name, old_value, new_value, action):
    # entity:     the Entity instance being changed
    # field_name: name of the field being changed (None for entity-level actions like delete)
    # old_value:  current value of the field
    # new_value:  proposed new value
    # action:     ACTION_CREATE, ACTION_MODIFY, or ACTION_DELETE
    
    return True, new_value  # (allow: bool, modified_value: any)
```

### Return Values

The hook must return one of:

| Return | Meaning |
|--------|---------|
| `(True, value)` | Allow the change, use `value` as the stored value |
| `(False, None)` | Reject the change (raises `ValueError` for modifications, `PermissionError` for deletions) |
| `True` | Allow the change, keep the original `new_value` unchanged |

### Action Constants

Import the action constants from `ic_python_db`:

```python
from ic_python_db import ACTION_CREATE, ACTION_MODIFY, ACTION_DELETE
```

| Constant | When |
|----------|------|
| `ACTION_CREATE` | Entity is being created (called per field during `__init__`) |
| `ACTION_MODIFY` | A field value is being changed |
| `ACTION_DELETE` | Entity is being deleted (`field_name` is `None`) |

## Patterns

### Validation

Reject values that don't meet your criteria:

```python
from ic_python_db import Entity, String, Integer

class User(Entity):
    username = String()
    email = String()
    age = Integer()

    @staticmethod
    def on_event(entity, field_name, old_value, new_value, action):
        if field_name == "username" and new_value and len(new_value) < 3:
            return False, None  # Raises ValueError

        if field_name == "email" and new_value and "@" not in new_value:
            return False, None

        if field_name == "age" and new_value is not None:
            if new_value < 0 or new_value > 150:
                return False, None

        return True, new_value
```

### Value Transformation

Modify values before they are stored:

```python
class User(Entity):
    name = String()
    age = Integer()

    @staticmethod
    def on_event(entity, field_name, old_value, new_value, action):
        if field_name == "name" and new_value:
            return True, new_value.upper()  # Auto-capitalize

        if field_name == "age" and new_value and new_value > 150:
            return True, 150  # Clamp to max

        return True, new_value

user = User(name="alice", age=200)
assert user.name == "ALICE"
assert user.age == 150
```

### Protected Deletion

Prevent certain entities from being deleted:

```python
from ic_python_db import ACTION_DELETE

class Account(Entity):
    name = String()
    protected = Integer()

    @staticmethod
    def on_event(entity, field_name, old_value, new_value, action):
        if action == ACTION_DELETE and entity.protected == 1:
            return False, None  # Raises PermissionError

        return True, new_value

admin = Account(name="Admin", protected=1)
admin.delete()  # Raises PermissionError: "Hook rejected deletion"
```

### Change Tracking

Record all modifications for auditing:

```python
from ic_python_db import ACTION_MODIFY, ACTION_DELETE

changes = []

class AuditedDocument(Entity):
    title = String()
    content = String()

    @staticmethod
    def on_event(entity, field_name, old_value, new_value, action):
        if action == ACTION_MODIFY and field_name:
            changes.append({
                "entity_id": entity._id,
                "field": field_name,
                "old_value": old_value,
                "new_value": new_value,
            })

        if action == ACTION_DELETE:
            changes.append({
                "entity_id": entity._id,
                "action": "deleted",
            })

        return True, new_value
```

### Auto-Generated Fields

Derive one field from another inside the hook. Use `_do_not_save` to avoid triggering a save cycle when setting the derived field:

```python
class User(Entity):
    name = String()
    slug = String()

    @staticmethod
    def on_event(entity, field_name, old_value, new_value, action):
        if field_name == "name" and new_value and not entity.slug:
            entity._do_not_save = True
            entity.slug = new_value.lower().replace(" ", "-")
            entity._do_not_save = False

        return True, new_value

user = User(name="John Doe")
assert user.slug == "john-doe"
```

## Combining with Access Control

Hooks work naturally with the `as_user()` context manager for ownership-based access control. See [ACCESS_CONTROL.md](ACCESS_CONTROL.md) for details.

```python
from ic_python_db import ACTION_MODIFY, ACTION_DELETE, Entity, String
from ic_python_db.context import get_caller_id
from ic_python_db.mixins import TimestampedMixin

class Document(Entity, TimestampedMixin):
    title = String()

    @staticmethod
    def on_event(entity, field_name, old_value, new_value, action):
        caller = get_caller_id()

        if action in (ACTION_MODIFY, ACTION_DELETE):
            if entity._owner != caller:
                return False, None  # Only owner can modify/delete

        return True, new_value
```

## Notes

- Hooks are optional. Entities without `on_event` work normally.
- The hook is called **before** the change is persisted. Returning `False` prevents the change entirely.
- During `ACTION_CREATE`, the hook is called once per field as the entity is initialized.
- During `ACTION_DELETE`, `field_name` is `None`.

## See Also

- [ACCESS_CONTROL.md](ACCESS_CONTROL.md) — Access control patterns
- [examples/hooks_example.py](../examples/hooks_example.py) — Working examples
- [examples/simple_access_control.py](../examples/simple_access_control.py) — Access control example
