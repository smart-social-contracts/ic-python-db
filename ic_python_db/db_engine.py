"""
Core database engine implementation
"""

import hashlib
import json
import time
import weakref
from typing import Any, Dict, List, Optional, Tuple

from ic_python_logging import get_logger

from .storage import MemoryStorage, Storage, storage_sort_key, supports_range

logger = get_logger(__name__)

_DIGITS = frozenset("0123456789")
_HEX = frozenset("0123456789abcdef")
# Posting-list entry suffix widths (see "Posting lists" below)
_ID_WIDTH = 12
_HASH_WIDTH = 24
_SCAN_PAGE = 500
_BUCKETS = (("n", _ID_WIDTH, ":"), ("h", _HASH_WIDTH, "g"))
# Purge queue of deleted index sets (see _ix_clear): fixed-width positions
# keep its entries contiguous in key order
_PQ = "_pq@"
_PQ_HEAD = "_pq:head"
_PQ_TAIL = "_pq:tail"
# Entries of deleted sets each index write removes on the side
_PURGE_PER_WRITE = 20


def _is_sequential_id(id_str: str) -> bool:
    """True for canonical sequential ids: ASCII digits, no leading zero."""
    return bool(id_str) and id_str[0] != "0" and all(c in _DIGITS for c in id_str)


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
        # Identity map: {(type_name, entity_id): entity}. Weak, so an entity
        # lives only as long as some caller holds it, and its entry goes with it.
        self._entity_registry = weakref.WeakValueDictionary()
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

    def register_entity(self, entity_instance):
        """Register an entity instance in the identity map."""
        key = (entity_instance._type, entity_instance._id)
        self._entity_registry[key] = entity_instance

    def get_entity(self, type_name: str, entity_id: str):
        """Get entity from registry if it exists."""
        return self._entity_registry.get((type_name, entity_id))

    def clear_registry(self):
        """Clear the entity registry (useful for testing)."""
        self._entity_registry.clear()

    def unregister_entity(self, type_name: str, entity_id: str):
        """Remove an entity from the registry (used when deleting)."""
        self._entity_registry.pop((type_name, entity_id), None)

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
        self._entity_types[type_name] = type_obj

        # For backward compatibility, also register under class name if:
        # 1. No namespace (type_name == class name) - allows non-namespaced lookups, OR
        # 2. Class name slot is available (no collision) - allows simple name lookups
        # This dual registration enables both "User" and "app::User" lookups while
        # preventing collisions when multiple namespaced entities share a class name
        if (
            type_name == type_obj.__name__
            or type_obj.__name__ not in self._entity_types
        ):
            self._entity_types[type_obj.__name__] = type_obj
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
    # Before 0.13 a base key held the whole set as one JSON array, which hit
    # the value-size limit at ~10k ids. Such an array is converted to entries
    # the first time its base is touched (see _ix_count).

    def _scan(self, start: str, end: str, limit: int) -> List[Tuple[str, str]]:
        if supports_range(self._db_storage):
            return self._db_storage.range(start, end, limit)
        lo, hi = storage_sort_key(start), storage_sort_key(end)
        keys = sorted(
            (k for k in self._db_storage.keys() if lo <= storage_sort_key(k) < hi),
            key=storage_sort_key,
        )
        return [(k, self._db_storage.get(k)) for k in keys[:limit]]

    @staticmethod
    def _ix_entry_key(base: str, entity_id: str) -> str:
        if _is_sequential_id(entity_id) and len(entity_id) <= _ID_WIDTH:
            return f"{base}|n{entity_id.zfill(_ID_WIDTH)}"
        digest = hashlib.sha256(entity_id.encode("utf-8")).hexdigest()
        return f"{base}|h{digest[:_HASH_WIDTH]}"

    def _ix_count(self, base: str) -> int:
        raw = self._db_storage.get(f"{base}|c")
        if raw is not None:
            return int(raw)
        legacy = self._db_storage.get(base)
        if legacy is None:
            return 0
        try:
            ids = json.loads(legacy)
        except ValueError:
            return 0
        if not isinstance(ids, list):
            return 0
        self._db_storage.remove(base)
        unique = {str(i) for i in ids}
        for entity_id in unique:
            self._db_storage.insert(self._ix_entry_key(base, entity_id), entity_id)
        if unique:
            self._db_storage.insert(f"{base}|c", str(len(unique)))
        return len(unique)

    def _ix_add(self, base: str, entity_id: str) -> None:
        self._purge_some()
        count = self._ix_count(base)
        if count == 0:
            self._ix_revive(base)
        key = self._ix_entry_key(base, entity_id)
        if self._db_storage.get(key) is not None:
            return
        self._db_storage.insert(key, entity_id)
        self._db_storage.insert(f"{base}|c", str(count + 1))

    def _ix_remove(self, base: str, entity_id: str) -> None:
        self._purge_some()
        count = self._ix_count(base)
        if count == 0:
            return
        key = self._ix_entry_key(base, entity_id)
        if self._db_storage.get(key) is None:
            return
        self._db_storage.remove(key)
        if count > 1:
            self._db_storage.insert(f"{base}|c", str(count - 1))
        else:
            self._db_storage.remove(f"{base}|c")

    def _ix_contains(self, base: str, entity_id: str) -> bool:
        if self._ix_count(base) == 0:
            return False
        return self._db_storage.get(self._ix_entry_key(base, entity_id)) is not None

    def _ix_page(self, base: str, from_id: int, count: int) -> List[str]:
        """Up to ``count`` sequential ids >= ``from_id``, ascending."""
        if from_id < 1:
            raise ValueError("from_id must be at least 1")
        if count < 1:
            raise ValueError("count must be at least 1")
        if self._ix_count(base) == 0 or len(str(from_id)) > _ID_WIDTH:
            return []
        start = f"{base}|n{str(from_id).zfill(_ID_WIDTH)}"
        end = f"{base}|n{':' * _ID_WIDTH}"
        cut = len(base) + 2
        # A key of another base can fall inside the range only if its suffix
        # is not all digits (e.g. value "x|n0;..." next to value "x").
        return [
            value
            for key, value in self._scan(start, end, count)
            if all(c in _DIGITS for c in key[cut:])
        ]

    def _ix_bucket(
        self, base: str, tag: str, width: int, top: str, page_size: int = _SCAN_PAGE
    ):
        start = f"{base}|{tag}{'0' * width}"
        end = f"{base}|{tag}{top * width}"
        cut = len(base) + 2
        valid = _DIGITS if tag == "n" else _HEX
        while True:
            page = self._scan(start, end, page_size)
            for key, value in page:
                if all(c in valid for c in key[cut:]):
                    yield key, value
            if len(page) < page_size:
                return
            # Smallest same-length key after the last one: append the lowest
            # char and drop the last, i.e. bump by one in the key order.
            last = page[-1][0]
            start = last[:-1] + chr(ord(last[-1]) + 1)

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
        position = self._db_storage.get(f"{base}|d")
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

    def _fi_key(self, type_name: str, field_name: str, value: str) -> str:
        return f"_fi:{type_name}:{field_name}:{value}"

    def field_index_get(self, type_name: str, field_name: str, value: str) -> List[str]:
        """All ids of entities whose ``field_name`` equals ``value``
        (O(matches); prefer ``field_index_page``)."""
        return self._ix_all(self._fi_key(type_name, field_name, value))

    def field_index_page(
        self, type_name: str, field_name: str, value: str, from_id: int, count: int
    ) -> List[str]:
        """Up to ``count`` sequential ids >= ``from_id`` whose ``field_name``
        equals ``value``, ascending. One range read."""
        return self._ix_page(self._fi_key(type_name, field_name, value), from_id, count)

    def field_index_count(self, type_name: str, field_name: str, value: str) -> int:
        """Number of entities whose ``field_name`` equals ``value`` (one read)."""
        return self._ix_count(self._fi_key(type_name, field_name, value))

    def field_index_add(
        self, type_name: str, field_name: str, value: str, entity_id: str
    ) -> None:
        """Add an entity ID to the index entry for ``value``."""
        self._ix_add(self._fi_key(type_name, field_name, value), entity_id)

    def field_index_remove(
        self, type_name: str, field_name: str, value: str, entity_id: str
    ) -> None:
        """Remove an entity ID from the index entry for ``value``."""
        self._ix_remove(self._fi_key(type_name, field_name, value), entity_id)

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
