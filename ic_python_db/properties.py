"""Property definitions for Entity classes.

Relation properties (ManyToOne, OneToMany, OneToOne, ManyToMany) use persisted
reverse indexes in stable storage so that relationships can be resolved without
scanning all entities.
"""

from typing import (
    TYPE_CHECKING,
    Any,
    Callable,
    Generic,
    List,
    Optional,
    Type,
    TypeVar,
    Union,
)

if TYPE_CHECKING:
    from .entity import Entity

T = TypeVar("T")
E = TypeVar("E", bound="Entity")

PROPERTY_STORAGE_PREFIX = "prop"


class Property(Generic[T]):
    """Definition of an entity property.

    A generic descriptor class that provides type-safe property access.
    """

    name: str
    type: Type[T]
    default: Optional[T]
    validator: Optional[Callable[[T], bool]]

    def __init__(
        self,
        name: str = "",
        type: Type[T] = type(None),  # type: ignore[assignment]
        default: Optional[T] = None,
        validator: Optional[Callable[[T], bool]] = None,
    ):
        self.name = name
        self.type = type
        self.default = default
        self.validator = validator

    def __set_name__(self, owner: type, name: str) -> None:
        self.name = name

    def __get__(
        self, obj: object, objtype: Optional[type] = None
    ) -> Union["Property[T]", Optional[T]]:
        if obj is None:
            return self  # type: ignore[return-value]
        return obj.__dict__.get(f"_{PROPERTY_STORAGE_PREFIX}_{self.name}", self.default)

    def __set__(self, obj, value):
        from .constants import ACTION_CREATE, ACTION_MODIFY
        from .hooks import call_entity_hook

        old_value = obj.__dict__.get(
            f"_{PROPERTY_STORAGE_PREFIX}_{self.name}", self.default
        )
        action = (
            ACTION_CREATE
            if not hasattr(obj, "_loaded") or not obj._loaded
            else ACTION_MODIFY
        )

        allow, modified_value = call_entity_hook(
            obj, self.name, old_value, value, action
        )

        if not allow:
            raise ValueError(f"Hook rejected change to {self.name}")

        value = modified_value

        if value is not None:
            if not isinstance(value, self.type):
                if isinstance(value, str) and self.type in (int, float, bool):
                    try:
                        if self.type == bool:
                            value = value.lower() in ("true", "1", "yes", "on")
                        else:
                            value = self.type(value)
                    except (ValueError, TypeError):
                        raise TypeError(
                            f"{self.name} must be of type {self.type.__name__}"
                        )
                else:
                    raise TypeError(f"{self.name} must be of type {self.type.__name__}")

            if self.validator and not self.validator(value):
                raise ValueError(f"Invalid value for {self.name}: {value}")

        obj.__dict__[f"_{PROPERTY_STORAGE_PREFIX}_{self.name}"] = value
        obj._save()


class String(Property[str]):
    """String property with optional length validation."""

    def __init__(
        self,
        min_length: Optional[int] = None,
        max_length: Optional[int] = None,
        default: Optional[str] = None,
    ):
        def validator(value: str) -> bool:
            if min_length is not None and len(value) < min_length:
                return False
            if max_length is not None and len(value) > max_length:
                return False
            return True

        super().__init__(name="", type=str, default=default, validator=validator)


class Integer(Property[int]):
    """Integer property with optional range validation."""

    def __init__(
        self,
        min_value: Optional[int] = None,
        max_value: Optional[int] = None,
        default: Optional[int] = None,
    ):
        def validator(value: int) -> bool:
            if min_value is not None and value < min_value:
                return False
            if max_value is not None and value > max_value:
                return False
            return True

        super().__init__(name="", type=int, default=default, validator=validator)


class Float(Property[float]):
    """Float property with optional range validation."""

    def __init__(
        self,
        min_value: Optional[float] = None,
        max_value: Optional[float] = None,
        default: Optional[float] = None,
    ):
        def validator(value: float) -> bool:
            if min_value is not None and value < min_value:
                return False
            if max_value is not None and value > max_value:
                return False
            return True

        super().__init__(name="", type=float, default=default, validator=validator)


