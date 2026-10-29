"""Counting storage and database setup for the local scaling suite."""

import gc

import scaling_cases  # on sys.path via conftest.py

from ic_python_db import Database, Entity
from ic_python_db.storage import MemoryStorage

# Sizes every case runs at: N_LARGE is past scaling_cases.CAP
N_SMALL = 50
N_LARGE = scaling_cases.CAP + 500

# Bounded case name -> (Cost at N_SMALL, Cost at N_LARGE), printed by conftest
COSTS = {}

# Storage settings Casals runs with in production
PROD_MAX_KEY_SIZE = 256
PROD_MAX_VALUE_SIZE = 20000


class TrapError(Exception):
    """What a StableBTreeMap does on an oversized key or value: trap the call."""


class CountingStorage(MemoryStorage):
    """MemoryStorage that counts work and enforces StableBTreeMap's size limits.

    One unit of ``ops`` is one get, insert or remove, one range call, or one
    row returned by a range or a full-map iteration. ``bytes`` counts the key
    and value bytes moved. On the IC both drive the instruction count.
    """

    def __init__(self, max_key_size=None, max_value_size=None):
        super().__init__()
        self.max_key_size = max_key_size
        self.max_value_size = max_value_size
        self.reset()

    def reset(self):
        self.ops = 0
        self.writes = 0
        self.bytes = 0

    def _check(self, key, value):
        if self.max_key_size and len(key.encode()) > self.max_key_size:
            raise TrapError(f"key of {len(key.encode())} bytes: {key[:60]}...")
        if self.max_value_size and len(value.encode()) > self.max_value_size:
            raise TrapError(f"value of {len(value.encode())} bytes under {key[:60]}")

    def insert(self, key, value):
        self._check(key, value)
        self.ops += 1
        self.writes += 1
        self.bytes += len(key) + len(value)
        super().insert(key, value)

    def get(self, key):
        self.ops += 1
        value = super().get(key)
        self.bytes += len(key) + (len(value) if value else 0)
        return value

    def remove(self, key):
        self.ops += 1
        self.writes += 1
        super().remove(key)

    def __contains__(self, key):
        self.ops += 1
        return super().__contains__(key)

    def range(self, start, end=None, limit=1000):
        rows = super().range(start, end, limit)
        self.ops += 1 + len(rows)
        self.bytes += sum(len(k) + len(v) for k, v in rows)
        return rows

    def keys(self):
        for key in list(super().keys()):
            self.ops += 1
            yield key

    def items(self):
        for key, value in list(super().items()):
            self.ops += 1
            self.bytes += len(key) + len(value)
            yield key, value


def fresh_db(max_key_size=None, max_value_size=None, audit=False):
    """Replace the Database singleton with an empty one over CountingStorage."""
    Database._instance = None
    storage = CountingStorage(max_key_size, max_value_size)
    db = Database.init(audit_enabled=audit, db_storage=storage)
    for cls in scaling_cases.MODEL:
        db.register_entity_type(cls, cls.get_full_type_name())
    Entity._context.clear()
    return db, storage


class Cost:
    def __init__(self, storage):
        self.ops = storage.ops
        self.writes = storage.writes
        self.bytes = storage.bytes

    def __repr__(self):
        return f"{self.ops} ops, {self.bytes} bytes, {self.writes} writes"


def measure(storage, fn, *args):
    storage.reset()
    fn(*args)
    return Cost(storage)


def live_entities():
    """Entity instances still reachable in the heap."""
    gc.collect()
    return sum(1 for obj in gc.get_objects() if isinstance(obj, Entity))
