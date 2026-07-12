"""Schema introspection, diffing, and upgrade compatibility checking.

Provides tools to:
- Build a JSON-serializable schema descriptor from Entity subclasses
- Compare old and new schemas to classify changes as safe or breaking
- Enforce upgrade compatibility (reject breaking changes without migrate())
"""

import hashlib
import json
from dataclasses import dataclass, field
from enum import Enum
from typing import TYPE_CHECKING, Any, Dict, List, Optional, Type

if TYPE_CHECKING:
    from .db_engine import Database


class ChangeType(Enum):
    ADDED = "added"
    REMOVED = "removed"
    TYPE_CHANGED = "type_changed"
    DEFAULT_CHANGED = "default_changed"
    CONSTRAINTS_CHANGED = "constraints_changed"
    RELATIONSHIP_CHANGED = "relationship_changed"
    ENTITY_ADDED = "entity_added"
    ENTITY_REMOVED = "entity_removed"
    VERSION_CHANGED = "version_changed"


@dataclass
class SchemaChange:
    """Describes a single change between two schema versions."""

    entity_type: str
    field: Optional[str]
    change_type: ChangeType
    old_value: Any = None
    new_value: Any = None
    safe: bool = False
    reason: str = ""

    def __repr__(self):
        if self.field:
            return f"SchemaChange({self.entity_type}.{self.field}: {self.change_type.value}, safe={self.safe})"
        return f"SchemaChange({self.entity_type}: {self.change_type.value}, safe={self.safe})"


def build_field_descriptor(prop) -> Dict[str, Any]:
    """Build a descriptor dict for a single Property or Relation."""
    from .properties import (
        Boolean,
        Float,
        Integer,
        ManyToMany,
        ManyToOne,
        OneToMany,
        OneToOne,
        Relation,
        String,
    )

    desc: Dict[str, Any] = {}

    if isinstance(prop, Relation):
        desc["kind"] = "relationship"
        desc["type"] = type(prop).__name__
        entity_types = prop.entity_types
        if isinstance(entity_types, list):
            desc["target"] = entity_types
        else:
            desc["target"] = entity_types
        if prop.reverse_name:
            desc["inverse"] = prop.reverse_name
        desc["many"] = prop.many
        if isinstance(prop, ManyToMany) and prop.unidirectional:
            desc["unidirectional"] = True
        return desc

    desc["kind"] = "property"
    desc["type"] = type(prop).__name__

    if prop.default is not None:
        desc["default"] = prop.default
    elif hasattr(prop, "default"):
        desc["has_default"] = prop.default is not None

    if isinstance(prop, String):
        constraints = {}
        if prop.validator:
            # Extract min/max length from closure
            closure_vars = _extract_closure_vars(prop.validator)
            if closure_vars.get("min_length") is not None:
                constraints["min_length"] = closure_vars["min_length"]
            if closure_vars.get("max_length") is not None:
                constraints["max_length"] = closure_vars["max_length"]
        if constraints:
            desc["constraints"] = constraints

    elif isinstance(prop, (Integer, Float)):
        constraints = {}
        if prop.validator:
            closure_vars = _extract_closure_vars(prop.validator)
            if closure_vars.get("min_value") is not None:
                constraints["min_value"] = closure_vars["min_value"]
            if closure_vars.get("max_value") is not None:
                constraints["max_value"] = closure_vars["max_value"]
        if constraints:
            desc["constraints"] = constraints

    return desc


def _extract_closure_vars(func) -> Dict[str, Any]:
    """Extract closure variables from a validator function."""
    result = {}
    if hasattr(func, "__code__") and hasattr(func, "__closure__"):
        if func.__closure__:
            free_vars = func.__code__.co_freevars
            for name, cell in zip(free_vars, func.__closure__):
                try:
                    result[name] = cell.cell_contents
                except ValueError:
                    pass
    return result