class Boolean(Property[bool]):
    """Boolean property."""

    def __init__(self, default: Optional[bool] = None):
        super().__init__(name="", type=bool, default=default)


# ── Relation Properties ───────────────────────────────────────────────────────


class Relation(Generic[E]):
    """Base class for relation properties.

    Provides shared utilities for resolving and validating related entities.
    Subclasses implement specific relationship semantics using persisted
    reverse indexes.
    """

    name: Optional[str]
    entity_types: Union[str, List[str]]
    reverse_name: Optional[str]

    def __init__(
        self,
        entity_types: Union[str, List[str]],
        reverse_name: Optional[str] = None,
    ):
        self.entity_types = entity_types
        self.name = None
        self.reverse_name = reverse_name

    def __set_name__(self, owner: type, name: str) -> None:
        self.name = name
        if self.reverse_name is None:
            self.reverse_name = name

    @property
    def many(self) -> bool:
        """Whether this relation holds multiple entities."""
        return isinstance(self, (OneToMany, ManyToMany))

    def _get_allowed_types(self) -> List[str]:
        if isinstance(self.entity_types, str):
            return [self.entity_types]
        return list(self.entity_types)

    def validate_entity(self, entity: Any) -> bool:
        """Validate that an entity is of the correct type."""
        from .entity import Entity

        if entity is None:
            return True
        if not isinstance(entity, Entity):
            raise TypeError(f"{self.name} must be set to Entity instances")

        allowed_types = self._get_allowed_types()
        if entity._type not in allowed_types:
            entity_class_name = (
                entity._type.split("::")[-1] if "::" in entity._type else entity._type
            )
            type_matches = any(
                entity_class_name == (t.split("::")[-1] if "::" in t else t)
                for t in allowed_types
            )
            if not type_matches:
                raise TypeError(
                    f"{self.name} must be an Entity of type {self.entity_types}, "
                    f"but got '{entity._type}'"
                )
        return True

    def resolve_entity(self, obj: Any, value: Any) -> Optional[E]:
        """Resolve a value (Entity, ID string, or alias) to an Entity instance."""
        from .entity import Entity

        if value is None:
            return None

        if isinstance(value, Entity):
            return value  # type: ignore[return-value]

        if isinstance(value, (str, int)):
            for entity_type_name in self._get_allowed_types():
                entity_class = obj.db()._entity_types.get(entity_type_name)
                if entity_class:
                    found_entity = entity_class[value]
                    if found_entity:
                        return found_entity
            raise ValueError(
                f"No entity of type {self.entity_types} found with ID or name '{value}'"
            )

        raise TypeError(
            f"{self.name} must be set to an Entity instance, string ID, or string name"
        )


