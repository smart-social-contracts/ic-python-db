"""
Core database engine implementation
"""

import hashlib
import json
import sys
import time
import weakref
from typing import Any, Dict, List, Optional, Tuple

from ic_python_logging import get_logger

from ._cdk import ic
from .storage import MemoryStorage, Storage, storage_sort_key, supports_range

logger = get_logger(__name__)

_DIGITS = frozenset("0123456789")
_HEX = frozenset("0123456789abcdef")
# Posting-list entry suffix widths (see "Posting lists" below)
_ID_WIDTH = 12
_HASH_WIDTH = 24
_SCAN_PAGE = 500
# Field-index key families repair_field_indexes walks (see "Repairing field
# indexes")
_FI_FAMILIES = ("_fi:", "_fx:")
# Most ids of an old-form list the repair converts in one call: about 245
# thousand instructions an id, about 345 thousand where other old lists are
# among its entry keys (one more read an id), so 24.5 billion, or 34.5 billion
# at worst, in a call of 40 billion
_CONVERT_MAX_IDS = 100_000
_BUCKETS = (("n", _ID_WIDTH, ":"), ("h", _HASH_WIDTH, "g"))
# Purge queue of deleted index sets (see _ix_clear): fixed-width positions
# keep its entries contiguous in key order
_PQ = "_pq@"
_PQ_HEAD = "_pq:head"
_PQ_TAIL = "_pq:tail"
# Entries of deleted sets each index write removes on the side
_PURGE_PER_WRITE = 20
# Sorted indexes (see "Sorted indexes" below): each field's value is encoded
# in _SORT_WIDTH decimal digits, an entity id in _SORT_ID_WIDTH characters
_SORT_WIDTH = 20
_SORT_ID_WIDTH = 13
_INVERT = str.maketrans("0123456789", "9876543210")


def _is_sequential_id(id_str: str) -> bool:
    """True for canonical sequential ids: ASCII digits, no leading zero."""
    return bool(id_str) and id_str[0] != "0" and all(c in _DIGITS for c in id_str)


def _id_digest(entity_id: str) -> str:
    """The digest a posting-list entry of a custom id is keyed by."""
    return hashlib.sha256(entity_id.encode("utf-8")).hexdigest()[:_HASH_WIDTH]


def _is_hash_entry_key(key: str) -> bool:
    """True for a key that ends like the entry of a custom (hashed) id."""
    digest = key[-_HASH_WIDTH:]
    return key.endswith("|h" + digest) and all(c in _HEX for c in digest)


def _is_hash_entry(key: str, value: str) -> bool:
    """True for the posting-list entry of a custom id: it is keyed by the digest
    of its value. An old list stored under a value that ends like such a key
    does not match it; only a value made for it does (``V|h`` and the digest of
    the list's own JSON text), and that list then reads as an entry of ``V``."""
    return _is_hash_entry_key(key) and _id_digest(value) == key[-_HASH_WIDTH:]


def _escape_key_text(text: str) -> str:
    """``text`` with no ``|`` in it, and still told apart from every other text.

    ``%`` is written ``%25`` and ``|`` is written ``%7C``. Escaping ``%`` too is
    what keeps two texts from sharing a result (``a|`` and ``a%7C``).
    """
    return text.replace("%", "%25").replace("|", "%7C")


def _unescape_key_text(text: str) -> str:
    """The text ``_escape_key_text`` was given."""
    return text.replace("%7C", "|").replace("%25", "%")


def _entry_base(key: str) -> Optional[str]:
    """The base a posting-list entry key belongs to; None for any other key
    (a count, a mark, an old list)."""
    tail = key[-_ID_WIDTH:]
    if key.endswith("|n" + tail) and all(c in _DIGITS for c in tail):
        return key[: -_ID_WIDTH - 2]
    if _is_hash_entry_key(key):
        return key[: -_HASH_WIDTH - 2]
    return None


def _old_list_size(key: str, value: str) -> int:
    """Ids in the list a release before 0.13 stored as one JSON array under
    ``key``, a key of a value with a "|"; 0 for any other key or value. (The
    entry of a custom id may read as an array: see ``_is_hash_entry``.)"""
    if (
        key.startswith("_fi:")
        and "|" in key
        and value.startswith("[")
        and not _is_hash_entry(key, value)
    ):
        return value.count(",") + 1
    return 0


def _listing_order(entity_id: str) -> Tuple[int, int]:
    """Sort key of the order ``_ix_all`` lists ids in: sequential ids
    ascending, then the others."""
    if _is_sequential_id(entity_id) and len(entity_id) <= _ID_WIDTH:
        return (0, int(entity_id))
    return (1, 0)


def _next_sequential_id(id_str: str, digits: int) -> Optional[int]:
    """Smallest ``digits``-digit sequential id that sorts after ``id_str``.

    ``id_str`` is a key suffix returned by a range over the ``digits``-length
    bucket, so it starts with a digit but may be a custom id such as
    ``"1x"`` or ``"1-a"``. Returns None when no such id exists in the bucket.
    """
    i = 0
    while i < len(id_str) and id_str[i] in _DIGITS:
        i += 1
    numeric_prefix, rest = id_str[:i], id_str[i:]
    if not rest:
        candidate = str(int(numeric_prefix) + 1)
    elif rest[0] > "9":
        # "1x" > every "1<digit>": jump to the next prefix, zero-filled
        candidate = str(int(numeric_prefix) + 1) + "0" * len(rest)
    else:
        # "1-a" < "10": the zero-filled prefix is the next candidate
        candidate = numeric_prefix + "0" * len(rest)
    if len(candidate) > digits:
        return None
    return max(int(candidate), 10 ** (digits - 1))


def _message_time() -> Optional[int]:
    """The IC's time, which is the same throughout one message; None outside
    a canister, where the identity map's weak references do the job."""
    if sys.platform != "wasi" or ic is None:
        return None
    try:
        return ic.time()
    except Exception:
        return None


