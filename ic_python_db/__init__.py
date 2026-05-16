"""
IC Python DB - A lightweight key-value database with entity relationships and audit logging
"""

from .constants import ACTION_CREATE, ACTION_DELETE, ACTION_MODIFY
from .db_engine import Database
from .entity import Entity
from .mixins import TimestampedMixin
from .properties import (
    Boolean,
    Float,
    Integer,
    ManyToMany,
    ManyToOne,
    OneToMany,
    OneToOne,
    String,
)
from .schema import SchemaIncompatibleError, build_schema, diff_schemas, schema_hash
from .storage import MemoryStorage, Storage
from .system_time import SystemTime

__version__ = "0.8.0"
__all__ = [
    "Database",
    "Entity",
    "Storage",
    "MemoryStorage",
    "TimestampedMixin",
    "String",
    "Integer",
    "Float",
    "Boolean",
    "OneToOne",
    "OneToMany",
    "ManyToOne",
    "ManyToMany",
    "SystemTime",
    "ACTION_CREATE",
    "ACTION_MODIFY",
    "ACTION_DELETE",
    "SchemaIncompatibleError",
    "build_schema",
    "diff_schemas",
    "schema_hash",
]