class ManyToOne(Relation[E]):
    """Many-to-one relationship (child stores FK to parent).

    This is the "owning" side. Setting this property:
    - Stores the parent's ID on the child entity
    - Updates the parent's reverse index so it can find this child

    Example:
        class Employee(Entity):
            department = ManyToOne('Department', 'employees')

        class Department(Entity):
            employees = OneToMany('Employee', 'department')
    """

    def __init__(
        self, entity_types: Union[str, List[str]], reverse_name: Optional[str] = None
    ):
        super().__init__(entity_types, reverse_name)

    def __get__(
        self, obj: object, objtype: Optional[type] = None
    ) -> Union["ManyToOne[E]", Optional[E]]:
        if obj is None:
            return self  # type: ignore[return-value]
        parent_ref = obj.__dict__.get(f"_rel_{self.name}")
        if parent_ref is None:
            return None
        for type_name in self._get_allowed_types():
            entity_class = obj.db()._entity_types.get(type_name)
            if entity_class:
                entity = entity_class.load(parent_ref)
                if entity:
                    return entity  # type: ignore[return-value]
        return None

    def __set__(self, obj, value):
        from .db_engine import Database
        from .entity import Entity

        # Fast path: loading from DB — just store the raw reference, indexes are intact
        if getattr(obj, "_do_not_save", False) and getattr(obj, "_loaded", False):
            if value is None:
                obj.__dict__[f"_rel_{self.name}"] = None
            elif isinstance(value, Entity):
                obj.__dict__[f"_rel_{self.name}"] = value._id
            else:
                obj.__dict__[f"_rel_{self.name}"] = str(value)
            return

        if value is not None:
            if isinstance(value, (list, tuple)):
                raise ValueError(
                    f"{self.name} cannot be set to multiple values (many-to-one)"
                )
            value = self.resolve_entity(obj, value)
            self.validate_entity(value)

        db = Database.get_instance()
        old_ref = obj.__dict__.get(f"_rel_{self.name}")

        # Remove from old parent's reverse index
        if old_ref is not None and obj._id is not None:
            for type_name in self._get_allowed_types():
                entity_class = db._entity_types.get(type_name)
                if entity_class and db.load(type_name, old_ref):
                    db.reverse_index_remove(
                        type_name, old_ref, self.reverse_name, obj._id
                    )
                    break

        # Set new reference
        if value is not None:
            obj.__dict__[f"_rel_{self.name}"] = value._id
            if obj._id is not None:
                db.reverse_index_add(value._type, value._id, self.reverse_name, obj._id)
        else:
            obj.__dict__[f"_rel_{self.name}"] = None

        if not obj._do_not_save:
            obj._save()


class OneToMany(Relation[E]):
    """One-to-many relationship (parent side, resolved via reverse index).

    Accessing this property reads the persisted reverse index to find child
    IDs, then loads each child. No scanning required.

    Example:
        class Department(Entity):
            employees = OneToMany('Employee', 'department')

        class Employee(Entity):
            department = ManyToOne('Department', 'employees')
    """

    def __init__(self, entity_types: Union[str, List[str]], reverse_name: str):
        super().__init__(entity_types, reverse_name)

    def __get__(
        self, obj: object, objtype: Optional[type] = None
    ) -> Union["OneToMany[E]", List[E]]:
        if obj is None:
            return self  # type: ignore[return-value]

        from .db_engine import Database

        db = Database.get_instance()
        child_ids = db.reverse_index_get(obj._type, obj._id, self.name)

        children = []
        for child_id in child_ids:
            for type_name in self._get_allowed_types():
                entity_class = db._entity_types.get(type_name)
                if entity_class:
                    child = entity_class.load(child_id)
                    if child:
                        children.append(child)
                        break
        return children  # type: ignore[return-value]

    def __set__(self, obj, values):
        raise AttributeError(
            f"Cannot set OneToMany '{self.name}' directly. "
            f"Set the ManyToOne '{self.reverse_name}' on each child instead."
        )