class Database:
    """Main database class providing high-level operations"""

    _instance = None
    _audit_enabled = False

    @classmethod
    def get_instance(cls) -> "Database":
        if not cls._instance:
            cls._instance = cls.init(audit_enabled=True)
        return cls._instance

    @classmethod
    def init(
        cls,
        audit_enabled: bool = False,
        db_storage: Storage = None,
        db_audit: Storage = None,
    ) -> "Database":
        if cls._instance:
            raise RuntimeError("Database instance already exists")
        cls._instance = cls(audit_enabled, db_storage, db_audit)

        # Flush any Entity subclasses that were defined before Database existed
        from .entity import Entity

        Entity._flush_deferred_types()

        return cls._instance

    def __init__(
        self,
        audit_enabled: bool = False,
        db_storage: Storage = None,
        db_audit: Storage = None,
    ):
        self._db_storage = db_storage if db_storage else MemoryStorage()
        self._audit_enabled = audit_enabled
        self._db_audit = None
        if self._audit_enabled:
            self._db_audit = db_audit if db_audit else MemoryStorage()

        if self._db_audit:
            self._db_audit.insert("_min_id", "0")
            self._db_audit.insert("_max_id", "0")

            logger.debug(
                f"Audit database initialized with {len(list(self._db_audit.items()))} items"
            )

        self._entity_types = {}
        # {target class name: reverse counter names}, built by the first
        # delete after the registry last changed (see reverse_count_names)
        self._reverse_count_names: Optional[Dict[str, List[str]]] = None
        # Identity map: {(type_name, entity_id): entity}. Weak, so an entity
        # lives only as long as some caller holds it, and its entry goes with it.
        self._entity_registry = weakref.WeakValueDictionary()
        # In a canister weakref is a stub that holds strong references, so the
        # map is also emptied when a new message starts (see _registry)
        self._registry_time: Optional[int] = None
        # Whether deleted index sets may still have entries to purge (None:
        # not checked yet). Kept in the heap: a query discards it with its writes.
        self._purge_pending = None

    def as_user(self, user_id: str):
        """Context manager for running operations as a specific user.

        Usage:
            with db.as_user("alice"):
                doc = Document(title="My Doc")  # Owner is alice

        Args:
            user_id: ID of the user to impersonate

        Returns:
            Context manager that sets and resets caller ID
        """
        from .context import get_caller_id, set_caller_id

        class UserContext:
            def __init__(self, user_id):
                self.user_id = user_id
                self.previous_caller = None

            def __enter__(self):
                self.previous_caller = get_caller_id()
                set_caller_id(self.user_id)
                return self

            def __exit__(self, *args):
                set_caller_id(self.previous_caller)

        return UserContext(user_id)

    def clear(self):
        keys = list(self._db_storage.keys())
        for key in keys:
            self._db_storage.remove(key)

        # Also clear the entity registry
        self.clear_registry()
        self._purge_pending = None

        if not self._db_audit:
            return

        keys = list(self._db_audit.keys())
        for key in keys:
            self._db_audit.remove(key)

        self._db_audit.insert("_min_id", "0")
        self._db_audit.insert("_max_id", "0")

    def _registry(self):
        """The identity map, emptied when a new message starts.

        ic.time() is constant within one message, so a new value means the
        previous message is over: its entities are dropped from the map
        instead of piling up until the next upgrade. An entity some code
        still holds stays usable, but loading its id gives a new instance.
        """
        now = _message_time()
        if now is not None and now != self._registry_time:
            self._entity_registry.clear()
            self._registry_time = now
        return self._entity_registry

    def register_entity(self, entity_instance):
        """Register an entity instance in the identity map."""
        key = (entity_instance._type, entity_instance._id)
        self._registry()[key] = entity_instance

    def get_entity(self, type_name: str, entity_id: str):
        """Get entity from registry if it exists."""
        return self._registry().get((type_name, entity_id))

    def clear_registry(self):
        """Clear the entity registry (useful for testing)."""
        self._entity_registry.clear()

    def unregister_entity(self, type_name: str, entity_id: str):
        """Remove an entity from the registry (used when deleting)."""
        self._registry().pop((type_name, entity_id), None)

    def _audit(self, op: str, key: str, data: Any) -> None:
        if self._db_audit and self._audit_enabled:
            timestamp = int(time.time() * 1000)
            id = self._db_audit.get("_max_id")
            logger.debug(f"Audit: Recording {op} operation with ID {id}")
            self._db_audit.insert(str(id), json.dumps([op, timestamp, key, data]))
            self._db_audit.insert("_max_id", str(int(id) + 1))

    def save(self, type_name: str, id: str, data: dict) -> None:
        """Store the data under the given key

        Args:
            type_name: Type of the entity
            id: ID of the entity
            data: Data to store
        """
        key = f"{type_name}@{id}"
        self._db_storage.insert(key, json.dumps(data))
        self._audit("save", key, data)

    def load(self, type_name: str, id: str) -> Optional[dict]:
        """Load and return the data associated with the key

        Args:
            type_name: Type of the entity
            id: ID of the entity

        Returns:
            Dict if found, None otherwise
        """
        key = f"{type_name}@{id}"
        data = self._db_storage.get(key)
        if data:
            return json.loads(data)
        return None

    def max_id(self, type_name: str) -> int:
        """Highest sequential id ever issued for ``type_name`` (0 if none)."""
        raw = self.load("_system", f"{type_name}_id")
        return int(raw) if raw else 0

    def load_page(
        self,
        type_name: str,
        from_id: int,
        count: int,
        max_id: Optional[int] = None,
    ) -> List[Tuple[str, dict]]:
        """Persisted entities of ``type_name`` with sequential id >= ``from_id``.

        Returns up to ``count`` ``(id, data)`` pairs in ascending numeric id
        order, skipping ids that were deleted. Only canonical sequential ids
        (``"1"``, ``"42"``; no leading zeros, no custom string ids) are
        returned, matching what probing ``1..max_id`` would find.

        When the storage backend supports ``range`` (Basilisk's
        ``StableBTreeMap``, ``MemoryStorage``) a page costs one range call per
        digit-count bucket touched (usually one), independent of how many ids
        were deleted. Otherwise it falls back to one ``get`` per id.
        """
        if from_id < 1:
            raise ValueError("from_id must be at least 1")
        if count < 1:
            raise ValueError("count must be at least 1")
        if max_id is None:
            max_id = self.max_id(type_name)
        if max_id < from_id:
            return []
        if supports_range(self._db_storage):
            return self._load_page_range(type_name, from_id, count, max_id)
        return self._load_page_probe(type_name, from_id, count, max_id)

    def _load_page_probe(
        self, type_name: str, from_id: int, count: int, max_id: int
    ) -> List[Tuple[str, dict]]:
        results: List[Tuple[str, dict]] = []
        entity_id = from_id
        while len(results) < count and entity_id <= max_id:
            raw = self._db_storage.get(f"{type_name}@{entity_id}")
            if raw:
                results.append((str(entity_id), json.loads(raw)))
            entity_id += 1
        return results

    def _load_page_range(
        self, type_name: str, from_id: int, count: int, max_id: int
    ) -> List[Tuple[str, dict]]:
        # Keys are "Type@<id>" with unpadded decimal ids. The backend orders
        # str keys by byte length first, then bytewise (see storage_sort_key),
        # so all ids with the same digit count form one contiguous, numerically
        # ordered run. Walk one run ("bucket") per digit count, from the digit
        # count of from_id up to that of max_id.
        prefix = f"{type_name}@"
        results: List[Tuple[str, dict]] = []
        digits = len(str(from_id))
        max_digits = len(str(max_id))
        while digits <= max_digits and len(results) < count:
            lo_id = max(from_id, 10 ** (digits - 1))
            if digits == max_digits:
                # Exclusive upper bound right after max_id (same digit count),
                # or the whole bucket if max_id is the largest d-digit number.
                hi_id = max_id + 1
                end = (
                    f"{prefix}{hi_id}"
                    if len(str(hi_id)) == digits
                    else f"{prefix}{':' * digits}"
                )
            else:
                # ':' is the character after '9': every same-length key of
                # the form "Type@<digits>" sorts before "Type@:::".
                end = f"{prefix}{':' * digits}"

            start = f"{prefix}{lo_id}"
            plen = len(prefix)
            while len(results) < count:
                need = count - len(results)
                page = self._db_storage.range(start, end, need)
                for key, raw in page:
                    id_str = key[plen:]
                    if _is_sequential_id(id_str):
                        results.append((id_str, json.loads(raw)))
                if len(page) < need:
                    break  # bucket exhausted
                # Page was full but may have contained skipped custom ids:
                # continue from the next possible sequential id in this bucket.
                next_id = _next_sequential_id(page[-1][0][plen:], digits)
                if next_id is None:
                    break
                start = f"{prefix}{next_id}"
            digits += 1
        return results

    def delete(self, type_name: str, entity_id: str) -> None:
        """Delete the data associated with the key

        Args:
            type_name: Type of the entity
            id: ID of the entity
        """
        logger.debug(f"Database: Deleting entity {type_name}@{entity_id}")
        key = f"{type_name}@{entity_id}"
        data = self._db_storage.get(key)
        self._db_storage.remove(key)
        self._audit("delete", key, data)
        logger.debug(f"Database: Deleted entity {type_name}@{entity_id}")

    def update(self, type_name: str, id: str, field: str, value: Any) -> None:
        """Update a specific field in the stored data

        Args:
            type_name: Type of the entity
            id: ID of the entity
            field: Field to update
            value: New value
        """
        data = self.load(type_name, id)
        if data:
            data[field] = value
            self.save(type_name, id, data)
            self._audit("update", f"{type_name}@{id}", data)

    def get_all(self) -> Dict[str, Any]:
        """Return all stored data"""
        return {k: json.loads(v) for k, v in self._db_storage.items()}

    def _extract_class_name(self, type_name: str) -> str:
        """Extract the class name from a potentially namespaced type name.

        Args:
            type_name: Type name, possibly with namespace (e.g., "app::User")

        Returns:
            The class name without namespace (e.g., "User")
        """
        if "::" in type_name:
            return type_name.split("::")[-1]
        return type_name

    def _set_entity_type(self, name: str, type_obj) -> None:
        """Point a registry name at a class, dropping what is derived from the registry.

        Every Entity construction registers its class again, so an unchanged
        entry must leave the derived data alone.
        """
        if self._entity_types.get(name) is not type_obj:
            self._entity_types[name] = type_obj
            self._reverse_count_names = None

    def register_entity_type(self, type_obj, type_name: str = None):
        """Register an entity type with the database.

        Args:
            type_obj: Type object to register
            type_name: Optional full type name (including namespace). If not provided, uses class name.
        """
        if type_name is None:
            type_name = type_obj.__name__
        logger.debug(
            f"Registering type {type_name} (class: {type_obj.__name__}) with bases {[b.__name__ for b in type_obj.__bases__]}"
        )

        # Always register under the full type name
        self._set_entity_type(type_name, type_obj)

        # For backward compatibility, also register under class name if:
        # 1. No namespace (type_name == class name) - allows non-namespaced lookups, OR
        # 2. Class name slot is available (no collision) - allows simple name lookups
        # This dual registration enables both "User" and "app::User" lookups while
        # preventing collisions when multiple namespaced entities share a class name
        if (
            type_name == type_obj.__name__
            or type_obj.__name__ not in self._entity_types
        ):
            self._set_entity_type(type_obj.__name__, type_obj)
        else:
            # Class name already registered - log warning about potential collision
            existing = self._entity_types[type_obj.__name__]
            if existing != type_obj:
                logger.warning(
                    f"Class name '{type_obj.__name__}' collision: '{type_name}' not registered under class name. "
                    f"Existing registration: '{getattr(existing, '__module__', 'unknown')}.{existing.__name__}'. "
                    f"Use full type name '{type_name}' in relations."
                )

    def is_subclass(self, type_name, parent_type):
        """Check if a type is a subclass of another type.

        Args:
            type_name: Name of the type to check (may include namespace)
            parent_type: Parent type to check against

        Returns:
            bool: True if type_name is a subclass of parent_type
        """
        type_obj = self._entity_types.get(type_name)
        if not type_obj:
            # Try to extract class name from namespaced type (e.g., "app::User" -> "User")
            class_name = self._extract_class_name(type_name)
            type_obj = self._entity_types.get(class_name)
        logger.debug(f"Type check: {type_name} -> {parent_type.__name__}")
        return type_obj and issubclass(type_obj, parent_type)

    def dump_json(self, pretty: bool = False) -> str:
        """Dump the entire database as a JSON string.

        Args:
            pretty: If True, format the JSON with indentation for readability

        Returns:
            JSON string containing all database data organized by type
        """
        result = {}
        for key in self._db_storage.keys():
            if key.startswith("_"):  # Skip internal keys
                continue
            try:
                type_name, id = key.split("@")
                if type_name not in result:
                    result[type_name] = {}
                result[type_name][id] = json.loads(self._db_storage.get(key))
            except (ValueError, json.JSONDecodeError):
                continue  # Skip invalid entries

        if pretty:
            return json.dumps(result, indent=2)
        return json.dumps(result)

    def raw_dump_json(self, pretty: bool = False) -> str:
        """Dump the raw contents of the storage as a JSON string.

        Args:
            pretty: If True, format the JSON with indentation for readability

        Returns:
            A JSON string representation of the raw storage contents
        """
        result = {}
        for key in self._db_storage.keys():
            result[key] = self._db_storage.get(key)
        if pretty:
            return json.dumps(result, indent=2)
        return json.dumps(result)

    def build_schema_from_entities(self) -> Dict[str, Any]:
        """Build a schema descriptor from all registered entity types.

        Returns:
            Dict describing every entity type's fields, types, defaults,
            constraints, and relationships.
        """
        from .schema import build_schema

        return build_schema(self._entity_types)

    def get_schema_hash(self) -> Optional[str]:
        """Return the stored schema hash, or None if no schema has been saved."""
        return self.load("_system", "_schema_hash")

    def save_schema(self) -> None:
        """Persist the current schema descriptor and its hash."""
        from .schema import schema_hash

        current = self.build_schema_from_entities()
        self.save("_system", "_schema", current)
        self.save("_system", "_schema_hash", schema_hash(current))

    def check_upgrade_compatibility(self, raise_on_error: bool = True):
        """Check that current Entity definitions are compatible with stored schema.

        Compares the previously stored schema against the live class definitions.
        Breaking changes (type changes, new fields without defaults, relationship
        cardinality changes) require the Entity to define a migrate() override.

        On success, saves the new schema. On failure (with raise_on_error=True),
        raises SchemaIncompatibleError — designed to be called from post_upgrade
        so the IC rolls back the upgrade.

        Returns:
            List of SchemaChange objects.

        Raises:
            SchemaIncompatibleError: if incompatible and raise_on_error is True.
        """
        from .schema import check_upgrade_compatibility

        return check_upgrade_compatibility(self, raise_on_error=raise_on_error)

    # ── Posting lists (issue #18) ──────────────────────────────────────────────
    #
    # Field indexes and reverse indexes map one base key to a set of entity
    # ids. Each id is its own storage entry, so adding or removing one costs
    # O(1) storage operations however many ids share the base, and a page is
    # one range read:
    #
    #   {base}|n{id zero-padded to _ID_WIDTH}  -> id   canonical sequential ids
    #   {base}|h{sha256(id)[:_HASH_WIDTH]}     -> id   any other id
    #   {base}|c                               -> number of ids
    #
    # Storage orders str keys by length, then bytewise (storage_sort_key), so
    # fixed-width suffixes keep one base's entries contiguous, and the "n"
    # entries come back in ascending numeric id order.
    #
    # A base that holds a "|" can make keys of its own that look like another
    # base's entries (base "a|n0000000000" has the key "a|n0000000000|c", as
    # long as the entries "a|n000000000100"). A field index never has one (see
    # _fi_key). A reverse index base, _ri:{Type}:{parent id}:{relation}, is
    # not escaped, and a custom parent id may hold a "|": but the relation name
    # ends the base, so a look-alike has to end with it as well, which is
    # rarely possible. Either way a range read does not depend on it: it skips
    # any key that is not an entry of its base and goes on (see _ix_bucket).
    #
    # Before 0.13 a base key held the whole set as one JSON array, which hit
    # the value-size limit at ~10k ids. Such an array is converted to entries
    # the first time its base is touched (see _ix_count).

    def _scan(
        self, start: str, end: Optional[str], limit: int
    ) -> List[Tuple[str, str]]:
        """Up to ``limit`` pairs with ``start <= key < end``, in key order;
        ``end=None`` is no end."""
        if supports_range(self._db_storage):
            return self._db_storage.range(start, end, limit)
        lo = storage_sort_key(start)
        hi = None if end is None else storage_sort_key(end)
        keys = sorted(
            (
                k
                for k in self._db_storage.keys()
                if lo <= storage_sort_key(k)
                and (hi is None or storage_sort_key(k) < hi)
            ),
            key=storage_sort_key,
        )
        return [(k, self._db_storage.get(k)) for k in keys[:limit]]

    @staticmethod
    def _ix_entry_key(base: str, entity_id: str) -> str:
        if _is_sequential_id(entity_id) and len(entity_id) <= _ID_WIDTH:
            return f"{base}|n{entity_id.zfill(_ID_WIDTH)}"
        return f"{base}|h{_id_digest(entity_id)}"

    def _ix_own(self, key: str) -> Optional[str]:
        """What is stored under a key of this list's own (its count, its mark,
        an entry of it), after an old list of another value that is stored
        there is converted, which moves it out of the way.

        Before 0.13 a list was one array under the bare key of its value, and
        the bare key of the value "V|c" is the key of V's count; "V|d" is V's
        mark and "V|n" and 12 digits an entry of V.
        """
        value = self._db_storage.get(key)
        if value is not None and value.startswith("["):
            # (An entry of a custom id that reads as an array is left: see
            # _is_hash_entry)
            self._ix_count(key)
            value = self._db_storage.get(key)
        return value

    def _ix_count(self, base: str) -> int:
        raw = self._ix_own(f"{base}|c")
        if raw is not None:
            return int(raw)
        legacy = self._db_storage.get(base)
        if legacy is None:
            return 0
        if _is_hash_entry(base, legacy):
            # An entry holds a custom id, which may read as a list: it is not
            # the array an old list was stored as, and converting it would
            # remove the entry
            return 0
        try:
            ids = json.loads(legacy)
        except ValueError:
            return 0
        if not isinstance(ids, list):
            return 0
        self._db_storage.remove(base)
        unique = {str(i) for i in ids}
        # Old lists of other values among the entry keys (rare: two reads to
        # know) are converted before an entry is written over them
        crowded = any(
            self._scan(f"{base}|{tag}{'0' * width}", f"{base}|{tag}{top * width}", 1)
            for tag, width, top in _BUCKETS
        )
        for entity_id in unique:
            key = self._ix_entry_key(base, entity_id)
            if crowded:
                self._ix_own(key)
            self._db_storage.insert(key, entity_id)
        if unique:
            self._db_storage.insert(f"{base}|c", str(len(unique)))
        return len(unique)

    def _ix_add(self, base: str, entity_id: str) -> None:
        self._purge_some()
        count = self._ix_count(base)
        if count == 0:
            self._ix_revive(base)
        key = self._ix_entry_key(base, entity_id)
        if self._ix_own(key) == entity_id:
            return
        self._db_storage.insert(key, entity_id)
        self._db_storage.insert(f"{base}|c", str(count + 1))

    def _ix_remove(self, base: str, entity_id: str) -> None:
        self._purge_some()
        count = self._ix_count(base)
        if count == 0:
            return
        key = self._ix_entry_key(base, entity_id)
        if self._db_storage.get(key) != entity_id:
            return  # not listed (the key may be an old list of another value)
        self._db_storage.remove(key)
        if count > 1:
            self._db_storage.insert(f"{base}|c", str(count - 1))
        else:
            self._db_storage.remove(f"{base}|c")

    def _ix_contains(self, base: str, entity_id: str) -> bool:
        if self._ix_count(base) == 0:
            return False
        return self._db_storage.get(self._ix_entry_key(base, entity_id)) == entity_id

    def _ix_page(self, base: str, from_id: int, count: int) -> List[str]:
        """Up to ``count`` sequential ids >= ``from_id``, ascending."""
        if from_id < 1:
            raise ValueError("from_id must be at least 1")
        if count < 1:
            raise ValueError("count must be at least 1")
        if self._ix_count(base) == 0 or len(str(from_id)) > _ID_WIDTH:
            return []
        ids: List[str] = []
        first = str(from_id).zfill(_ID_WIDTH)
        for _, value in self._ix_bucket(base, "n", _ID_WIDTH, ":", count, first):
            ids.append(value)
            if len(ids) == count:
                break
        return ids

    def _ix_bucket(
        self,
        base: str,
        tag: str,
        width: int,
        top: str,
        page_size: int = _SCAN_PAGE,
        first: Optional[str] = None,
    ):
        """The entries of ``base`` under ``tag`` (from the entry suffix
        ``first``, when given), in key order.

        A key of another base can fall in the range (see "Posting lists"), and
        so can the old list of another value (see _ix_own): neither is an entry,
        which holds the id its key spells. A page that came back short is the
        end of the range; one that is full but held such keys is not, so the
        scan goes on from its last key.
        """
        start = f"{base}|{tag}{first or '0' * width}"
        end = f"{base}|{tag}{top * width}"
        cut = len(base) + 2
        valid = _DIGITS if tag == "n" else _HEX
        last = None
        while True:
            # A range starts at its last key, which is read twice: one more
            # than a page. (No "next key" is computed: the successor of a
            # char can be longer in utf-8, so the key after it is not the
            # same length.)
            wanted = page_size if last is None else page_size + 1
            page = self._scan(start, end, wanted)
            for key, value in page:
                if key != last and all(c in valid for c in key[cut:]):
                    if tag == "n":
                        own = value == str(int(key[cut:]))
                    else:
                        own = not value.startswith("[") or _is_hash_entry(key, value)
                    if own:
                        yield key, value
            if len(page) < wanted:
                return
            last = start = page[-1][0]

    def _ix_all(self, base: str) -> List[str]:
        """Every id in the set: sequential ids ascending, then the others."""
        if self._ix_count(base) == 0:
            return []
        ids = [v for _, v in self._ix_bucket(base, "n", _ID_WIDTH, ":")]
        ids.extend(v for _, v in self._ix_bucket(base, "h", _HASH_WIDTH, "g"))
        return ids

    def _ix_clear(self, base: str) -> None:
        """Empty a set in O(1); its entries are purged in bounded steps.

        Every reader checks the counter first, so removing it empties the set
        at once. The entries are queued and removed by later index writes
        (_PURGE_PER_WRITE each) or by purge_step(). ``{base}|d`` marks the set
        as dead and holds its queue position, so re-adding to it (a reused
        custom id) purges the leftovers first instead of reviving them.
        """
        if self._ix_count(base) == 0:
            return
        storage = self._db_storage
        tail = int(storage.get(_PQ_TAIL) or 0)
        storage.insert(self._pq_key(tail), base)
        storage.insert(_PQ_TAIL, str(tail + 1))
        storage.insert(f"{base}|d", str(tail))
        storage.remove(f"{base}|c")
        self._purge_pending = True

    @staticmethod
    def _pq_key(position: int) -> str:
        return f"{_PQ}{str(position).zfill(_ID_WIDTH)}"

    def _ix_drain(self, base: str, limit: int) -> int:
        """Remove up to ``limit`` entries of ``base``; returns how many."""
        removed = 0
        for tag, width, top in _BUCKETS:
            keys = []
            page_size = min(limit - removed, _SCAN_PAGE)
            for key, _ in self._ix_bucket(base, tag, width, top, page_size):
                if removed + len(keys) >= limit:
                    break
                keys.append(key)
            for key in keys:
                self._db_storage.remove(key)
            removed += len(keys)
            if removed >= limit:
                break
        return removed

    def _ix_revive(self, base: str) -> None:
        """Before reusing a deleted set: purge its leftovers, drop its queue entry."""
        position = self._ix_own(f"{base}|d")
        if position is None:
            return
        while self._ix_drain(base, _SCAN_PAGE) == _SCAN_PAGE:
            pass
        self._db_storage.remove(f"{base}|d")
        queued = self._pq_key(int(position))
        if self._db_storage.get(queued) is not None:
            self._db_storage.remove(queued)

    def _purge_some(self) -> None:
        if self._purge_pending is not False:
            self.purge_step(_PURGE_PER_WRITE)

    def purge_step(self, limit: int = 500) -> bool:
        """Remove up to ``limit`` entries of deleted index sets.

        Deleting an entity empties its relation indexes at once; their entries
        are removed in the background, a few by each later index write. A
        timer can call this to finish sooner. Returns True when nothing is
        left to purge.
        """
        if limit < 1:
            raise ValueError("limit must be at least 1")
        storage = self._db_storage
        head = int(storage.get(_PQ_HEAD) or 0)
        tail = int(storage.get(_PQ_TAIL) or 0)
        start, budget = head, limit
        while head < tail and budget > 0:
            queued = self._pq_key(head)
            base = storage.get(queued)
            removed = self._ix_drain(base, budget) if base is not None else 0
            if removed == budget:
                break  # out of budget, maybe not drained yet
            budget -= removed
            if base is not None:
                storage.remove(f"{base}|d")
                storage.remove(queued)
            head += 1
        if head != start:
            storage.insert(_PQ_HEAD, str(head))
        self._purge_pending = head < tail
        return not self._purge_pending

    # ── Reverse Index API ──────────────────────────────────────────────────────
    #
    # Reverse indexes allow OneToMany / ManyToMany / inverse-OneToOne relations
    # to be resolved without scanning all child entities.
    #
    # Base key: _ri:{ParentType}:{parent_id}:{relation_name} (posting list)

    def _ri_key(self, parent_type: str, parent_id: str, relation_name: str) -> str:
        return f"_ri:{parent_type}:{parent_id}:{relation_name}"

    def reverse_index_get(
        self, parent_type: str, parent_id: str, relation_name: str
    ) -> List[str]:
        """All child ids of a parent's relation (O(children); prefer
        ``reverse_index_page`` for relations that can grow large)."""
        return self._ix_all(self._ri_key(parent_type, parent_id, relation_name))

    def reverse_index_page(
        self,
        parent_type: str,
        parent_id: str,
        relation_name: str,
        from_id: int,
        count: int,
    ) -> List[str]:
        """Up to ``count`` sequential child ids >= ``from_id``, ascending.

        Children with custom (non-sequential) ids are not paged; use
        ``reverse_index_get`` for those.
        """
        return self._ix_page(
            self._ri_key(parent_type, parent_id, relation_name), from_id, count
        )

    def reverse_index_count(
        self, parent_type: str, parent_id: str, relation_name: str
    ) -> int:
        """Number of child ids in a parent's relation (one read)."""
        return self._ix_count(self._ri_key(parent_type, parent_id, relation_name))

    def reverse_index_contains(
        self, parent_type: str, parent_id: str, relation_name: str, child_id: str
    ) -> bool:
        return self._ix_contains(
            self._ri_key(parent_type, parent_id, relation_name), child_id
        )

    def reverse_index_add(
        self, parent_type: str, parent_id: str, relation_name: str, child_id: str
    ) -> None:
        """Add a child ID to a parent's reverse index."""
        self._ix_add(self._ri_key(parent_type, parent_id, relation_name), child_id)

    def reverse_index_remove(
        self, parent_type: str, parent_id: str, relation_name: str, child_id: str
    ) -> None:
        """Remove a child ID from a parent's reverse index."""
        self._ix_remove(self._ri_key(parent_type, parent_id, relation_name), child_id)

    def reverse_index_delete(
        self, parent_type: str, parent_id: str, relation_name: str
    ) -> None:
        """Delete a whole reverse index (used when the parent is deleted).

        Costs one removal per child id.
        """
        self._ix_clear(self._ri_key(parent_type, parent_id, relation_name))

    # ── Field Index API (issue #11) ────────────────────────────────────────────
    #
    # Secondary indexes for scalar properties declared with indexed=True.
    # Allow equality lookups (Entity.find_by) without scanning all entities.
    #
    # Base key: _fi:{Type}:{field}:{value} (posting list)
    #
    # A value is text a caller chooses, so its base must not hold a "|" (see
    # "Posting lists"). One that holds a "|" goes under _fx instead, with "|"
    # and "%" written as %7C and %25 (so that no two values share an escaped
    # form): _fx:{Type}:{field}:{escaped value}. Every other value, "%" alone
    # included, keeps the key it always had, so nothing moved for them.
    #
    # Such a value used to be filed under _fi with no escape. Its entries stay
    # readable there: each call below also looks at the old key of a value that
    # has one, and adding or removing an id takes it off the old list.
    # repair_field_indexes moves them all, those of custom ids too (which
    # Entity.rebuild_field_indexes does not reach), and converts the lists
    # stored before 0.13 as one array. Once it has run to its end on every
    # realm, _fi_old_key and what uses it can go, but for what it cannot judge
    # and leaves (see its docstring). (_fx, not _fi, so that an old key can
    # never be taken for the new key of another value.)

    @staticmethod
    def _fi_needs_escape(value: str) -> bool:
        return "|" in value

    def _fi_key(self, type_name: str, field_name: str, value: str) -> str:
        if self._fi_needs_escape(value):
            return f"_fx:{type_name}:{field_name}:{_escape_key_text(value)}"
        return f"_fi:{type_name}:{field_name}:{value}"

    def _fi_old_key(self, type_name: str, field_name: str, value: str) -> Optional[str]:
        """Where ``value`` was filed before it was escaped, if not where it is now."""
        if self._fi_needs_escape(value):
            return f"_fi:{type_name}:{field_name}:{value}"
        return None

    def field_index_get(self, type_name: str, field_name: str, value: str) -> List[str]:
        """All ids of entities whose ``field_name`` equals ``value``
        (O(matches); prefer ``field_index_page``)."""
        ids = self._ix_all(self._fi_key(type_name, field_name, value))
        old = self._fi_old_key(type_name, field_name, value)
        if old is None:
            return ids
        # An id on both lists (a release that did not know the new keys wrote
        # to the old one) is listed once
        ids = list(dict.fromkeys(ids + self._ix_all(old)))
        return sorted(ids, key=_listing_order)

    def field_index_page(
        self, type_name: str, field_name: str, value: str, from_id: int, count: int
    ) -> List[str]:
        """Up to ``count`` sequential ids >= ``from_id`` whose ``field_name``
        equals ``value``, ascending. One range read; two for a value with a
        "|" while its old key may hold entries, and one more for each key of
        another value in the way."""
        key = self._fi_key(type_name, field_name, value)
        ids = self._ix_page(key, from_id, count)
        old = self._fi_old_key(type_name, field_name, value)
        if old is None:
            return ids
        # The first ``count`` of both lists together are among the first
        # ``count`` of each
        ids += self._ix_page(old, from_id, count)
        return sorted(set(ids), key=int)[:count]

    def field_index_count(self, type_name: str, field_name: str, value: str) -> int:
        """Number of entities whose ``field_name`` equals ``value`` (one read;
        two for a value with a "|" while its old key may hold entries, and an id
        on both lists is counted twice until repair_field_indexes has run)."""
        total = self._ix_count(self._fi_key(type_name, field_name, value))
        old = self._fi_old_key(type_name, field_name, value)
        if old is not None:
            total += self._ix_count(old)
        return total

    def field_index_add(
        self, type_name: str, field_name: str, value: str, entity_id: str
    ) -> None:
        """Add an entity ID to the index entry for ``value``."""
        old = self._fi_old_key(type_name, field_name, value)
        if old is not None:
            self._ix_remove(old, entity_id)
        self._ix_add(self._fi_key(type_name, field_name, value), entity_id)

    def field_index_remove(
        self, type_name: str, field_name: str, value: str, entity_id: str
    ) -> None:
        """Remove an entity ID from the index entry for ``value``."""
        old = self._fi_old_key(type_name, field_name, value)
        if old is not None:
            self._ix_remove(old, entity_id)
        self._ix_remove(self._fi_key(type_name, field_name, value), entity_id)

    # ── Repairing field indexes ────────────────────────────────────────────────
    #
    # A release that does not know the _fx keys files a value with a "|" under
    # its _fi key, and does not take an id off the _fx list when the entity's
    # value changes or the entity is deleted. After an upgrade from it (or
    # after a rollback to it and a new upgrade) entries are under the old key,
    # or stale on a list, or on both; a list stored before 0.13 may still be
    # one JSON array. Entity.rebuild_field_indexes walks the entities, so it
    # can add or move an entry, but it never finds a stale one and does not
    # reach custom ids. This walks the keys instead, and checks each entry of a
    # value with a "|" against its entity.

    def _index_ranges(self, after: str):
        """The (start, end) key ranges that hold field-index keys, in the order
        repair_field_indexes walks them, from the one that holds ``after``.

        Storage orders keys by length first, so each length of each family is a
        range of its own. Only lengths at which the map holds a key are
        visited (the shortest key at least as long as the last length tells
        the next), so no length is out of reach.
        """
        resume = after[:4] if after else ""
        first = _FI_FAMILIES.index(resume) if resume else 0
        for family in _FI_FAMILIES[first:]:
            length = len(family) + 1
            if family == resume:
                length = len(after.encode("utf-8"))
            while True:
                shortest = self._scan("\x00" * length, None, 1)
                if not shortest:
                    break
                length = len(shortest[0][0].encode("utf-8"))
                fill = "\x00" * (length - len(family))
                resumed = family == resume and length == len(after.encode("utf-8"))
                yield (after if resumed else family + fill), (
                    family[:-1] + chr(ord(family[-1]) + 1) + fill
                )
                length += 1

    def _field_of_base(self, rest: str) -> Optional[Tuple[str, str, str]]:
        """(type name, field, text) of ``{Type}:{field}:{text}``, when the type
        is registered and the field indexed; None otherwise."""
        for entity_class in self._entity_types.values():
            type_name = entity_class.get_full_type_name()
            if not rest.startswith(type_name + ":"):
                continue
            skip = len(type_name) + 1
            field, colon, text = rest[skip:].partition(":")
            if colon and field in entity_class._indexed_properties():
                return type_name, field, text
        return None

    def _repair_entry(self, key: str, value: str) -> None:
        size = _old_list_size(key, value)
        if size:
            if size <= _CONVERT_MAX_IDS:
                # Converted into entries, which are longer keys: the walk
                # reaches them later
                self._ix_count(key)
            else:
                logger.warning(
                    f"repair_field_indexes: the old list {key!r} has {size} ids, "
                    f"more than a call can convert; left as it is"
                )
            return
        base = _entry_base(key)
        if base is None:
            return
        rest, old_style = base[4:], key.startswith("_fi:")
        if old_style and "|" not in rest:
            return  # a value without "|": its key never changed
        found = self._field_of_base(rest)
        if found is None:
            return  # not a type and field this code can judge
        type_name, field, text = found
        text = text if old_style else _unescape_key_text(text)
        row = self.load(type_name, value)  # an entry's value is its entity id
        if row is not None and field not in row:
            return  # a row from before the field: nothing to compare with
        if row is None or row[field] is None or str(row[field]) != text:
            self._ix_remove(base, value)
        elif old_style:
            self.field_index_add(type_name, field, text, value)

    def repair_field_indexes(self, after: str = "", limit: int = 500) -> Optional[str]:
        """Check the field-index entries of values with a ``|`` against the
        entities, at most ``limit`` keys at a time; resumable.

        An entry whose entity is gone, or no longer has that value, is removed.
        One under the old key (before escaping) whose entity has the value is
        moved to its present key, so an id is listed once. A list a release
        before 0.13 stored as one array under the old key of such a value is
        converted into entries first, and they are checked later in the walk.
        Entries of values without a ``|`` are only looked at.

            cursor = ""
            while cursor is not None:
                cursor = Database.get_instance().repair_field_indexes(cursor)

        Run it to its end after upgrading from a release before escaping, and
        again after a rollback to one and a new upgrade, **from ``after=""``**:
        a cursor, or a note that it was done, saved before the rollback skips
        what the older release wrote. Nothing else needs to run first. Once it
        has run to its end, no old key holds an entry and counts are exact,
        except for what it cannot judge and leaves as it is: entries of a type
        or field this code does not index; entries whose row has no such field;
        an old-form list of more than 100 thousand ids (it is logged; a read of
        its value converts it where it fits the message, about 160 thousand ids
        in an update and 20 thousand in a query, and traps where it does not,
        as before); and an old-form list of a value made to end with ``|h`` and
        the digest of its own text, which reads as an entry of the value it
        extends (only such a value matches; ``find_by`` is not affected). A
        chain of old lists of ``V``, ``V|c``, ``V|c|c`` and so on deeper than
        about 500 raises ``RecursionError``: also a value made for it.

        Cost, measured on the IC: a key read about 0.1 million instructions; an
        entry of a value with a ``|`` that is right about 0.73 million (its row
        is loaded); an old entry moved about 2 million, so a call of 500 moves
        is about 1 billion; and about 0.58 million for each distinct key length
        in the whole map that a call walks past (only the lengths at which the
        map holds a key are visited). A call is O(``limit`` + those lengths). A
        list it converts counts its ids against ``limit``, and one longer than
        ``limit`` is converted whole, alone in its call, at about 245 thousand
        instructions an id: a map with limited value size holds at most about
        10 thousand ids in a list, but a realm's is not limited (a list of
        127,000 ids is on record: about 31 billion; it is over the cap, so the
        repair leaves it, and a read of its value in an update converts it).

        Returns:
            The ``after`` of the call that continues, None when the walk is done.
        """
        if limit < 1:
            raise ValueError("limit must be at least 1")
        if after and after[:4] not in _FI_FAMILIES:
            raise ValueError("after is not a cursor of repair_field_indexes")
        seen, last = 0, after
        for start, end in self._index_ranges(after):
            # One more than is left: a range starts at its last key. A range
            # with more keys than that fills the call, and ends it below
            wanted = limit - seen + 1
            for key, value in self._scan(start, end, wanted):
                if key == last:
                    continue
                cost = _old_list_size(key, value) or 1
                if seen and seen + cost > limit:
                    return last
                self._repair_entry(key, value)
                last, seen = key, seen + cost
            if seen >= limit:
                return last
        return None

    # ── Sorted indexes ─────────────────────────────────────────────────────────
    #
    # A sorted index keeps one entry per entity whose key starts with the
    # entity's sort values, so the storage's own key order is the sort order
    # and a "top N" is one range read:
    #
    #   _si:{Type}:{fields}:a|{values}{id}  -> entity id   lowest first
    #   _si:{Type}:{fields}:d|{values}{id}  -> entity id   highest first
    #   _sb:{Type}:{fields}:{entity id}     -> {values}    what is indexed now
    #
    # {values} is each field's value as _SORT_WIDTH digits (Entity encodes
    # them), {id} the id in _SORT_ID_WIDTH characters. Every key of one index
    # and direction has the same length, which keeps them contiguous in key
    # order (storage_sort_key). The "d" entry stores every digit inverted, so
    # ascending key order is descending value order; ties go to the higher id.
    #
    # The back pointer (_sb) lets an update or a rebuild remove the stale
    # entry without knowing the old value, so an index survives data
    # migrations and type changes.

    def _si_prefix(self, type_name: str, index: str, descending: bool) -> str:
        return f"_si:{type_name}:{index}:{'d' if descending else 'a'}|"

    @staticmethod
    def _si_id(entity_id: str, descending: bool) -> str:
        if _is_sequential_id(entity_id) and len(entity_id) < _SORT_ID_WIDTH:
            digits = entity_id.zfill(_SORT_ID_WIDTH - 1)
            return "n" + (digits.translate(_INVERT) if descending else digits)
        digest = hashlib.sha256(entity_id.encode("utf-8")).hexdigest()
        return "h" + digest[: _SORT_ID_WIDTH - 1]

    def sorted_index_set(
        self, type_name: str, index: str, entity_id: str, values: Optional[str]
    ) -> None:
        """Index ``entity_id`` under ``values`` (encoded sort values), or drop
        it when ``values`` is None. A no-op when nothing changed."""
        back = f"_sb:{type_name}:{index}:{entity_id}"
        old = self._db_storage.get(back)
        if old == values:
            return
        storage = self._db_storage
        for descending in (False, True):
            prefix = self._si_prefix(type_name, index, descending)
            ident = self._si_id(entity_id, descending)
            if old is not None:
                key = prefix + (old.translate(_INVERT) if descending else old) + ident
                if storage.get(key) is not None:
                    storage.remove(key)
            if values is not None:
                key = prefix + (values.translate(_INVERT) if descending else values)
                storage.insert(key + ident, entity_id)
        if values is None:
            storage.remove(back)
        else:
            storage.insert(back, values)

    def sorted_index_scan(
        self,
        type_name: str,
        index: str,
        fields: int,
        descending: bool,
        start: Optional[str],
        limit: int,
    ) -> List[Tuple[str, str]]:
        """Up to ``limit`` (key, entity id) pairs of an index in sort order,
        from key ``start`` (inclusive; None for the first)."""
        prefix = self._si_prefix(type_name, index, descending)
        width = _SORT_WIDTH * fields + _SORT_ID_WIDTH
        if start is None:
            start = prefix + "0" * width
        elif not start.startswith(prefix) or len(start) != len(prefix) + width:
            raise ValueError("cursor does not belong to this sorted index")
        return self._scan(start, prefix + "~" * width, limit)

    # Reverse counters (unidirectional relations)
    # Key format: "_rc:{parent_type}:{parent_id}:{relation_name}"
    # Value: integer count of related entities.
    #
    # Used by unidirectional ManyToMany relations: the owning side keeps its
    # normal (small) index while the target side maintains only an O(1)
    # counter instead of an ID array that would grow with every relation
    # (e.g. a UserProfile referenced by 100k users).

    def _rc_key(self, parent_type: str, parent_id: str, relation_name: str) -> str:
        return f"_rc:{parent_type}:{parent_id}:{relation_name}"

    def reverse_count_get(
        self, parent_type: str, parent_id: str, relation_name: str
    ) -> int:
        """Read the reverse counter for a parent entity's relation.

        Falls back to the size of a legacy reverse index (from when the
        relation was bidirectional) if no counter exists yet.
        """
        key = self._rc_key(parent_type, parent_id, relation_name)
        raw = self._db_storage.get(key)
        if raw is not None:
            return int(raw)
        return self.reverse_index_count(parent_type, parent_id, relation_name)

    def reverse_count_add(
        self, parent_type: str, parent_id: str, relation_name: str, delta: int
    ) -> None:
        """Adjust the reverse counter by delta (never below zero).

        On first write, seeds the counter from a legacy reverse index (if
        present) and drops it — lazy, exactly-once migration of relations
        converted from bidirectional to unidirectional.
        """
        key = self._rc_key(parent_type, parent_id, relation_name)
        raw = self._db_storage.get(key)
        if raw is not None:
            current = int(raw)
        else:
            current = self.reverse_index_count(parent_type, parent_id, relation_name)
            if current:
                self.reverse_index_delete(parent_type, parent_id, relation_name)
        self._db_storage.insert(key, str(max(0, current + delta)))

    def reverse_count_delete(
        self, parent_type: str, parent_id: str, relation_name: str
    ) -> None:
        """Delete a reverse counter entry (used when the parent is deleted)."""
        key = self._rc_key(parent_type, parent_id, relation_name)
        if self._db_storage.get(key) is not None:
            self._db_storage.remove(key)

    def reverse_count_names(self, type_name: str) -> List[str]:
        """Names of the reverse counters that entities of a type carry.

        These are the ``reverse_name`` of every unidirectional ManyToMany that
        a registered class declares towards ``type_name``; a deleted entity
        must delete its counters. Finding them reads every attribute of every
        registered class, which costs millions of IC instructions with a
        realm's 93 types, so the answer is kept until ``register_entity_type``
        changes the registry.
        """
        if self._reverse_count_names is None:
            self._reverse_count_names = self._find_reverse_count_names()
        return self._reverse_count_names.get(self._extract_class_name(type_name), [])

    def _find_reverse_count_names(self) -> Dict[str, List[str]]:
        """{target class name: reverse counter names}, from the registered classes."""
        from .properties import ManyToMany

        names: Dict[str, List[str]] = {}
        read = set()  # a base class is shared by many registered classes
        for entity_class in self._entity_types.values():
            if not isinstance(entity_class, type):
                continue
            for cls in entity_class.__mro__:
                if cls in read:
                    continue
                read.add(cls)
                for attr_name, attr in cls.__dict__.items():
                    if attr_name.startswith("_"):
                        continue
                    if not isinstance(attr, ManyToMany) or not attr.unidirectional:
                        continue
                    for target in attr._get_allowed_types():
                        reverse_names = names.setdefault(
                            self._extract_class_name(target), []
                        )
                        if attr.reverse_name not in reverse_names:
                            reverse_names.append(attr.reverse_name)
        return names

    def get_audit(
        self, id_from: Optional[int] = None, id_to: Optional[int] = None
    ) -> Dict[str, str]:
        """Get audit log entries between the specified IDs"""
        if not self._db_audit:
            return {}

        id_from = id_from or int(self._db_audit.get("_min_id"))
        id_to = id_to or int(self._db_audit.get("_max_id"))

        ret = {}
        for id in range(id_from, id_to):
            id_str = str(id)
            entry = self._db_audit.get(id_str)
            if entry:
                ret[id_str] = json.loads(entry)
        return ret
