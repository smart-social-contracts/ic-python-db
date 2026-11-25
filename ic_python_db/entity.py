"""Enhanced entity implementation with support for mixins and entity types."""

import json
from typing import Any, Callable, Dict, List, Optional, Set, Tuple, Type, TypeVar

from ic_python_logging import get_logger

from .constants import LEVEL_MAX_DEFAULT
from .context import get_caller_id
from .db_engine import Database
from .errors import MigrationError, StaleEntityError

logger = get_logger(__name__)


T = TypeVar("T", bound="Entity")

_I64_MIN, _I64_LIMIT = -(1 << 63), 1 << 63
_INF = float("inf")


def _float_bits(value: float) -> int:
    """The IEEE 754 bit pattern of abs(value), read from float.hex().

    The canister's ``struct`` module is a stub without pack/unpack.
    """
    mantissa, exponent = float.hex(abs(value))[2:].split("p")
    lead, fraction = mantissa.split(".")
    fraction_bits = int(fraction.ljust(13, "0"), 16)
    if lead == "0":
        return fraction_bits  # zero or subnormal: biased exponent 0
    return ((int(exponent) + 1023) << 52) | fraction_bits


def _sort_encode(value: Any) -> str:
    """A value as 20 decimal digits whose string order is the value's order.

    Integers are offset into 0..2**64-1; finite floats use their IEEE 754
    bits around 2**63, so that negative numbers come first.
    """
    if isinstance(value, bool):
        value = int(value)
    if isinstance(value, int):
        if not _I64_MIN <= value < _I64_LIMIT:
            raise ValueError(f"sorted Integer values must fit in 64 bits: {value}")
        unsigned = value - _I64_MIN
    elif isinstance(value, float):
        if value != value or value in (_INF, -_INF):
            raise ValueError(f"sorted Float values must be finite: {value}")
        bits = _float_bits(value)
        unsigned = (1 << 63) - bits if value < 0 else (1 << 63) + bits
    else:
        raise TypeError(f"sorted fields hold numbers, got {type(value).__name__}")
    return str(unsigned).zfill(20)