class OneToOne(Relation[E]):
    """One-to-one relationship.

    The "owning" side stores the FK and updates the reverse index.
    The "inverse" side resolves via the reverse index.

    Example:
        class Person(Entity):
            profile = OneToOne('Profile', 'person')

        class Profile(Entity):
            person = OneToOne('Person', 'profile')
    """

    def __init__(
        self,
        entity_types: Union[str, List[str]],
        reverse_name: Optional[str] = None,
    ):
        super().__init__(entity_types, reverse_name)

    def __get__(
        self, obj: object, objtype: Optional[type] = None
    ) -> Union["OneToOne[E]", Optional[E]]:
        if obj is None:
            return self  # type: ignore[return-value]

        from .db_engine import Database

        # Check if this side stores the FK (owning side)
        local_ref = obj.__dict__.get(f"_rel_{self.name}")
        if local_ref is not None:
            for type_name in self._get_allowed_types():
                entity_class = obj.db()._entity_types.get(type_name)
                if entity_class:
                    entity = entity_class.load(local_ref)
                    if entity:
                        return entity  # type: ignore[return-value]
            return None

        # Inverse side: check reverse index
        db = Database.get_instance()
        child_ids = db.reverse_index_get(obj._type, obj._id, self.name)
        if child_ids:
            for type_name in self._get_allowed_types():
                entity_class = db._entity_types.get(type_name)
                if entity_class:
                    entity = entity_class.load(child_ids[0])
                    if entity:
                        return entity  # type: ignore[return-value]
        return None

    def __set__(self, obj, value):
        from .db_engine import Database
        from .entity import Entity

        # Fast path: loading from DB — just store the raw reference, indexes are intact
        if getattr(obj, "_do_not_save", False) and getattr(obj, "_loaded", False):
            if value is None:
                obj.__dict__[f"_rel_{self.name}"] = None
            elif isinstance(value, Entity):
                obj.__dict__[f"_rel_{self.name}"] = value._id
            else:
                obj.__dict__[f"_rel_{self.name}"] = str(value)
            return

        if value is not None:
            if isinstance(value, (list, tuple)):
                raise ValueError(
                    f"{self.name} cannot be set to multiple values (one-to-one)"
                )
            value = self.resolve_entity(obj, value)
            self.validate_entity(value)

        db = Database.get_instance()
        old_ref = obj.__dict__.get(f"_rel_{self.name}")

        # Remove from old target's reverse index
        if old_ref is not None and obj._id is not None:
            for type_name in self._get_allowed_types():
                entity_class = db._entity_types.get(type_name)
                if entity_class and db.load(type_name, old_ref):
                    db.reverse_index_remove(
                        type_name, old_ref, self.reverse_name, obj._id
                    )
                    break

        # Enforce OneToOne exclusivity: if the target entity already has a direct
        # FK on the reverse relation pointing to a different entity, clear it.
        if value is not None:
            existing_on_target = value.__dict__.get(f"_rel_{self.reverse_name}")
            if existing_on_target is not None and existing_on_target != obj._id:
                # The target already links to someone else via reverse relation.
                # Clear that link and its reverse index entry.
                db.reverse_index_remove(
                    obj._type, existing_on_target, self.name, value._id
                )
                value.__dict__[f"_rel_{self.reverse_name}"] = None
                value._save()

            # Also check if obj was previously linked via reverse index (inverse side)
            # e.g., if someone else set "other.rel = obj" previously
            existing_in_ri = db.reverse_index_get(obj._type, obj._id, self.name)
            for old_other_id in existing_in_ri:
                if old_other_id != value._id:
                    db.reverse_index_remove(obj._type, obj._id, self.name, old_other_id)
                    # Clear the other entity's direct FK
                    for tn in self._get_allowed_types():
                        ec = db._entity_types.get(tn)
                        if ec:
                            other_entity = ec.load(old_other_id)
                            if (
                                other_entity
                                and other_entity.__dict__.get(
                                    f"_rel_{self.reverse_name}"
                                )
                                == obj._id
                            ):
                                other_entity.__dict__[f"_rel_{self.reverse_name}"] = (
                                    None
                                )
                                other_entity._save()
                                break

        # Set new reference
        if value is not None:
            obj.__dict__[f"_rel_{self.name}"] = value._id
            if obj._id is not None:
                db.reverse_index_add(value._type, value._id, self.reverse_name, obj._id)
        else:
            obj.__dict__[f"_rel_{self.name}"] = None
            # Also clear any reverse index entries pointing at obj for this relation
            existing_in_ri = db.reverse_index_get(obj._type, obj._id, self.name)
            for old_other_id in existing_in_ri:
                db.reverse_index_remove(obj._type, obj._id, self.name, old_other_id)

        if not obj._do_not_save:
            obj._save()