def build_schema(entity_types: Dict[str, Type]) -> Dict[str, Any]:
    """Build a complete schema descriptor from registered entity types.

    Args:
        entity_types: Dict mapping type names to Entity classes,
                      typically from Database._entity_types

    Returns:
        Schema descriptor dict suitable for JSON serialization and comparison.
    """
    from .entity import Entity
    from .properties import Property, Relation

    schema: Dict[str, Any] = {}
    seen_classes = set()

    for type_name, entity_cls in entity_types.items():
        if entity_cls in seen_classes:
            continue
        if not isinstance(entity_cls, type) or not issubclass(entity_cls, Entity):
            continue
        if entity_cls is Entity:
            continue

        seen_classes.add(entity_cls)
        full_name = entity_cls.get_full_type_name()

        entity_desc: Dict[str, Any] = {
            "version": entity_cls.__version__,
            "fields": {},
            "relationships": {},
        }

        has_custom_migrate = _has_custom_migrate(entity_cls)
        if has_custom_migrate:
            entity_desc["has_migrate"] = True

        for cls in reversed(entity_cls.__mro__):
            for attr_name, attr_value in cls.__dict__.items():
                if attr_name.startswith("_"):
                    continue

                if isinstance(attr_value, Property):
                    entity_desc["fields"][attr_name] = build_field_descriptor(
                        attr_value
                    )
                elif isinstance(attr_value, Relation):
                    entity_desc["relationships"][attr_name] = build_field_descriptor(
                        attr_value
                    )

        schema[full_name] = entity_desc

    return schema


def _has_custom_migrate(entity_cls: Type) -> bool:
    """Check if an Entity class has overridden the default migrate() method."""
    from .entity import Entity

    if "migrate" not in entity_cls.__dict__:
        return False
    return entity_cls.migrate is not Entity.migrate