class Entity:
    """Base class for database entities with enhanced features.

    This is the core class for all database entities. It provides automatic ID generation,
    property storage, relationship management, and alias-based lookups.

    Usage Examples:
        # Basic entity with auto-generated ID
        class User(Entity):
            name = String(min_length=2, max_length=50)
            age = Integer(min_value=0)

        user = User(name="John", age=30)  # Creates entity with _id="1"

        # Entity with alias for lookups
        class Person(Entity):
            __alias__ = "name"  # Enables Person["John"] lookup
            name = String()

        person = Person(name="Jane")
        found = Person["Jane"]  # Lookup by alias

        # Entity with versioning and migration
        class Product(Entity):
            __version__ = 2  # Current schema version
            name = String()
            price = Float()

            @classmethod
            def migrate(cls, obj, from_version, to_version):
                if from_version == 1 and to_version >= 2:
                    if 'price' not in obj:
                        obj['price'] = 0.0
                return obj

        # Entity with namespace
        class AppUser(Entity):
            __namespace__ = "app"  # Stores as "app::AppUser"
            name = String()

        class AdminUser(Entity):
            __namespace__ = "admin"  # Stores as "admin::AdminUser"
            name = String()

    Key Features:
        - Auto-generated sequential IDs (_id: "1", "2", "3", ...)
        - Property validation and type checking via descriptors
        - Relationship management (OneToOne, OneToMany, ManyToMany)
        - Alias-based entity lookup (Entity["alias_value"])
        - Automatic persistence to database on creation/update
        - Entity counting and pagination support
        - Schema versioning and automatic migration on load

    Internally managed attributes:
        _type (str): Entity class name (e.g., "User", "Person")
        _id (str): Unique sequential identifier ("1", "2", "3", ...)
        _loaded (bool): True if loaded from DB, False if newly created
        _counted (bool): True if entity has been counted (prevents double-counting)
        _do_not_save (bool): Temporary flag to prevent saving during initialization

    Class-level attributes:
        __alias__ (str): Optional field name for alias-based lookups
        __version__ (int): Schema version for migration support (default: 1)
        __namespace__ (str): Optional namespace for entity type (e.g., "app", "admin")
                           Entities with namespaces are stored as "namespace::ClassName"
                           Allows multiple entities with same class name in different namespaces
        _entity_type (str): Optional entity type for subclasses
        _context (Set[Entity]): Unused, kept empty for compatibility (issue #23)

    Property Storage:
        User-defined properties (String, Integer, etc.) are stored as _prop_{name}
        in the entity's __dict__ to avoid conflicts with internal attributes.
        Example: name = String() stores value in _prop_name
    """

    _entity_type = None  # To be defined in subclasses
    # Used to hold every entity ever constructed, which kept them all in the
    # heap until the next upgrade (issue #23). Kept, empty, for code that
    # clears it.
    _context: Set["Entity"] = set()
    _deferred_types: List[Type["Entity"]] = []  # Types defined before DB exists
    _do_not_save = False
    _migrating = False  # True while a save persists a migration made on load
    # The version of a row loaded from newer code than this (after a rollback);
    # such an entity is never saved (see _hydrate)
    _newer_version: Optional[int] = None
    # The row as this instance last read or wrote it (see _refuse_if_stale)
    _synced: Optional[dict] = None
    __version__ = 1  # Default schema version
    __namespace__: Optional[str] = None  # Optional namespace for entity type

    def __init_subclass__(cls, **kwargs):
        """Auto-register Entity subclasses with the Database at class definition time."""
        super().__init_subclass__(**kwargs)
        db = Database._instance
        if db is not None:
            db.register_entity_type(cls, cls.get_full_type_name())
        else:
            # Database not initialized yet — defer registration
            Entity._deferred_types.append(cls)

    @classmethod
    def _flush_deferred_types(cls):
        """Register any Entity subclasses that were defined before Database existed."""
        if not cls._deferred_types:
            return
        try:
            db = Database.get_instance()
        except Exception:
            return
        for deferred_cls in cls._deferred_types:
            try:
                db.register_entity_type(deferred_cls, deferred_cls.get_full_type_name())
            except Exception:
                pass
        cls._deferred_types.clear()

    def __init__(self, **kwargs):
        """Initialize a new entity.

        Creates a new entity instance with auto-generated ID and sets up all internal
        attributes. User-provided properties are validated and stored using the
        _prop_{name} pattern to avoid conflicts with internal attributes.

        The entity is automatically:
        - Assigned a sequential ID (_id)
        - Registered with the database
        - Added to the class context
        - Persisted to storage via _save()

        Args:
            **kwargs: User-defined attributes to set on the entity.
                     These correspond to properties defined on the class
                     (e.g., name="John" for a String() property named 'name').

                     Special internal kwargs (used internally by the system):
                     - _id: Custom ID string (bypasses auto-generation)

        Example:
            class User(Entity):
                name = String(min_length=2)
                age = Integer(min_value=0)

            # Creates new entity with _id="1", validates and stores properties
            user = User(name="Alice", age=25)
        """
        # Initialize any mixins
        super().__init__() if hasattr(super(), "__init__") else None

        # Store the type for this entity - use namespace::class_name if namespace is set
        self._type = self.__class__.get_full_type_name()
        # Get next sequential ID from storage
        self._id = None if kwargs.get("_id") is None else kwargs["_id"]
        self._loaded = False if kwargs.get("_loaded") is None else kwargs["_loaded"]
        self._counted = False  # Track if this entity has been counted

        # Register this type with the database (using full type name)
        self.db().register_entity_type(self.__class__, self._type)

        # The id counter as it was before this entity moved it, to give the id
        # back if the constructor fails (None: it did not move it)
        counter_before = None
        # Generate ID if not provided, or update max_id if custom ID is higher
        if self._id is None:
            db = self.db()
            type_name = self._type
            current_id = db.load("_system", f"{type_name}_id")
            if current_id is None:
                current_id = "0"
            next_id = str(int(current_id) + 1)
            self._id = next_id
            db.save("_system", f"{type_name}_id", self._id)
            counter_before = current_id
        elif not self._loaded:
            # Update max_id if custom ID is higher than current max.
            # Skipped when hydrating persisted state: that id already went
            # through this check when it was first created, so re-reading the
            # counter would only add one storage read per loaded entity.
            db = self.db()
            type_name = self._type
            # Refuse a taken id now: each argument set below writes its index
            # entries, and _save() only finds the row once they are written.
            if db.load(type_name, self._id) is not None:
                raise ValueError(f"Entity {self._type}@{self._id} already exists")
            current_max_id = db.load("_system", f"{type_name}_id")
            if current_max_id is None:
                current_max_id = "0"

            # Only update if the custom ID is numeric and higher than current max
            try:
                custom_id_int = int(self._id)
                current_max_int = int(current_max_id)
                if custom_id_int > current_max_int:
                    db.save("_system", f"{type_name}_id", self._id)
                    counter_before = current_max_id
            except ValueError:
                # If custom ID is not numeric, don't update max_id counter
                pass

        # Register this instance in the entity registry
        self.db().register_entity(self)

        self._do_not_save = True
        # True only while reconstructing persisted state in this constructor:
        # property descriptors skip hooks, validation, and index maintenance
        # (indexes are already intact). Explicit updates later — including
        # deserialize() upserts — run the full descriptor path.
        self._hydrating = self._loaded
        try:
            if self._loaded:
                for k, v in kwargs.items():
                    if not k.startswith("_"):
                        setattr(self, k, v)
                if hasattr(self, "_restore_state"):
                    self._restore_state()
            else:
                self._apply_arguments(kwargs)
            self._do_not_save = False
            self._hydrating = False

            if not self._loaded:
                # A new entity is filed in its indexes only now, with every
                # argument applied and the defaults of the others: an argument
                # refused after an indexed one leaves no entry behind.
                self._save()
                self._index_new()
        except Exception:
            # It was registered above, before its fields. In a canister the
            # identity map keeps it until the message ends, so the next load of
            # this id would return the half-built entity.
            db = self.db()
            if db.get_entity(self._type, self._id) is self:
                db.unregister_entity(self._type, self._id)
            if not self._loaded:
                self._undo_unsaved(counter_before)
            raise

    def _apply_arguments(self, kwargs: dict) -> None:
        """Set a new entity's arguments: its fields, then its relations, a
        OneToOne last; each group in the order given.

        Every refusal that can come from a field (its type, its validator, its
        hook, an alias another row holds) then comes before any relation has
        written anything; the links relations make are taken back if the
        constructor fails (see _undo_unsaved). A OneToOne comes last because
        linking it can clear a link its target had and save the target: that
        write to another row is not taken back. Only another OneToOne argument,
        or the save itself, can fail after it.
        """
        from .properties import OneToOne, Relation

        fields, relations, one_to_one = [], [], []
        for k, v in kwargs.items():
            if k.startswith("_"):
                continue
            attr = getattr(type(self), k, None)
            if isinstance(attr, OneToOne):
                one_to_one.append((k, v))
            elif isinstance(attr, Relation):
                relations.append((k, v))
            else:
                fields.append((k, v))
        for k, v in fields:
            setattr(self, k, v)
        alias = getattr(type(self), "__alias__", None)
        if alias:
            self._refuse_taken_alias(getattr(self, alias, None))
        for k, v in relations + one_to_one:
            setattr(self, k, v)

    def _index_new(self) -> None:
        """File a new entity in its field and sorted indexes (see __init__)."""
        db = self.db()
        for name in self._indexed_properties():
            value = getattr(self, name, None)
            if value is not None:
                db.field_index_add(self._type, name, str(value), self._id)
        self._sync_sorted()

    def _undo_unsaved(self, counter_before: Optional[str]) -> None:
        """Take back what a constructor that failed before the row was saved
        wrote: the links its relation arguments made, and the id it took.

        The id goes back only if no later entity has taken one since.
        """
        self._unlink_relations()
        db = self.db()
        if self._counted:  # the save counted the row, then failed
            count_key = f"{self._type}_count"
            db.save("_system", count_key, str(int(db.load("_system", count_key)) - 1))
        if counter_before is not None:
            counter_key = f"{self._type}_id"
            if db.load("_system", counter_key) == self._id:
                db.save("_system", counter_key, counter_before)

    def _refuse_taken_alias(self, value: Any) -> None:
        """Raise ValueError if another row holds ``value`` in the alias field.

        The alias entry names one row: a second row with the same value took it
        over, and deleting that row left the first one unfound by its alias.
        An entry whose row is gone, or no longer holds the value, is stale, and
        is taken over.
        """
        if value is None:
            return
        field = type(self).__alias__
        db = self.db()
        holder = db.load(self._alias_key(), str(value))
        if holder is None or holder == self._id:
            return
        row = db.load(self._type, holder)
        if row is not None and field in row and str(row[field]) == str(value):
            raise ValueError(
                f"{self._type}: {field} {value!r} is taken by {self._type}@{holder}"
            )

    @classmethod
    def new(cls, **kwargs):
        return cls(**kwargs)

    @classmethod
    def db(cls) -> Database:
        """Get the database instance.

        Returns:
            Database: The database instance
        """
        return Database.get_instance()

    @classmethod
    def get_full_type_name(cls) -> str:
        """Get the full type name including namespace.

        Returns:
            str: Full type name in format 'namespace::ClassName' or 'ClassName'
        """
        namespace = getattr(cls, "__namespace__", None)
        if namespace:
            return f"{namespace}::{cls.__name__}"
        return cls.__name__

    def _check_owner(self) -> None:
        """Raise PermissionError if the caller does not own this entity.

        Only a TimestampedMixin entity has an owner. One with none yet is being
        created, or was stored before its class had the mixin, and the save
        that follows claims it. Neither a caller id nor set_owner can be None,
        so a claimed entity never goes back to having none.
        """
        if hasattr(self, "check_ownership") and self._owner is not None:
            if not self.check_ownership(get_caller_id()):
                raise PermissionError(
                    f"Only the owner can update this entity. Current owner: {self._owner}"
                )

    def _check_write(self) -> None:
        """Raise before a write to this entity changes anything.

        The save that ends a write refuses it too, but a field's index entries,
        its sorted entry and a relation's reverse index are written before that
        save, and its hook has run: refused only there, a write the caller may
        not make had already moved them.
        """
        self._refuse_if_newer()
        self._refuse_if_stale()
        self._check_owner()

    def _refuse_if_stale(self) -> None:
        """Raise StaleEntityError if another message changed this row after
        this instance read or wrote it.

        The identity map is emptied when a message starts, so an instance some
        code holds across one (an endpoint that awaits another canister) is no
        longer the one a load returns. A save writes every field it holds, and
        would put back what the other message wrote, or bring back a row it
        deleted. Only such an instance is checked, at one read per write.
        """
        if not self._loaded:
            return
        db = self.db()
        current = db.get_entity(self._type, self._id)
        if current is self:
            return
        stored = db.load(self._type, self._id)
        if json.dumps(stored, sort_keys=True) != json.dumps(
            self._synced, sort_keys=True
        ):
            raise StaleEntityError(
                f"{self._type}@{self._id} was changed by another message since "
                f"this instance read it: load it again"
            )
        # Unchanged: this is the current instance again. Another one loaded in
        # this message is then the stale one, and its next write is checked.
        db.register_entity(self)

    def _refuse_if_newer(self) -> None:
        """Raise MigrationError if this row was stored by newer code."""
        if self._newer_version is not None:
            raise MigrationError(
                f"{self._type}@{self._id} is stored at version "
                f"{self._newer_version}, newer than this code's "
                f"{self.__class__.__version__}: refusing to overwrite it"
            )

    def _save(
        self,
    ) -> "Entity":
        """Save the entity to the database.

        Returns:
            Entity: self for chaining

        Raises:
            PermissionError: If TimestampedMixin is used and caller is not the owner
            MigrationError: If the row was stored by newer code (see _hydrate)
        """
        self._refuse_if_newer()
        if not self._migrating:
            self._refuse_if_stale()

        # Use full type name (including namespace) for system counters and storage
        type_name = self._type
        db = self.__class__.db()

        if self._id is None:
            # Increment the ID when a new entity is created (never reuse or decrement)
            self._id = str(int(db.load("_system", f"{type_name}_id") or 0) + 1)
            db.save("_system", f"{type_name}_id", self._id)
            # Increment the count when a new entity is created and decrement when deleted
            if not self._counted:
                count_key = f"{type_name}_count"
                current_count = int(db.load("_system", count_key) or 0)
                db.save("_system", count_key, str(current_count + 1))
                self._counted = True
        else:
            if not self._loaded:
                if db.load(type_name, self._id) is not None:
                    raise ValueError(f"Entity {self._type}@{self._id} already exists")
                else:
                    # Increment count for new entities with custom IDs
                    if not self._counted:
                        count_key = f"{type_name}_count"
                        current_count = int(db.load("_system", count_key) or 0)
                        db.save("_system", count_key, str(current_count + 1))
                        self._counted = True

        logger.debug(f"Saving entity {self._type}@{self._id}")

        # Update timestamps if mixin is present
        if not self._migrating and hasattr(self, "_update_timestamps"):
            self._check_owner()
            self._update_timestamps(get_caller_id())

        # Save to database (full serialization preserves all relations)
        data = self._serialize_full()

        if not self._do_not_save:
            logger.debug(f"Saving entity {self._type}@{self._id} to database")
            db = self.db()
            db.save(self._type, self._id, data)
            self._synced = data
            if hasattr(self.__class__, "__alias__") and self.__class__.__alias__:
                alias_field = self.__class__.__alias__
                if hasattr(self, alias_field):
                    alias_value = getattr(self, alias_field)
                    if alias_value is not None:
                        db.save(self.__class__._alias_key(), alias_value, self._id)
            self._loaded = True

        return self

    @classmethod
    def _alias_key(cls: Type[T], field_name: str = None) -> str:
        """Get the alias key for this entity type and field, including namespace if set.

        Args:
            field_name: Optional field name for the alias key.
                - If provided, it is used in the key.
                - If not provided and cls.__alias__ is defined, cls.__alias__ is used.
                - If neither is provided, the key is generated without a field name.

        Returns:
            Alias key string in one of the following formats:
                - "{type_name}_{field_name}_alias" (if field_name or cls.__alias__ is used)
                - "{type_name}_alias" (if neither is provided)
        """
        if field_name is not None and not isinstance(field_name, str):
            raise TypeError(
                f"field_name must be a string, got {type(field_name).__name__}"
            )
        if field_name is None:
            field_name = getattr(cls, "__alias__", None)
        if field_name is None:
            return cls.get_full_type_name() + "_alias"
        return f"{cls.get_full_type_name()}_{field_name}_alias"

    @classmethod
    def _auto_migrate_defaults(cls, data: dict) -> dict:
        """Inject default values for new Property fields not present in stored data.

        Called before migrate() during version-mismatch loads so that developers
        don't need to write migrate() for the trivial case of adding a field
        with a default value.
        """
        from .properties import Property

        for klass in reversed(cls.__mro__):
            for attr_name, attr_value in klass.__dict__.items():
                if attr_name.startswith("_"):
                    continue
                if isinstance(attr_value, Property) and attr_name not in data:
                    if attr_value.default is not None:
                        data[attr_name] = attr_value.default
        return data

    @classmethod
    def migrate(cls, obj: dict, from_version: int, to_version: int) -> dict:
        """Migrate entity data from one version to another.

        This is the default implementation that does nothing. Subclasses should
        override this method to implement custom migration logic.

        Args:
            obj: Dictionary containing the entity data to migrate
            from_version: The version of the data being migrated from
            to_version: The target version to migrate to

        Returns:
            Dictionary containing the migrated entity data

        Example:
            @classmethod
            def migrate(cls, obj, from_version, to_version):
                if from_version == 1 and to_version >= 2:
                    if 'price' not in obj:
                        obj['price'] = 0.0
                if from_version <= 2 and to_version >= 3:
                    if 'old_name' in obj:
                        obj['new_name'] = obj.pop('old_name')
                return obj
        """
        return obj

    @classmethod
    def _migrate_row(cls, label: str, data: dict, stored_version: int) -> dict:
        """A copy of ``data`` migrated from ``stored_version`` to this class's
        version: ``migrate()``, then the defaults of fields it did not set.

        Shared by ``_hydrate`` (a stored row) and ``deserialize`` (a record);
        ``label`` names the row in errors (``Type@id``). ``data`` is left as
        it was, so the caller can compare the two.

        Raises:
            MigrationError: If ``migrate()`` raises.
        """
        current_version = cls.__version__
        logger.debug(f"Migrating {label} from version {stored_version}")
        try:
            # Developer logic first; defaults only fill what it left out
            migrated = cls.migrate(dict(data), stored_version, current_version)
        except Exception as e:
            raise MigrationError(
                f"{label}: migrate() from version {stored_version} to "
                f"{current_version} raised {type(e).__name__}: {e}"
            ) from e
        cls._check_migrated_values(label, data, migrated, stored_version)
        migrated = cls._auto_migrate_defaults(migrated)
        migrated["__version__"] = current_version
        return migrated

    @classmethod
    def _check_migrated_values(
        cls, label: str, data: dict, migrated: dict, stored_version: int
    ) -> None:
        """Check each property value ``migrate()`` set or changed the way a
        write checks it, converting it as a write would. A row is built from
        storage without checks (it was checked when written), so without this
        a value ``migrate()`` got wrong was stored as it was.

        Raises:
            MigrationError: If a property refuses such a value.
        """
        for name in cls._property_names():
            if name not in migrated or (name in data and migrated[name] == data[name]):
                continue
            try:
                migrated[name] = getattr(cls, name)._checked(migrated[name])
            except (TypeError, ValueError) as e:
                raise MigrationError(
                    f"{label}: migrate() from version {stored_version} to "
                    f"{cls.__version__} set {name} to {migrated[name]!r}, which "
                    f"the field refuses: {e}"
                ) from e

    @classmethod
    def load(
        cls: Type[T], entity_id: str = None, level: int = LEVEL_MAX_DEFAULT
    ) -> Optional[T]:
        """Load an entity from the database.

        Args:
            id: ID of entity to load

        Returns:
            Entity if found, None otherwise
        """
        logger.debug(f"Loading entity {entity_id} (level={level})")
        if level == 0:
            return None

        if not entity_id:
            return None

        # Use full type name (including namespace if set)
        type_name = cls.get_full_type_name()

        # Check entity registry first
        db = cls.db()
        existing_entity = db.get_entity(type_name, entity_id)
        if existing_entity is not None:
            logger.debug(f"Found entity {type_name}@{entity_id} in registry")
            return existing_entity

        logger.debug(f"Loading entity {type_name}@{entity_id} from database")
        data = db.load(type_name, entity_id)
        if not data:
            return None

        return cls._hydrate(entity_id, data)

    @classmethod
    def _hydrate(cls: Type[T], entity_id: str, data: dict) -> T:
        """Build an instance from its persisted dict, migrating if needed.

        Shared by ``load`` (single ``get``) and ``load_some`` (range page).
        Callers must have checked the identity registry first.

        Raises:
            MigrationError: If the row's ``migrate()`` raises.
        """
        type_name = cls.get_full_type_name()
        stored_version = data.get("__version__", 1)
        current_version = cls.__version__

        if stored_version > current_version:
            # Written by newer code: this code was rolled back. Read the row as
            # stored and never write it, since a save would stamp it with this
            # older version and the newer code would then migrate it again.
            entity = cls(**data, _loaded=True)
            entity._newer_version = stored_version
            entity._synced = data
            return entity

        if stored_version != current_version:
            stored = data
            data = cls._migrate_row(f"{type_name}@{entity_id}", data, stored_version)

        # Create instance first
        entity = cls(**data, _loaded=True)
        entity._synced = data  # replaced by the save below, if it migrates

        # If migration was applied, persist the migrated data and move its
        # index entries along with any value migrate() changed
        if stored_version != current_version:
            # The caller asked to read, not to write: the save that persists the
            # migration neither checks the owner nor sets updater and timestamps
            entity._migrating = True
            try:
                entity._save()
            finally:
                entity._migrating = False
            entity._reindex_after_migration(stored)

        return entity

    def _reindex_after_migration(self, stored: dict) -> None:
        db = self.db()
        for field in self._indexed_properties():
            old, new = stored.get(field), getattr(self, field, None)
            if old != new:
                if old is not None:
                    db.field_index_remove(self._type, field, str(old), self._id)
                if new is not None:
                    db.field_index_add(self._type, field, str(new), self._id)
        for name in self._foreign_key_names():
            old, new = stored.get(name), self.__dict__.get(f"_rel_{name}")
            old = None if old is None else str(old)
            if old != new:
                self._move_reverse_entry(name, old, new)
        alias = getattr(type(self), "__alias__", None)
        if alias:
            old = stored.get(alias)
            if old is not None and old != getattr(self, alias, None):
                self._drop_alias_entry(alias, old)  # the save filed the new value
        self._sync_sorted()

    def _drop_alias_entry(self, field: str, value: Any) -> None:
        """Remove the alias entry of ``value`` under ``field``, if it names this row."""
        db = self.db()
        key = self._alias_key(field)
        if db.load(key, str(value)) == self._id:
            db.delete(key, str(value))

    def _move_reverse_entry(
        self, name: str, old: Optional[str], new: Optional[str]
    ) -> None:
        """Move this row's entry in the reverse index of the row that its
        reference ``name`` names, from ``old`` to ``new``.

        A reference migrate() set (renamed, added or pointed elsewhere) is
        stored by the save, but the other side of the relation reads the
        reverse index, which only a write through the relation updates."""
        relation = getattr(type(self), name)
        db = self.db()
        for ref, move in ((old, db.reverse_index_remove), (new, db.reverse_index_add)):
            if ref is None:
                continue
            for type_name in relation._get_allowed_types():
                if db._entity_types.get(type_name) and db.load(type_name, ref):
                    move(type_name, ref, relation.reverse_name, self._id)
                    break

    @classmethod
    def _require_unpaged(cls, method: str) -> None:
        """Raise TooLargeError before ``method`` loads more than MAX_UNPAGED entities."""
        from . import constants
        from .errors import TooLargeError

        total = cls.count()
        if total > constants.MAX_UNPAGED:
            raise TooLargeError(
                f"{cls.__name__}.{method}() would load all {total} entities, more "
                f"than {constants.MAX_UNPAGED} in one call; use {cls.__name__}.count(), "
                f"{cls.__name__}.find_by() on an indexed field, or "
                f"{cls.__name__}.page(from_id, count) instead"
            )

    @classmethod
    def find(cls: Type[T], d) -> List[T]:
        """Find entities matching a dictionary filter.

        .. deprecated::
            Loads every entity of the type to filter them, and raises
            TooLargeError past constants.MAX_UNPAGED. Use find_by() on an
            indexed field, or page() (issue #22).
        """
        try:
            import warnings

            warnings.warn(
                "Entity.find() is deprecated and will be removed. "
                "Use find_by() on an indexed field, or page().",
                DeprecationWarning,
                stacklevel=2,
            )
        except (ImportError, AttributeError):
            pass
        cls._require_unpaged("find")
        max_id = cls.max_id()
        if max_id == 0:
            return []
        all_entities = cls.load_some(1, max_id)
        return [
            e
            for e in all_entities
            if all(getattr(e, k, None) == v for k, v in d.items())
        ]

    # The three lookups below walk every class in the hierarchy, so each is
    # computed once per class, on first use, and kept in the class's own
    # __dict__ (a subclass computes its own). Like _sorted_indexes, they read
    # the class as it is when first used.
    @classmethod
    def _indexed_properties(cls) -> Dict[str, Any]:
        """Return {name: Property} for all properties declared with indexed=True."""
        cached = cls.__dict__.get("_indexed_cache")
        if cached is not None:
            return cached
        from .properties import Property

        result: Dict[str, Any] = {}
        for klass in reversed(cls.__mro__):
            for attr_name, attr_value in klass.__dict__.items():
                if attr_name.startswith("_"):
                    continue
                if isinstance(attr_value, Property) and attr_value.indexed:
                    result[attr_name] = attr_value
        cls._indexed_cache = result
        return result

    @classmethod
    def _property_names(cls) -> Tuple[str, ...]:
        """Names of all Property fields, base classes first."""
        cached = cls.__dict__.get("_property_names_cache")
        if cached is not None:
            return cached
        from .properties import Property

        names: List[str] = []
        for klass in reversed(cls.__mro__):
            for attr_name, attr_value in klass.__dict__.items():
                if attr_name.startswith("_") or attr_name in names:
                    continue
                if isinstance(attr_value, Property):
                    names.append(attr_name)
        cls._property_names_cache = tuple(names)
        return cls._property_names_cache

    @classmethod
    def _foreign_key_names(cls) -> Tuple[str, ...]:
        """Names of the ManyToOne and OneToOne fields, which store a reference."""
        cached = cls.__dict__.get("_foreign_key_names_cache")
        if cached is not None:
            return cached
        from .properties import ManyToOne, OneToOne

        names: List[str] = []
        for klass in reversed(cls.__mro__):
            for attr_name, attr_value in klass.__dict__.items():
                if attr_name.startswith("_") or attr_name in names:
                    continue
                if isinstance(attr_value, (ManyToOne, OneToOne)):
                    names.append(attr_name)
        cls._foreign_key_names_cache = tuple(names)
        return cls._foreign_key_names_cache

    @classmethod
    def _require_indexed_field(cls, field: str) -> None:
        if field not in cls._indexed_properties():
            raise ValueError(
                f"{cls.__name__}.{field} is not an indexed property "
                f"(declare it with indexed=True)"
            )

    @classmethod
    def find_by(
        cls: Type[T],
        field: str,
        value: Any,
        from_id: int = 1,
        count: int = 50,
    ) -> Tuple[List[T], Optional[int]]:
        """Equality lookup on an indexed field with cursor pagination (issue #11).

        IDs come from the persistent field index with one range read per
        page, so cost is proportional to ``count``, not to the number of
        matches or to max_id. Results are ordered by ascending entity ID,
        compatible with load_some-style cursors. Entities with custom
        (non-sequential) ids are not returned.

        Args:
            field: Name of a property declared with indexed=True
            value: Value to match (compared via its string form, like the index)
            from_id: Only return entities with _id >= from_id
            count: Maximum number of entities to return

        Returns:
            (entities, next_from_id) — next_from_id is None when no more
            matches remain, otherwise pass it back as from_id for the next page.

        Raises:
            ValueError: If the field is not declared with indexed=True
        """
        cls._require_indexed_field(field)
        if from_id < 1:
            raise ValueError("from_id must be at least 1")
        if count < 1:
            raise ValueError("count must be at least 1")

        db = cls.db()
        type_name = cls.get_full_type_name()
        entities: List[T] = []
        cursor = from_id
        while len(entities) < count:
            need = count - len(entities)
            # One extra id tells whether another page exists
            ids = db.field_index_page(type_name, field, str(value), cursor, need + 1)
            for entity_id in ids[:need]:
                entity = cls.load(entity_id)
                if entity is not None:
                    entities.append(entity)
            if len(ids) <= need:
                return entities, None
            cursor = int(ids[need])
        return entities, cursor

    @classmethod
    def count_by(cls: Type[T], field: str, value: Any) -> int:
        """Number of entities whose indexed ``field`` equals ``value`` (one read).

        Raises:
            ValueError: If the field is not declared with indexed=True
        """
        cls._require_indexed_field(field)
        return cls.db().field_index_count(cls.get_full_type_name(), field, str(value))

    @classmethod
    def rebuild_field_index(
        cls: Type[T],
        field: str,
        from_id: int = 1,
        batch: int = 200,
    ) -> Optional[int]:
        """Index one batch of pre-existing entities; resumable (issue #11).

        Entities created before a field was declared indexed=True are missing
        from its index. This walks the ID range in bounded batches so callers
        on the IC can spread the work across multiple update calls or timer
        ticks without hitting per-message instruction limits:

            cursor = 1
            while cursor is not None:
                cursor = Proposal.rebuild_field_index("org_scope", from_id=cursor)

        Idempotent — re-indexing an already-indexed entity is a no-op. An
        entity whose value holds ``|`` and was indexed under the key such a
        value had before it was escaped is moved to its present key.

        Returns:
            The from_id for the next batch, or None when the rebuild is done.

        Raises:
            ValueError: If the field is not declared with indexed=True
        """
        cls._require_indexed_field(field)
        return cls._rebuild_field_indexes([field], from_id, batch)

    @classmethod
    def rebuild_field_indexes(
        cls: Type[T],
        from_id: int = 1,
        batch: int = 200,
    ) -> Optional[int]:
        """``rebuild_field_index`` for every indexed field of the type at once.

        Moves the entries of values holding ``|`` that an earlier version filed
        under their own text, for entities with sequential ids: until they are
        moved they are still found, under the old key. It adds an entry that is
        missing but never removes one, and does not reach custom ids:
        ``Database.repair_field_indexes`` does both, and is what completes the
        move after an upgrade.
        Same cursor and batch as ``rebuild_field_index``; one batch costs the
        entities it loads once, and a write per indexed field of each.
        """
        return cls._rebuild_field_indexes(
            list(cls._indexed_properties()), from_id, batch
        )

    @classmethod
    def _rebuild_field_indexes(
        cls: Type[T], fields: List[str], from_id: int, batch: int
    ) -> Optional[int]:
        db = cls.db()
        type_name = cls.get_full_type_name()

        def index(entity):
            for field in fields:
                value = getattr(entity, field, None)
                if value is not None:
                    db.field_index_add(type_name, field, str(value), entity._id)

        return cls._for_each_in_batch(from_id, batch, index)

    @classmethod
    def rebuild_alias_index(
        cls: Type[T],
        from_id: int = 1,
        batch: int = 200,
        old_alias: Optional[str] = None,
    ) -> Optional[int]:
        """File one batch of entities under their alias value; resumable.

        A row is filed under its alias value when it is saved. After
        ``__alias__`` moves to another field (or is first set), the rows saved
        before are not found by the new field's values until this has run over
        them. Spread it over update calls as ``rebuild_field_index`` is (a
        query's writes are discarded):

            cursor = 1
            while cursor is not None:
                cursor = Realm.rebuild_alias_index(from_id=cursor, old_alias="id")

        With ``old_alias``, each row's entry under that field is removed.
        Idempotent.

        Returns:
            The from_id for the next batch, or None when the rebuild is done.

        Raises:
            ValueError: If the class has no ``__alias__``.
        """
        alias = getattr(cls, "__alias__", None)
        if not alias:
            raise ValueError(f"{cls.__name__} has no __alias__")
        db = cls.db()

        def file_under_alias(entity):
            value = getattr(entity, alias, None)
            if value is not None:
                db.save(cls._alias_key(), value, entity._id)
            if old_alias and old_alias != alias:
                old_value = getattr(entity, old_alias, None)
                if old_value is not None:
                    entity._drop_alias_entry(old_alias, old_value)

        return cls._for_each_in_batch(from_id, batch, file_under_alias)

    @classmethod
    def _for_each_in_batch(
        cls: Type[T], from_id: int, batch: int, action: Callable[[T], None]
    ) -> Optional[int]:
        """Call ``action`` on each live entity of one batch from ``from_id``.

        Returns:
            The from_id for the next batch, or None after the last.
        """
        if from_id < 1:
            raise ValueError("from_id must be at least 1")
        if batch < 1:
            raise ValueError("batch must be at least 1")

        max_id = cls.max_id()
        if max_id == 0 or from_id > max_id:
            return None

        # One range read per batch of *live* entities (deleted ids cost nothing)
        entities = cls.load_some(from_id, batch)
        for entity in entities:
            action(entity)

        if len(entities) < batch:
            return None
        next_from_id = int(entities[-1]._id) + 1
        return next_from_id if next_from_id <= max_id else None

    @classmethod
    def instances(cls: Type[T]) -> List[T]:
        """Get all instances of this entity type, including subclass instances.

        .. deprecated::
            Loads every entity, and raises TooLargeError past
            constants.MAX_UNPAGED. Use count(), find_by() on an indexed field,
            or page() (issue #22).

        Returns:
            List of entities
        """
        try:
            import warnings

            warnings.warn(
                "Entity.instances() is deprecated and will be removed. "
                "Use count(), find_by() or page() instead.",
                DeprecationWarning,
                stacklevel=2,
            )
        except (ImportError, AttributeError):
            pass
        db = Database.get_instance()
        full_type_name = cls.get_full_type_name()
        db.register_entity_type(cls, full_type_name)
        cls._require_unpaged("instances")

        max_id = cls.max_id()
        if max_id == 0:
            instances = []
        else:
            instances = cls.load_some(1, max_id)

        processed_classes = {cls}
        for type_name, type_cls in db._entity_types.items():
            if type_cls in processed_classes:
                continue
            if type_name == full_type_name or type_name == cls.__name__:
                continue
            if db.is_subclass(type_name, cls):
                processed_classes.add(type_cls)
                subclass_max_id = type_cls.max_id()
                if subclass_max_id > 0:
                    instances.extend(type_cls.load_some(1, subclass_max_id))

        return instances

    @classmethod
    def page(
        cls: Type[T], from_id: int = 1, count: int = 50
    ) -> Tuple[List[T], Optional[int]]:
        """One page of this type's entities, in ascending id order (issue #22).

        Returns (entities, next_from_id); next_from_id is None on the last page.
        Costs one range read plus ``count`` loads, however many entities
        exist, so it replaces instances() for large types::

            cursor = 1
            while cursor is not None:
                users, cursor = User.page(cursor, 50)

        Only this type's own entities (not subclasses'), with canonical
        sequential ids, as with load_some(). Entities that cannot be built
        because of a broken relation are skipped, so a page may hold fewer than
        ``count``. An entity whose ``migrate()`` raises is not skipped: the
        page raises MigrationError, as load() does.
        """
        if from_id < 1:
            raise ValueError("from_id must be at least 1")
        if count < 1:
            raise ValueError("count must be at least 1")
        db = cls.db()
        type_name = cls.get_full_type_name()
        # One extra row tells whether another page exists
        rows = db.load_page(type_name, from_id, count + 1)
        entities: List[T] = []
        for entity_id, data in rows[:count]:
            try:
                entity = db.get_entity(type_name, entity_id)
                if entity is None:
                    entity = cls._hydrate(entity_id, data)
                entities.append(entity)
            except (ValueError, AttributeError) as e:
                logger.warning(f"Skipping {cls.__name__}@{entity_id}: {e}")
        next_from_id = int(rows[count][0]) if len(rows) > count else None
        return entities, next_from_id

    @classmethod
    def _sorted_indexes(cls) -> Dict[str, Tuple[str, ...]]:
        """{index name: fields} for this type's sorted indexes.

        A property declared ``sorted=True`` gets its own index; a class's
        ``__sorted_indexes__`` adds indexes over several fields, compared in
        order (the second breaks ties in the first, and so on).
        """
        cached = cls.__dict__.get("_sorted_cache")
        if cached is not None:
            return cached
        from .properties import Property

        props: Dict[str, Any] = {}
        for klass in reversed(cls.__mro__):
            for attr_name, attr_value in klass.__dict__.items():
                if not attr_name.startswith("_") and isinstance(attr_value, Property):
                    props[attr_name] = attr_value
        indexes: Dict[str, Tuple[str, ...]] = {
            name: (name,) for name, prop in props.items() if prop.sorted
        }
        for spec in getattr(cls, "__sorted_indexes__", ()) or ():
            fields = (spec,) if isinstance(spec, str) else tuple(spec)
            for field in fields:
                prop = props.get(field)
                if prop is None or prop.type not in (int, float):
                    raise TypeError(
                        f"{cls.__name__}.__sorted_indexes__: {field} is not an "
                        f"Integer or Float property"
                    )
            indexes[",".join(fields)] = fields
        cls._sorted_cache = indexes
        return indexes

    def _sync_sorted(
        self, field: Optional[str] = None, index: Optional[str] = None
    ) -> None:
        """Bring this entity's sorted index entries up to date: the indexes
        that contain ``field``, only ``index``, or all of them."""
        if self._id is None:
            return
        db = self.db()
        for name, fields in self._sorted_indexes().items():
            if (field is not None and field not in fields) or (
                index is not None and name != index
            ):
                continue
            values = [getattr(self, f, None) for f in fields]
            encoded = (
                None
                if any(v is None for v in values)
                else "".join(_sort_encode(v) for v in values)
            )
            db.sorted_index_set(self._type, name, self._id, encoded)

    @classmethod
    def _sorted_index_name(cls, fields) -> Tuple[str, Tuple[str, ...]]:
        key = (fields,) if isinstance(fields, str) else tuple(fields)
        name = ",".join(key)
        if name not in cls._sorted_indexes():
            raise ValueError(
                f"{cls.__name__} has no sorted index on {name}: declare the "
                f"property sorted=True, or list the fields in __sorted_indexes__"
            )
        return name, key

    @classmethod
    def top_by(
        cls: Type[T],
        fields,
        count: int = 20,
        cursor: Optional[str] = None,
        descending: bool = True,
        where: Optional[Callable[[Any], bool]] = None,
        scan_limit: int = 200,
    ) -> Tuple[List[T], Optional[str]]:
        """One page of entities in the order of a sorted index.

        ``fields`` is a field declared ``sorted=True``, or a tuple listed in
        ``__sorted_indexes__``. Highest first unless ``descending=False``;
        ties go to the higher id. Entities with None in any of the fields are
        not indexed.

        Returns (entities, cursor); pass the cursor back for the next page,
        None once the index is exhausted. ``where`` drops entities that fail
        it. At most ``scan_limit`` entries are read per call, so with a
        selective ``where`` a page can come back short, or empty, with a
        cursor: call again. Cost is bounded by ``scan_limit`` loads, however
        many entities exist::

            top, cursor = Listing.top_by("installs", count=20)
            top, cursor = Listing.top_by(("installs", "likes"), count=20)
        """
        if count < 1:
            raise ValueError("count must be at least 1")
        if scan_limit < 1:
            raise ValueError("scan_limit must be at least 1")
        name, key = cls._sorted_index_name(fields)
        db = cls.db()
        type_name = cls.get_full_type_name()
        found: List[T] = []
        read = 0
        start = cursor
        chunk = count  # with `where`, doubled each round: rows read track rows kept
        while len(found) < count and read < scan_limit:
            want = min(
                count - len(found) if where is None else chunk, scan_limit - read, 100
            )
            chunk *= 2
            rows = db.sorted_index_scan(
                type_name, name, len(key), descending, start, want
            )
            for row_key, entity_id in rows:
                read += 1
                # The next same-length key: where a later page starts
                start = row_key[:-1] + chr(ord(row_key[-1]) + 1)
                entity = cls.load(entity_id)
                if entity is not None and (where is None or where(entity)):
                    found.append(entity)
                    if len(found) >= count:
                        break
            else:
                if len(rows) < want:
                    return found, None  # index exhausted
        if start is None or not db.sorted_index_scan(
            type_name, name, len(key), descending, start, 1
        ):
            return found, None
        return found, start

    @classmethod
    def rebuild_sorted_index(
        cls: Type[T], fields=None, from_id: int = 1, batch: int = 200
    ) -> Optional[int]:
        """Index one batch of existing entities in a sorted index (all of
        them when ``fields`` is None); resumable and idempotent.

        Use it after declaring a field sorted, or after changing a sorted
        field's type: each entity's stale entry is replaced, whatever it was::

            cursor = 1
            while cursor is not None:
                cursor = Listing.rebuild_sorted_index("installs", from_id=cursor)

        Returns the from_id for the next batch, or None when done.
        """
        if from_id < 1:
            raise ValueError("from_id must be at least 1")
        if batch < 1:
            raise ValueError("batch must be at least 1")
        name = None if fields is None else cls._sorted_index_name(fields)[0]
        max_id = cls.max_id()
        if max_id == 0 or from_id > max_id:
            return None
        entities = cls.load_some(from_id, batch)
        for entity in entities:
            entity._sync_sorted(index=name)
        if len(entities) < batch:
            return None
        next_from_id = int(entities[-1]._id) + 1
        return next_from_id if next_from_id <= max_id else None

    @classmethod
    def count(cls: Type[T]) -> int:
        """Get the total count of entities of this type.

        Returns:
            int: Total number of entities
        """
        type_name = cls.get_full_type_name()
        db = cls.db()
        count_key = f"{type_name}_count"
        count = db.load("_system", count_key)
        return int(count) if count else 0

    def reverse_count(self, relation_name: str) -> int:
        """Number of entities referencing this one via a unidirectional
        ManyToMany relation.

        ``relation_name`` is the ``reverse_name`` declared on the owning
        side, e.g. with ``User.profiles = ManyToMany('UserProfile', 'users',
        unidirectional=True)`` a profile's holder count is
        ``profile.reverse_count('users')``.

        Falls back to the legacy bidirectional index length when the
        relation was converted and no write has migrated the counter yet.
        """
        return self.db().reverse_count_get(self._type, self._id, relation_name)

    @classmethod
    def max_id(cls: Type[T]) -> int:
        """Get the maximum ID assigned to entities of this type.

        Returns:
            int: Maximum entity ID
        """
        return cls.db().max_id(cls.get_full_type_name())

    @classmethod
    def load_some(
        cls: Type[T],
        from_id: int,
        count: int = 10,
    ) -> List[T]:
        """Load some entities.

        Args:
            from_id (int): ID of the first entity to load
            count (int): Number of entities to load

        Returns:
            List[T]: List of entities for the requested page

        Raises:
            ValueError: If from_id or count is less than 1
            MigrationError: If the ``migrate()`` of a stored entity raises.
                Entities that cannot be built because of a broken relation
                are skipped; one that cannot be migrated is an error.

        Cost: with a range-capable storage backend (Basilisk's StableBTreeMap,
        MemoryStorage) this is one ordered range read per page regardless of
        how many ids in between were deleted; otherwise one read per id.
        """
        logger.debug(f"Loading up to {count} entities from id {from_id}")

        if from_id < 1:
            raise ValueError("from_id must be at least 1")
        if count < 1:
            raise ValueError("count must be at least 1")

        db = cls.db()
        type_name = cls.get_full_type_name()

        ret: List[T] = []
        max_id = db.max_id(type_name)
        while len(ret) < count:
            need = count - len(ret)
            page = db.load_page(type_name, from_id, need, max_id=max_id)
            for entity_id, data in page:
                try:
                    # Identity map first, so callers keep getting the same instance
                    entity = db.get_entity(type_name, entity_id)
                    if entity is None:
                        entity = cls._hydrate(entity_id, data)
                    ret.append(entity)
                except (ValueError, AttributeError) as e:
                    # Skip entities with broken/dangling relation references
                    # (full fix: issue #4 — lazy relation resolution)
                    logger.warning(f"Skipping {cls.__name__}@{entity_id}: {e}")
            if len(page) < need:
                break  # no more persisted entities of this type
            # Page was full but something was skipped: continue after it
            from_id = int(page[-1][0]) + 1

        return ret

    def delete(self) -> None:
        """Delete this entity from the database, cleaning up all reverse indexes."""
        logger.debug(f"Deleting entity {self._type}@{self._id}")
        from .constants import ACTION_DELETE
        from .hooks import call_entity_hook
        from .properties import ManyToMany, _require_unpaged

        # Deleting is a write: refused to a caller who does not own the entity,
        # to older code for a row newer code stored, whose index entries this
        # code may not know of, and through an instance another message has
        # made stale (its index entries are found from the values it holds)
        self._check_write()

        # Call hook before deletion
        allow, _ = call_entity_hook(self, None, self, None, ACTION_DELETE)

        if not allow:
            raise PermissionError("Hook rejected entity deletion")

        db = self.db()

        # Refuse before changing anything: each ManyToMany member is updated
        for cls in reversed(self.__class__.__mro__):
            for attr_name, attr_value in cls.__dict__.items():
                if not attr_name.startswith("_") and isinstance(attr_value, ManyToMany):
                    _require_unpaged(
                        db,
                        self,
                        attr_name,
                        "delete it",
                        f"first .discard() its {attr_name} page by page",
                    )

        self._unlink_relations()

        # Clean up reverse counters other entity types keep on *this* entity
        # via unidirectional ManyToMany relations targeting our type. Their
        # forward indexes may retain a dangling ID (skipped on load); the
        # counter itself must not outlive us.
        for reverse_name in db.reverse_count_names(self._type):
            db.reverse_count_delete(self._type, self._id, reverse_name)

        # Remove from field indexes (issue #11)
        for field_name in self._indexed_properties():
            value = getattr(self, field_name, None)
            if value is not None:
                db.field_index_remove(self._type, field_name, str(value), self._id)

        for name in self._sorted_indexes():
            db.sorted_index_set(self._type, name, self._id, None)

        db.delete(self._type, self._id)

        # Remove from entity registry
        db.unregister_entity(self._type, self._id)

        # Decrement the count when an entity is deleted
        type_name = self.__class__.get_full_type_name()
        count_key = f"{type_name}_count"
        current_count = int(db.load("_system", count_key) or 0)
        if current_count > 0:
            db.save("_system", count_key, str(current_count - 1))
        else:
            raise ValueError(
                f"Entity count for {type_name} is already zero; cannot decrement further."
            )

        # Remove from alias mappings when deleted
        if hasattr(self.__class__, "__alias__") and self.__class__.__alias__:
            alias_field = self.__class__.__alias__
            if hasattr(self, alias_field):
                alias_value = getattr(self, alias_field)
                if alias_value is not None:
                    self._drop_alias_entry(alias_field, alias_value)

        logger.debug(f"Deleted entity {self._type}@{self._id}")

    def _unlink_relations(self) -> None:
        """Remove this entity from the reverse index of every relation it is in."""
        from .properties import ManyToMany, ManyToOne, OneToMany, OneToOne

        db = self.db()
        for cls in reversed(self.__class__.__mro__):
            for attr_name, attr_value in cls.__dict__.items():
                if attr_name.startswith("_"):
                    continue
                if isinstance(attr_value, ManyToOne):
                    # Remove self from parent's reverse index
                    parent_ref = self.__dict__.get(f"_rel_{attr_name}")
                    if parent_ref is not None:
                        for type_name in attr_value._get_allowed_types():
                            entity_class = db._entity_types.get(type_name)
                            if entity_class and db.load(type_name, parent_ref):
                                db.reverse_index_remove(
                                    type_name,
                                    parent_ref,
                                    attr_value.reverse_name,
                                    self._id,
                                )
                                break
                elif isinstance(attr_value, OneToOne):
                    # Remove self from target's reverse index
                    target_ref = self.__dict__.get(f"_rel_{attr_name}")
                    if target_ref is not None:
                        for type_name in attr_value._get_allowed_types():
                            entity_class = db._entity_types.get(type_name)
                            if entity_class and db.load(type_name, target_ref):
                                db.reverse_index_remove(
                                    type_name,
                                    target_ref,
                                    attr_value.reverse_name,
                                    self._id,
                                )
                                break
                    # Also delete any reverse index where self is parent (inverse side)
                    db.reverse_index_delete(self._type, self._id, attr_name)
                elif isinstance(attr_value, OneToMany):
                    # Delete the reverse index entry for this parent
                    db.reverse_index_delete(self._type, self._id, attr_name)
                elif isinstance(attr_value, ManyToMany):
                    # Remove self from each related entity's reverse index
                    # (or decrement its counter for unidirectional relations)
                    related_ids = db.reverse_index_get(self._type, self._id, attr_name)
                    for related_id in related_ids:
                        for type_name in attr_value._get_allowed_types():
                            entity_class = db._entity_types.get(type_name)
                            if entity_class and db.load(type_name, related_id):
                                if attr_value.unidirectional:
                                    db.reverse_count_add(
                                        type_name,
                                        related_id,
                                        attr_value.reverse_name,
                                        -1,
                                    )
                                else:
                                    db.reverse_index_remove(
                                        type_name,
                                        related_id,
                                        attr_value.reverse_name,
                                        self._id,
                                    )
                                break
                    # Delete own index
                    db.reverse_index_delete(self._type, self._id, attr_name)

    def _serialize_base(self) -> Dict[str, Any]:
        """Shared serialization logic: core data, properties, and instance attributes."""
        # Get mixin data first if available
        data = super().serialize() if hasattr(super(), "serialize") else {}

        # Add core entity data. A row loaded from newer code keeps the version
        # it was stored at: an export of it is of that version, not of this code's.
        data.update(
            {
                "_type": self._type,
                "_id": self._id,
                "__version__": self._newer_version or self.__class__.__version__,
            }
        )

        # Add all property descriptors from class hierarchy
        for k in self._property_names():
            data[k] = getattr(self, k)

        # Add instance attributes
        for k, v in self.__dict__.items():
            if not k.startswith("_"):
                data[k] = v

        return data

    @staticmethod
    def _get_entity_reference(entity):
        """Get the best reference for an entity: alias value if available, otherwise _id."""
        if hasattr(entity.__class__, "__alias__") and entity.__class__.__alias__:
            alias_field = entity.__class__.__alias__
            alias_value = getattr(entity, alias_field, None)
            if alias_value is not None:
                return alias_value
        return entity._id

    def _serialize_full(self) -> Dict[str, Any]:
        """Full serialization including relation FK references. Used by _save()."""
        data = self._serialize_base()

        for attr_name in self._foreign_key_names():
            ref = self.__dict__.get(f"_rel_{attr_name}")
            if ref is not None:
                data[attr_name] = ref

        return data

    def _resolve_ref_with_alias(self, entity_id: str, allowed_types: list) -> str:
        """Resolve an entity ID to its best reference (alias value or ID)."""
        db = self.db()
        for type_name in allowed_types:
            entity_class = db._entity_types.get(type_name)
            if entity_class:
                entity = entity_class.load(entity_id)
                if entity:
                    return self._get_entity_reference(entity)
        return entity_id

    def serialize(self) -> Dict[str, Any]:
        """Convert the entity to a portable serializable dictionary.

        Includes: ManyToOne FK, owning-side OneToOne FK, ManyToMany entries.
        Excludes: OneToMany (reconstructed from ManyToOne during import).

        Returns:
            Dict containing the entity's serializable data
        """
        data = self._serialize_base()

        from ic_python_db.properties import (
            ManyToMany,
            ManyToOne,
            OneToOne,
            _require_unpaged,
        )

        db = self.db()

        for cls in reversed(self.__class__.__mro__):
            for attr_name, attr_value in cls.__dict__.items():
                if attr_name.startswith("_"):
                    continue
                if isinstance(attr_value, ManyToOne):
                    ref = self.__dict__.get(f"_rel_{attr_name}")
                    if ref is not None:
                        data[attr_name] = self._resolve_ref_with_alias(
                            ref, attr_value._get_allowed_types()
                        )
                elif isinstance(attr_value, OneToOne):
                    ref = self.__dict__.get(f"_rel_{attr_name}")
                    if ref is not None:
                        data[attr_name] = self._resolve_ref_with_alias(
                            ref, attr_value._get_allowed_types()
                        )
                elif isinstance(attr_value, ManyToMany):
                    _require_unpaged(
                        db,
                        self,
                        attr_name,
                        "serialize it",
                        f"serialize its {attr_name} with .page()",
                    )
                    related_ids = db.reverse_index_get(self._type, self._id, attr_name)
                    if related_ids:
                        data[attr_name] = [
                            self._resolve_ref_with_alias(
                                rid, attr_value._get_allowed_types()
                            )
                            for rid in related_ids
                        ]

        return data

    def _undo_merge(self, before: dict) -> None:
        """Put back the fields a failed deserialize() merged, and their index,
        sorted and alias entries: the row was not saved, so the instance and
        its entries must say what the row says."""
        from .properties import PROPERTY_STORAGE_PREFIX

        merged = dict(self.__dict__)
        self.__dict__.clear()
        self.__dict__.update(before)
        for name in self._property_names():
            prop = getattr(type(self), name)
            key = f"_{PROPERTY_STORAGE_PREFIX}_{name}"
            new = merged.get(key, prop.default)
            old = before.get(key, prop.default)
            if new != old:
                prop._move_entries(self, new, old)

    @classmethod
    def _without_mixin_state(cls, data: dict) -> dict:
        """data without the TimestampedMixin state it carries.

        The owner, creator, updater and timestamps say who wrote the row: the
        claim and each save set them. Taken from imported data, they would be
        stored as the data gives them, and as plain attributes they would
        shadow the mixin's own state. A field the class declares under one of
        these names is a Property, and is kept.
        """
        from .properties import Property

        state = getattr(cls, "_STATE_FIELDS", ())
        return {
            key: value
            for key, value in data.items()
            if key not in state or isinstance(getattr(cls, key, None), Property)
        }

    @classmethod
    def deserialize(cls, data: dict, level: int = 1):
        """Deserialize entity from dictionary data with upsert functionality.

        This method will:
        - Update an existing entity if found by _id or alias
        - Create a new entity if not found
        - Handle proper ID generation and counting for new entities
        - Update alias mappings when alias fields change

        Args:
            data: Dictionary containing serialized entity data
            level: Relationship loading depth for the upsert existence check.
                Defaults to 1 (no deep relationship loading) since deserialize
                resolves relationships separately. Use higher values only if
                you need the returned entity to have pre-loaded relationships.

        Returns:
            Entity instance (either updated existing or newly created)

        A record carries the version it was written at (``__version__``, as
        ``serialize()`` writes it) and is migrated from it; one without is
        taken to be of the current version.

        On a class with TimestampedMixin, the record's ``owner``, ``creator``,
        ``updater`` and timestamps are ignored: the caller claims a new row
        and is recorded as the updater of an existing one, as on any write.

        Raises:
            ValueError: If data is invalid or entity type not found
            MigrationError: If the record is of a newer version than the class,
                or its ``migrate()`` raises
        """
        if not isinstance(data, dict):
            raise ValueError("Data must be a dictionary")

        # Validate entity type
        if "_type" not in data:
            raise ValueError("Serialized data must contain '_type' field")

        entity_type = data["_type"]

        # If called on base Entity class, look up the specific entity class
        if cls.__name__ == "Entity":
            db = cls.db()
            target_class = db._entity_types.get(entity_type)
            # If not found and entity_type has namespace, try without namespace
            if not target_class:
                class_name = db._extract_class_name(entity_type)
                target_class = db._entity_types.get(class_name)
            if not target_class:
                raise ValueError(f"Unknown entity type: {entity_type}")
            # Delegate to the specific entity class
            return target_class.deserialize(data, level=level)

        # If called on specific entity class, validate type matches (check both full type name and class name)
        full_type_name = cls.get_full_type_name()
        if entity_type != full_type_name and entity_type != cls.__name__:
            raise ValueError(
                f"Entity type mismatch: expected {full_type_name} or {cls.__name__}, got {entity_type}"
            )

        # serialize() writes the version. A record without one was written by
        # hand, or by serialize() before it did: take it to be of this code's
        # version, since migrating it from version 1 re-applies every step to
        # data that may already be current.
        stored_version = data.get("__version__", cls.__version__)
        label = f"{entity_type}@{data.get('_id')}"
        if stored_version > cls.__version__:
            raise MigrationError(
                f"{label}: the record is of version {stored_version}, newer "
                f"than this code's {cls.__version__}"
            )
        if stored_version != cls.__version__:
            data = cls._migrate_row(label, data, stored_version)
        data = cls._without_mixin_state(data)

        # Try to find existing entity
        existing_entity = None

        # First try by _id if provided
        entity_id = data.get("_id")
        if entity_id:
            existing_entity = cls.load(str(entity_id), level=level)

        # If not found by ID and class has alias, try by alias
        if not existing_entity and hasattr(cls, "__alias__") and cls.__alias__:
            alias_field = cls.__alias__
            if alias_field in data:
                alias_value = data[alias_field]
                if alias_value is not None:
                    # Use the same lookup logic as __class_getitem__
                    alias_key = cls._alias_key()
                    actual_id = cls.db().load(alias_key, str(alias_value))
                    if actual_id:
                        existing_entity = cls.load(actual_id, level=level)

        if existing_entity:
            # UPDATE existing entity
            from ic_python_db.properties import Property, Relation

            # Refuse now: the fields merged below run hooks and move index
            # entries, and the save that would refuse comes after all of them
            existing_entity._check_write()

            # Update properties (merge mode - only update provided fields).
            # Each one moves its own index and alias entries; the row is saved
            # once, below. A field that raises must not leave saving off: the
            # entity stays in the identity map for the rest of the message.
            before = dict(existing_entity.__dict__)
            existing_entity._do_not_save = True
            try:
                for key, value in data.items():
                    if key.startswith("_"):
                        continue  # Skip internal fields

                    # Check if this is a relation property
                    prop = getattr(cls, key, None)
                    if isinstance(prop, Relation):
                        continue  # Skip relations for now, handle them after

                    # Update the property
                    setattr(existing_entity, key, value)
            except Exception:
                existing_entity._undo_merge(before)
                raise
            finally:
                existing_entity._do_not_save = False

            # Attempt to resolve relations (silently skip if related entity doesn't exist)
            for key, value in data.items():
                if key.startswith("_"):
                    continue

                prop = getattr(cls, key, None)
                if isinstance(prop, Relation) and value is not None:
                    try:
                        # Set relation - the descriptor will resolve IDs to entities
                        setattr(existing_entity, key, value)
                    except ValueError:
                        # Skip if related entity doesn't exist
                        pass

            # Save to persist changes and update alias mappings
            existing_entity._save()
            return existing_entity

        else:
            # CREATE new entity
            from ic_python_db.properties import Property, Relation

            # Prepare kwargs for entity creation (exclude relations)
            kwargs = {}

            # Include _id if provided (for proper deserialization)
            if entity_id:
                kwargs["_id"] = entity_id

            # Add properties and instance attributes (excluding relations and other internal fields)
            for key, value in data.items():
                if key.startswith("_") and key != "_id":
                    continue  # Skip internal fields except _id

                # Check if this is a relation property
                prop = getattr(cls, key, None)
                if isinstance(prop, Relation):
                    continue  # Skip relations for now, handle them after entity creation

                kwargs[key] = value

            # Create the entity instance
            entity = cls(**kwargs)

            # Attempt to resolve relations (silently skip if related entity doesn't exist)
            for key, value in data.items():
                if key.startswith("_"):
                    continue

                prop = getattr(cls, key, None)
                if isinstance(prop, Relation) and value is not None:
                    try:
                        # Set relation - the descriptor will resolve IDs to entities
                        setattr(entity, key, value)
                    except ValueError:
                        # Skip if related entity doesn't exist
                        pass

            return entity

    @classmethod
    def __class_getitem__(cls: Type[T], key: Any) -> Optional[T]:
        """Allow using class[id] syntax to load entities.

        Args:
            key: ID of entity to load, value of aliased field, or tuple of (field_name, value)
                 for specific field lookup.

        Returns:
            Entity if found, None otherwise

        Examples:
            Entity[1]              # Lookup by ID
            Entity["john"]         # Lookup by ID, then by __alias__ field
            Entity["name", "john"] # Lookup by specific field "name" only
        """
        logger.debug(f"Loading entity with key {key}")

        # Handle tuple for specific field lookup: Entity["field_name", "value"]
        if isinstance(key, tuple) and len(key) == 2:
            field_name, value = key
            if not isinstance(field_name, str) or not field_name:
                raise TypeError(
                    f"Field name must be a non-empty string, got {type(field_name).__name__}"
                )
            alias_key = cls._alias_key(field_name)
            logger.debug(f"Specific field lookup: alias_key={alias_key}, value={value}")
            actual_id = cls.db().load(alias_key, str(value))
            if actual_id:
                return cls.load(actual_id)
            return None

        # First try as direct ID lookup (convert to string if numeric)
        str_key = str(key) if isinstance(key, (int, float)) else key
        entity = cls.load(str_key)
        if entity:
            return entity

        logger.debug(f"Entity not found by ID {str_key}")

        # If entity not found by ID and class has __alias__ defined, try by alias
        if hasattr(cls, "__alias__") and cls.__alias__:
            alias_key = cls._alias_key()
            logger.debug(
                f"Trying to find entity by alias key {alias_key} with value {str_key}"
            )
            actual_key = cls.db().load(alias_key, str_key)
            if actual_key:
                logger.debug(
                    f"Found entity by alias key {alias_key} with value {str_key}"
                )
                return cls.load(actual_key)
            else:
                logger.debug(
                    f"Entity not found by alias key {alias_key} with value {str_key}"
                )

        return None

    def __eq__(self, other: object) -> bool:
        """Compare entities based on type and ID.

        Args:
            other: Object to compare with

        Returns:
            True if entities are equal, False otherwise
        """
        if not isinstance(other, Entity):
            return NotImplemented
        return self._type == other._type and self._id == other._id

    def __hash__(self) -> int:
        """Hash entity based on type and ID.

        Returns:
            Hash value
        """
        return hash((self._type, self._id))
