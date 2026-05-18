"""Enhanced entity implementation with support for mixins and entity types."""

from typing import Any, Dict, List, Optional, Set, Type, TypeVar

from ic_python_logging import get_logger

from .constants import LEVEL_MAX_DEFAULT
from .db_engine import Database

logger = get_logger(__name__)


T = TypeVar("T", bound="Entity")


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
        _context (Set[Entity]): Set of all entities in current context

    Property Storage:
        User-defined properties (String, Integer, etc.) are stored as _prop_{name}
        in the entity's __dict__ to avoid conflicts with internal attributes.
        Example: name = String() stores value in _prop_name
    """

    _entity_type = None  # To be defined in subclasses
    _context: Set["Entity"] = set()  # Set of entities in current context
    _deferred_types: List[Type["Entity"]] = []  # Types defined before DB exists
    _do_not_save = False
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

        # Add to context
        self.__class__._context.add(self)

        # Register this type with the database (using full type name)
        self.db().register_entity_type(self.__class__, self._type)

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
        else:
            # Update max_id if custom ID is higher than current max
            db = self.db()
            type_name = self._type
            current_max_id = db.load("_system", f"{type_name}_id")
            if current_max_id is None:
                current_max_id = "0"

            # Only update if the custom ID is numeric and higher than current max
            try:
                custom_id_int = int(self._id)
                current_max_int = int(current_max_id)
                if custom_id_int > current_max_int:
                    db.save("_system", f"{type_name}_id", self._id)
            except ValueError:
                # If custom ID is not numeric, don't update max_id counter
                pass

        # Register this instance in the entity registry
        self.db().register_entity(self)

        self._do_not_save = True
        # Set additional attributes
        for k, v in kwargs.items():
            if not k.startswith("_"):
                setattr(self, k, v)
        self._do_not_save = False

        if not self._loaded:
            self._save()

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

    def _save(
        self,
    ) -> "Entity":
        """Save the entity to the database.

        Returns:
            Entity: self for chaining

        Raises:
            PermissionError: If TimestampedMixin is used and caller is not the owner
        """

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
        if hasattr(self, "_update_timestamps"):
            from .context import get_caller_id

            caller_id = get_caller_id()
            if (
                hasattr(self, "check_ownership")
                and hasattr(self, "_timestamp_created")
                and self._timestamp_created
            ):
                if not self.check_ownership(caller_id):
                    raise PermissionError(
                        f"Only the owner can update this entity. Current owner: {self._owner}"
                    )
            self._update_timestamps(caller_id)

        # Save to database (full serialization preserves all relations)
        data = self._serialize_full()

        if not self._do_not_save:
            logger.debug(f"Saving entity {self._type}@{self._id} to database")
            db = self.db()
            persisted_data = {**data, "__version__": self.__class__.__version__}
            db.save(self._type, self._id, persisted_data)
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

        stored_version = data.get("__version__", 1)
        current_version = cls.__version__

        if stored_version != current_version:
            logger.debug(
                f"Version mismatch for {type_name}@{entity_id}: "
                f"stored={stored_version}, current={current_version}"
            )
            # Apply custom migration first (developer logic takes priority)
            data = cls.migrate(data, stored_version, current_version)
            # Auto-inject defaults for new fields not handled by migrate()
            data = cls._auto_migrate_defaults(data)
            data["__version__"] = current_version
            logger.debug(
                f"Migrated {type_name}@{entity_id} to version {current_version}"
            )

        # Create instance first
        entity = cls(**data, _loaded=True)

        # If migration was applied, persist the migrated data
        if stored_version != current_version:
            entity._save()

        return entity

    @classmethod
    def find(cls: Type[T], d) -> List[T]:
        """Find entities matching a dictionary filter.

        Uses load_some over the full ID range. Prefer indexed lookups where possible.
        """
        max_id = cls.max_id()
        if max_id == 0:
            return []
        all_entities = cls.load_some(1, max_id)
        return [
            e
            for e in all_entities
            if all(getattr(e, k, None) == v for k, v in d.items())
        ]

    @classmethod
    def instances(cls: Type[T]) -> List[T]:
        """Get all instances of this entity type, including subclass instances.

        .. deprecated::
            This method loads ALL entities and is O(max_id). Prefer load_some()
            with pagination or use relationship accessors.

        Returns:
            List of entities
        """
        try:
            import warnings

            warnings.warn(
                "Entity.instances() is deprecated and will be removed. "
                "Use load_some() with pagination instead.",
                DeprecationWarning,
                stacklevel=2,
            )
        except (ImportError, AttributeError):
            pass
        db = Database.get_instance()
        full_type_name = cls.get_full_type_name()
        db.register_entity_type(cls, full_type_name)

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

    @classmethod
    def max_id(cls: Type[T]) -> int:
        """Get the maximum ID assigned to entities of this type.

        Returns:
            int: Maximum entity ID
        """
        type_name = cls.get_full_type_name()
        db = cls.db()
        max_id_key = f"{type_name}_id"
        max_id = db.load("_system", max_id_key)
        return int(max_id) if max_id else 0

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
            ValueError: If page or page_size is less than 1
        """
        logger.info(f"Loading entities from {from_id} to {from_id + count}")

        if from_id < 1:
            raise ValueError("from_id must be at least 1")
        if count < 1:
            raise ValueError("count must be at least 1")

        # Return the slice of entities for the requested page
        ret = []

        while len(ret) < count and from_id <= cls.max_id():
            logger.info(f"Loading entity {from_id}")
            try:
                entity = cls.load(str(from_id))
                if entity:
                    ret.append(entity)
            except (ValueError, AttributeError) as e:
                # Skip entities with broken/dangling relation references
                # (full fix: issue #4 — lazy relation resolution)
                logger.warning(f"Skipping {cls.__name__}@{from_id}: {e}")
            from_id += 1

        return ret

    def delete(self) -> None:
        """Delete this entity from the database, cleaning up all reverse indexes."""
        logger.debug(f"Deleting entity {self._type}@{self._id}")
        from .constants import ACTION_DELETE
        from .hooks import call_entity_hook
        from .properties import ManyToMany, ManyToOne, OneToMany, OneToOne

        # Call hook before deletion
        allow, _ = call_entity_hook(self, None, self, None, ACTION_DELETE)

        if not allow:
            raise PermissionError("Hook rejected entity deletion")

        db = self.db()

        # Clean up reverse indexes for all relation properties
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
                    related_ids = db.reverse_index_get(self._type, self._id, attr_name)
                    for related_id in related_ids:
                        for type_name in attr_value._get_allowed_types():
                            entity_class = db._entity_types.get(type_name)
                            if entity_class and db.load(type_name, related_id):
                                db.reverse_index_remove(
                                    type_name,
                                    related_id,
                                    attr_value.reverse_name,
                                    self._id,
                                )
                                break
                    # Delete own index
                    db.reverse_index_delete(self._type, self._id, attr_name)

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
                    db.delete(self._alias_key(), alias_value)

        logger.debug(f"Deleted entity {self._type}@{self._id}")

        # Remove from context
        self.__class__._context.discard(self)

    def _serialize_base(self) -> Dict[str, Any]:
        """Shared serialization logic: core data, properties, and instance attributes."""
        # Get mixin data first if available
        data = super().serialize() if hasattr(super(), "serialize") else {}

        # Add core entity data
        data.update(
            {
                "_type": self._type,
                "_id": self._id,
            }
        )

        # Add all property descriptors from class hierarchy
        from ic_python_db.properties import Property

        for cls in reversed(self.__class__.__mro__):
            for k, v in cls.__dict__.items():
                if not k.startswith("_") and isinstance(v, Property):
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

        from ic_python_db.properties import ManyToOne, OneToOne

        for cls in reversed(self.__class__.__mro__):
            for attr_name, attr_value in cls.__dict__.items():
                if attr_name.startswith("_"):
                    continue
                if isinstance(attr_value, (ManyToOne, OneToOne)):
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

        from ic_python_db.properties import ManyToMany, ManyToOne, OneToOne

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
                    related_ids = db.reverse_index_get(self._type, self._id, attr_name)
                    if related_ids:
                        data[attr_name] = [
                            self._resolve_ref_with_alias(
                                rid, attr_value._get_allowed_types()
                            )
                            for rid in related_ids
                        ]

        return data

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

        Raises:
            ValueError: If data is invalid or entity type not found
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

        stored_version = data.get("__version__", 1)
        current_version = cls.__version__

        if stored_version != current_version:
            logger.debug(
                f"Version mismatch during deserialize for {entity_type}: "
                f"stored={stored_version}, current={current_version}"
            )
            data = cls.migrate(data, stored_version, current_version)
            data["__version__"] = current_version
            logger.debug(f"Migrated {entity_type} to version {current_version}")

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

            # Store old alias value for cleanup if it changes
            old_alias_value = None
            if hasattr(cls, "__alias__") and cls.__alias__:
                alias_field = cls.__alias__
                if hasattr(existing_entity, alias_field):
                    old_alias_value = getattr(existing_entity, alias_field)

            # Update properties (merge mode - only update provided fields)
            existing_entity._do_not_save = True
            for key, value in data.items():
                if key.startswith("_"):
                    continue  # Skip internal fields

                # Check if this is a relation property
                prop = getattr(cls, key, None)
                if isinstance(prop, Relation):
                    continue  # Skip relations for now, handle them after

                # Update the property
                setattr(existing_entity, key, value)

            # Handle alias update if alias field changed
            if hasattr(cls, "__alias__") and cls.__alias__:
                alias_field = cls.__alias__
                if alias_field in data:
                    new_alias_value = data[alias_field]
                    if old_alias_value != new_alias_value:
                        # Remove old alias mapping
                        if old_alias_value is not None:
                            cls.db().delete(cls._alias_key(), str(old_alias_value))
                        # New alias mapping will be created when entity is saved

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