class ManyToManyList(list):
    """List subclass returned by ManyToMany.__get__ that supports .add() and .remove()."""

    def __init__(self, items, owner, prop):
        super().__init__(items)
        self._owner = owner
        self._prop = prop

    def add(self, entity):
        """Add an entity to this many-to-many relationship."""
        from .db_engine import Database
        from .entity import Entity

        if isinstance(entity, (str, int)):
            resolved = self._prop.resolve_entity(self._owner, entity)
        elif isinstance(entity, Entity):
            resolved = entity
        else:
            raise TypeError(f"Cannot add {type(entity)} to ManyToMany relation")

        self._prop.validate_entity(resolved)
        db = Database.get_instance()

        existing = db.reverse_index_get(
            self._owner._type, self._owner._id, self._prop.name
        )
        if resolved._id not in existing:
            db.reverse_index_add(
                self._owner._type, self._owner._id, self._prop.name, resolved._id
            )
            db.reverse_index_add(
                resolved._type, resolved._id, self._prop.reverse_name, self._owner._id
            )
            if not self._owner._do_not_save:
                self._owner._save()
        self.append(resolved)

    def discard(self, entity):
        """Remove an entity from this many-to-many relationship (no error if absent)."""
        from .db_engine import Database
        from .entity import Entity

        if isinstance(entity, Entity):
            entity_id = entity._id
            entity_type = entity._type
        else:
            entity_id = str(entity)
            entity_type = None

        db = Database.get_instance()
        db.reverse_index_remove(
            self._owner._type, self._owner._id, self._prop.name, entity_id
        )
        if entity_type:
            db.reverse_index_remove(
                entity_type, entity_id, self._prop.reverse_name, self._owner._id
            )
        else:
            for type_name in self._prop._get_allowed_types():
                db.reverse_index_remove(
                    type_name, entity_id, self._prop.reverse_name, self._owner._id
                )

        self[:] = [e for e in self if getattr(e, "_id", None) != entity_id]
        if not self._owner._do_not_save:
            self._owner._save()

    def remove(self, entity):
        """Remove an entity from this many-to-many relationship."""
        self.discard(entity)


class ManyToMany(Relation[E]):
    """Many-to-many relationship with bidirectional reverse indexes.

    Both sides maintain a reverse index. Setting on either side updates both.

    Example:
        class Student(Entity):
            courses = ManyToMany('Course', 'students')

        class Course(Entity):
            students = ManyToMany('Student', 'courses')
    """

    def __init__(self, entity_types: Union[str, List[str]], reverse_name: str):
        super().__init__(entity_types, reverse_name)

    def __get__(
        self, obj: object, objtype: Optional[type] = None
    ) -> Union["ManyToMany[E]", List[E]]:
        if obj is None:
            return self  # type: ignore[return-value]

        from .db_engine import Database

        db = Database.get_instance()
        related_ids = db.reverse_index_get(obj._type, obj._id, self.name)

        entities = []
        for related_id in related_ids:
            for type_name in self._get_allowed_types():
                entity_class = db._entity_types.get(type_name)
                if entity_class:
                    entity = entity_class.load(related_id)
                    if entity:
                        entities.append(entity)
                        break
        return ManyToManyList(entities, obj, self)  # type: ignore[return-value]

    def __set__(self, obj, values):
        from .db_engine import Database
        from .entity import Entity

        if values is None:
            values = []
        if isinstance(values, Entity):
            values = [values]
        elif isinstance(values, (str, int)):
            values = [values]
        elif not isinstance(values, (list, tuple)):
            raise TypeError(f"{self.name} must be set to an entity or list of entities")

        resolved = []
        for v in values:
            entity = self.resolve_entity(obj, v)
            self.validate_entity(entity)
            resolved.append(entity)

        db = Database.get_instance()

        # Remove old relations from both sides' indexes
        old_ids = db.reverse_index_get(obj._type, obj._id, self.name)
        for old_id in old_ids:
            db.reverse_index_remove(obj._type, obj._id, self.name, old_id)
            for type_name in self._get_allowed_types():
                entity_class = db._entity_types.get(type_name)
                if entity_class and db.load(type_name, old_id):
                    db.reverse_index_remove(
                        type_name, old_id, self.reverse_name, obj._id
                    )
                    break

        # Add new relations to both sides' indexes
        for entity in resolved:
            db.reverse_index_add(obj._type, obj._id, self.name, entity._id)
            db.reverse_index_add(entity._type, entity._id, self.reverse_name, obj._id)

        if not obj._do_not_save:
            obj._save()