def schema_hash(schema: Dict[str, Any]) -> str:
    """Compute a deterministic hash of a schema descriptor."""
    canonical = json.dumps(schema, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest()


def diff_schemas(
    old_schema: Dict[str, Any], new_schema: Dict[str, Any]
) -> List[SchemaChange]:
    """Compare two schema descriptors and return a list of changes.

    Changes are classified as safe (auto-migratable) or breaking (requires migrate()).
    """
    changes: List[SchemaChange] = []

    all_entity_types = set(old_schema.keys()) | set(new_schema.keys())

    for entity_type in sorted(all_entity_types):
        old_entity = old_schema.get(entity_type)
        new_entity = new_schema.get(entity_type)

        if old_entity is None and new_entity is not None:
            changes.append(
                SchemaChange(
                    entity_type=entity_type,
                    field=None,
                    change_type=ChangeType.ENTITY_ADDED,
                    new_value=new_entity,
                    safe=True,
                    reason="New entity type — no existing data to migrate",
                )
            )
            continue

        if old_entity is not None and new_entity is None:
            changes.append(
                SchemaChange(
                    entity_type=entity_type,
                    field=None,
                    change_type=ChangeType.ENTITY_REMOVED,
                    old_value=old_entity,
                    safe=True,
                    reason="Removed entity type — existing data will be orphaned",
                )
            )
            continue

        if old_entity["version"] != new_entity["version"]:
            changes.append(
                SchemaChange(
                    entity_type=entity_type,
                    field=None,
                    change_type=ChangeType.VERSION_CHANGED,
                    old_value=old_entity["version"],
                    new_value=new_entity["version"],
                    safe=True,
                    reason=f"Version {old_entity['version']} → {new_entity['version']}",
                )
            )

        _diff_fields(
            entity_type,
            old_entity.get("fields", {}),
            new_entity.get("fields", {}),
            changes,
        )
        _diff_relationships(
            entity_type,
            old_entity.get("relationships", {}),
            new_entity.get("relationships", {}),
            changes,
        )

    return changes


def _diff_fields(
    entity_type: str,
    old_fields: Dict[str, Any],
    new_fields: Dict[str, Any],
    changes: List[SchemaChange],
) -> None:
    """Compare fields between old and new entity schemas."""
    all_field_names = set(old_fields.keys()) | set(new_fields.keys())

    for field_name in sorted(all_field_names):
        old_field = old_fields.get(field_name)
        new_field = new_fields.get(field_name)

        if old_field is None and new_field is not None:
            has_default = new_field.get("default") is not None or new_field.get(
                "has_default", False
            )
            changes.append(
                SchemaChange(
                    entity_type=entity_type,
                    field=field_name,
                    change_type=ChangeType.ADDED,
                    new_value=new_field,
                    safe=has_default,
                    reason=(
                        f"New field with default={new_field.get('default')!r} — auto-migratable"
                        if has_default
                        else "New field without default — requires migrate() to provide initial values"
                    ),
                )
            )
            continue

        if old_field is not None and new_field is None:
            changes.append(
                SchemaChange(
                    entity_type=entity_type,
                    field=field_name,
                    change_type=ChangeType.REMOVED,
                    old_value=old_field,
                    safe=True,
                    reason="Removed field — old data will be ignored on load",
                )
            )
            continue

        if old_field.get("type") != new_field.get("type"):
            changes.append(
                SchemaChange(
                    entity_type=entity_type,
                    field=field_name,
                    change_type=ChangeType.TYPE_CHANGED,
                    old_value=old_field.get("type"),
                    new_value=new_field.get("type"),
                    safe=False,
                    reason=f"Type changed {old_field.get('type')} → {new_field.get('type')} — requires migrate()",
                )
            )

        old_constraints = old_field.get("constraints", {})
        new_constraints = new_field.get("constraints", {})
        if old_constraints != new_constraints:
            changes.append(
                SchemaChange(
                    entity_type=entity_type,
                    field=field_name,
                    change_type=ChangeType.CONSTRAINTS_CHANGED,
                    old_value=old_constraints,
                    new_value=new_constraints,
                    safe=True,
                    reason="Constraints changed — existing data may need validation",
                )
            )


def _diff_relationships(
    entity_type: str,
    old_rels: Dict[str, Any],
    new_rels: Dict[str, Any],
    changes: List[SchemaChange],
) -> None:
    """Compare relationships between old and new entity schemas."""
    all_rel_names = set(old_rels.keys()) | set(new_rels.keys())

    for rel_name in sorted(all_rel_names):
        old_rel = old_rels.get(rel_name)
        new_rel = new_rels.get(rel_name)

        if old_rel is None and new_rel is not None:
            changes.append(
                SchemaChange(
                    entity_type=entity_type,
                    field=rel_name,
                    change_type=ChangeType.ADDED,
                    new_value=new_rel,
                    safe=True,
                    reason="New relationship — no existing data affected",
                )
            )
            continue

        if old_rel is not None and new_rel is None:
            changes.append(
                SchemaChange(
                    entity_type=entity_type,
                    field=rel_name,
                    change_type=ChangeType.REMOVED,
                    old_value=old_rel,
                    safe=True,
                    reason="Removed relationship — old references will be orphaned",
                )
            )
            continue

        if old_rel.get("type") != new_rel.get("type"):
            changes.append(
                SchemaChange(
                    entity_type=entity_type,
                    field=rel_name,
                    change_type=ChangeType.RELATIONSHIP_CHANGED,
                    old_value=old_rel,
                    new_value=new_rel,
                    safe=False,
                    reason=(
                        f"Relationship type changed {old_rel.get('type')} → {new_rel.get('type')} "
                        f"— requires migrate()"
                    ),
                )
            )
        elif old_rel.get("target") != new_rel.get("target"):
            changes.append(
                SchemaChange(
                    entity_type=entity_type,
                    field=rel_name,
                    change_type=ChangeType.RELATIONSHIP_CHANGED,
                    old_value=old_rel,
                    new_value=new_rel,
                    safe=False,
                    reason=(
                        f"Relationship target changed {old_rel.get('target')} → {new_rel.get('target')} "
                        f"— requires migrate()"
                    ),
                )
            )


def check_upgrade_compatibility(
    db: "Database",
    raise_on_error: bool = True,
) -> List[SchemaChange]:
    """Check that the current Entity definitions are compatible with stored schema.

    Loads the previously stored schema from _system/_schema, builds the current
    schema from registered Entity classes, diffs them, and verifies that every
    breaking change has a corresponding migrate() override.

    After successful validation, saves the new schema.

    Args:
        db: Database instance
        raise_on_error: If True (default), raise on incompatible changes.
                        If False, return the changes list without raising.

    Returns:
        List of SchemaChange objects describing all detected changes.

    Raises:
        SchemaIncompatibleError: If breaking changes are found without migrate().
    """
    old_schema_data = db.load("_system", "_schema")

    current_schema = build_schema(db._entity_types)

    if old_schema_data is None:
        db.save("_system", "_schema", current_schema)
        db.save("_system", "_schema_hash", schema_hash(current_schema))
        return []

    changes = diff_schemas(old_schema_data, current_schema)

    breaking_without_migrate = []
    for change in changes:
        if change.safe:
            continue
        entity_cls = db._entity_types.get(change.entity_type)
        if entity_cls and _has_custom_migrate(entity_cls):
            continue
        breaking_without_migrate.append(change)

    if breaking_without_migrate and raise_on_error:
        details = "\n".join(
            f"  - {c.entity_type}.{c.field}: {c.reason}"
            for c in breaking_without_migrate
        )
        raise SchemaIncompatibleError(
            f"Upgrade rejected: {len(breaking_without_migrate)} breaking change(s) "
            f"without migrate() method:\n{details}"
        )

    db.save("_system", "_schema", current_schema)
    db.save("_system", "_schema_hash", schema_hash(current_schema))

    return changes


class SchemaIncompatibleError(Exception):
    """Raised when an upgrade contains breaking schema changes without a migrate() method."""

    pass
