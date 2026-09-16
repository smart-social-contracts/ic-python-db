"""
Storage backends for IC Python DB
"""

from abc import ABC, abstractmethod
from typing import Dict, Iterator, List, Optional, Tuple


def storage_sort_key(key: str) -> Tuple[int, bytes]:
    """Sort key reproducing the on-chain order of ``str`` keys.

    Basilisk's ``StableBTreeMap`` stores a ``str`` key as
    ``tag + 4-byte big-endian length + utf-8``, so the B-tree orders keys by
    byte length first and bytewise second: ``"T@9" < "T@10" < "T@2x"``.
    ``MemoryStorage.range`` uses this so local tests see exactly the order a
    canister will.
    """
    raw = key.encode("utf-8")
    return (len(raw), raw)


def supports_range(storage) -> bool:
    """True if ``storage`` offers ``range(start, end, limit)``.

    Basilisk's ``StableBTreeMap`` (>= the version that added ``range``) and
    ``MemoryStorage`` do; older CDK builds and simple dict-backed test
    doubles don't, and callers fall back to key probing.
    """
    return callable(getattr(storage, "range", None))


class Storage(ABC):
    """Abstract base class for storage backends

    ``range`` is optional: backends that can walk keys in order should
    implement it (see ``storage_sort_key`` for the order that is expected);
    callers must check ``supports_range`` and fall back to ``get`` probing.
    """

    @abstractmethod
    def insert(self, key: str, value: str) -> None:
        """Insert a key-value pair into storage"""
        raise NotImplementedError

    @abstractmethod
    def get(self, key: str) -> Optional[str]:
        """Retrieve a value by key"""
        raise NotImplementedError

    @abstractmethod
    def remove(self, key: str) -> None:
        """Remove a key-value pair from storage"""
        raise NotImplementedError

    @abstractmethod
    def items(self) -> Iterator[Tuple[str, str]]:
        """Return all items in storage"""
        raise NotImplementedError

    @abstractmethod
    def __contains__(self, key: str) -> bool:
        """Check if key exists in storage"""
        raise NotImplementedError

    @abstractmethod
    def keys(self) -> Iterator[str]:
        """Return all keys in storage"""
        raise NotImplementedError


class MemoryStorage(Storage):
    """In-memory storage implementation using Python dictionary"""

    def __init__(self):
        self._data: Dict[str, str] = {}

    def insert(self, key: str, value: str) -> None:
        self._data[key] = value

    def get(self, key: str) -> Optional[str]:
        return self._data.get(key)

    def remove(self, key: str) -> None:
        if key in self._data:
            del self._data[key]
        else:
            raise KeyError(f"Key '{key}' not found in storage")

    def items(self) -> Iterator[Tuple[str, str]]:
        return iter(self._data.items())

    def __contains__(self, key: str) -> bool:
        return key in self._data

    def keys(self) -> Iterator[str]:
        """Return all keys in storage"""
        return iter(self._data.keys())

    def range(
        self, start: str, end: Optional[str] = None, limit: int = 1000
    ) -> List[Tuple[str, str]]:
        """Ordered page of ``(key, value)`` with ``start <= key < end``.

        Same contract as Basilisk's ``StableBTreeMap.range``: half-open,
        ``end=None`` means unbounded, keys ordered by ``storage_sort_key``.
        O(n log n) here, which is fine for an in-memory test backend.
        """
        if limit < 0:
            raise ValueError("limit must be >= 0")
        lo = storage_sort_key(start)
        hi = None if end is None else storage_sort_key(end)
        selected = [
            k
            for k in self._data
            if lo <= storage_sort_key(k) and (hi is None or storage_sort_key(k) < hi)
        ]
        selected.sort(key=storage_sort_key)
        return [(k, self._data[k]) for k in selected[:limit]]
