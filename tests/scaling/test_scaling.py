"""Local scaling suite: every public operation, checked for growth with data.

Costs are storage-operation and byte counts (see harness.CountingStorage),
which track IC instruction counts without needing a replica. The canister
suite (tests/entrypoint_scaling.py) measures the same cases in instructions.

Cases whose failure is tracked by an open issue are strict xfails: when a
fix lands they pass, the xfail fails, and the marker goes with the fix.
"""

import gc

import pytest
import scaling_cases as sc
from harness import (
    COSTS,
    N_LARGE,
    N_SMALL,
    PROD_MAX_KEY_SIZE,
    PROD_MAX_VALUE_SIZE,
    TrapError,
    fresh_db,
    live_entities,
    measure,
)
from scaling_cases import ADMIN, BOUNDED, CAP, CASES, GUARDED, KNOWN

import ic_python_db
from ic_python_db import Database, Entity, String
from ic_python_db import properties as props

# A bounded operation may cost this much more at N_LARGE than at N_SMALL
OPS_FACTOR, OPS_SLACK = 1.25, 10
BYTES_FACTOR, BYTES_SLACK = 1.25, 2000


def _params(kind):
    params = []
    for case in CASES:
        if case.kind != kind:
            continue
        marks = []
        if case.known:
            issue, reason = KNOWN[case.known]
            marks.append(pytest.mark.xfail(strict=True, reason=f"{issue}: {reason}"))
        params.append(pytest.param(case, id=case.name, marks=marks))
    return params


def _known(key):
    issue, reason = KNOWN[key]
    return pytest.mark.xfail(strict=True, reason=f"{issue}: {reason}")


def _build(case, n):
    """A fresh database holding the case's world at size n, nothing in memory."""
    _, storage = fresh_db()
    world = case.world.build(n)
    sc.cold()
    gc.collect()
    return world, storage


def _run(case, n):
    world, storage = _build(case, n)
    return measure(storage, case.op, world)


# ── Growth ──────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("case", _params(BOUNDED))
def test_bounded(case):
    """Costs about the same with N_SMALL or N_LARGE items in play."""
    small, large = _run(case, N_SMALL), _run(case, N_LARGE)
    COSTS[case.name] = (small, large)
    if case.readonly:
        assert small.writes == 0 and large.writes == 0, "a read wrote to storage"
    assert large.ops <= small.ops * OPS_FACTOR + OPS_SLACK, (small, large)
    assert large.bytes <= small.bytes * BYTES_FACTOR + BYTES_SLACK, (small, large)


@pytest.mark.parametrize("case", _params(GUARDED))
def test_guarded(case):
    """Works on a small collection; past CAP raises TooLargeError, never runs on."""
    too_large = getattr(ic_python_db, "TooLargeError", None)
    assert too_large is not None, "ic_python_db.TooLargeError does not exist"
    _run(case, N_SMALL)
    world, _ = _build(case, N_LARGE)
    with pytest.raises(too_large):
        case.op(world)


@pytest.mark.parametrize("case", _params(ADMIN))
def test_admin_has_paged_alternative(case):
    cls_name, method = case.alt.split(".")
    cls = {"Database": Database, "Entity": Entity}[cls_name]
    assert callable(getattr(cls, method, None)), f"{case.alt} does not exist"


# ── Reads stay reads ────────────────────────────────────────────────────────


@_known("20")
def test_loading_old_version_does_not_write():
    """A query cannot persist writes, so a load must not depend on one."""
    _, storage = fresh_db()
    for i in range(10):
        sc.ScVersioned(title=f"v{i}", rank=i)
    sc.ScVersioned.__version__ = 2
    try:
        sc.cold()
        cost = measure(storage, lambda: sc.ScVersioned.load_some(1, 10))
    finally:
        sc.ScVersioned.__version__ = 1
    assert cost.writes == 0, cost


# ── Heap ────────────────────────────────────────────────────────────────────


@_known("heap")
def test_heap_does_not_keep_loaded_entities():
    """The heap survives between calls, so entities nobody holds must go."""
    fresh_db()
    sc.world_items.build(N_LARGE)
    sc.cold()
    for i in range(1, N_LARGE + 1):
        sc.ScItem.load(str(i))
    assert live_entities() <= 50


# ── Size limits (production storage settings) ───────────────────────────────


def _prod_db():
    return fresh_db(PROD_MAX_KEY_SIZE, PROD_MAX_VALUE_SIZE)


@_known("18")
def test_many_children_fit_in_production_limits():
    _prod_db()
    sc.world_owner.build(3000)


@_known("sizes")
def test_long_indexed_value_raises_a_clear_error():
    _prod_db()
    with pytest.raises(ValueError):
        sc.ScItem(title="t", color="x" * 300)


@_known("sizes")
def test_long_alias_raises_a_clear_error():
    _prod_db()
    with pytest.raises(ValueError):
        sc.ScOwner(name="x" * 300, kind="k")


@_known("sizes")
def test_oversized_row_raises_a_clear_error():
    _prod_db()
    with pytest.raises(ValueError):
        sc.ScItem(title="x" * (PROD_MAX_VALUE_SIZE + 1), color="red")


@_known("sizes")
def test_large_schema_raises_a_clear_error():
    db, _ = _prod_db()
    for i in range(150):
        fields = {f"field_{j}": String(max_length=100) for j in range(12)}
        type(f"ScSchema{i}", (Entity,), fields)
    with pytest.raises(ValueError):
        db.save_schema()


def test_harness_traps_like_stable_btree_map():
    """Guards the guards: the counting storage really enforces the limits."""
    _prod_db()
    with pytest.raises(TrapError):
        Entity.db()._db_storage.insert("k" * (PROD_MAX_KEY_SIZE + 1), "v")


# ── Completeness ────────────────────────────────────────────────────────────

# Public operations without a case, and why they need none
EXEMPT = {
    "Database.get_instance": "in-memory bookkeeping",
    "Database.init": "in-memory bookkeeping",
    "Database.as_user": "in-memory bookkeeping",
    "Database.register_entity": "in-memory bookkeeping",
    "Database.get_entity": "in-memory bookkeeping",
    "Database.clear_registry": "in-memory bookkeeping",
    "Database.unregister_entity": "in-memory bookkeeping",
    "Database.register_entity_type": "in-memory bookkeeping",
    "Database.is_subclass": "in-memory bookkeeping",
    "Database.update": "one read and one write of a single row",
    "Database.build_schema_from_entities": "O(types x fields); size: test_large_schema_*",
    "Database.get_schema_hash": "one read",
    "Database.save_schema": "O(types x fields); size: test_large_schema_*",
    "Database.check_upgrade_compatibility": "O(types x fields); size: test_large_schema_*",
    "Database.field_index_get": "low-level: the whole set by contract; Entity pages",
    "Database.clear_page": "bounded by its limit argument (#19)",
    "Database.dump_page": "bounded by its limit argument (#19)",
    "Database.raw_dump_page": "bounded by its limit argument (#19)",
    "Database.get_audit_page": "bounded by its limit argument",
    "Entity.db": "in-memory bookkeeping",
    "Entity.get_full_type_name": "in-memory bookkeeping",
    "Entity.migrate": "user hook, run by load (test_loading_old_version_*)",
    "Entity.deserialize": "cost proportional to its input",
    "OneToMany.__set__": "always raises: set the ManyToOne side instead",
}

_RELATIONS = ("ManyToOne", "OneToMany", "OneToOne", "ManyToMany")
_VIEW_DUNDERS = ("__iter__", "__len__", "__bool__", "__contains__", "__getitem__")


def _public_api():
    names = set()
    for cls in (Database, Entity):
        for name, value in vars(cls).items():
            if isinstance(value, (classmethod, staticmethod)):
                value = value.__func__
            if callable(value) and (
                not name.startswith("_") or name == "__class_getitem__"
            ):
                names.add(f"{cls.__name__}.{name}")
    for rel in _RELATIONS:
        for name in ("__get__", "__set__"):
            if name in vars(getattr(props, rel)):
                names.add(f"{rel}.{name}")
    # The collection a to-many getter returns, whatever its class is called
    for view in ("RelationView", "ManyToManyList"):
        cls = getattr(props, view, None)
        if cls is None:
            continue
        for name, value in vars(cls).items():
            if callable(value) and (not name.startswith("_") or name in _VIEW_DUNDERS):
                names.add(f"RelationView.{name}")
    return names


def test_every_public_operation_has_a_case():
    """A new public method without a scaling case fails CI."""
    covered = {name for case in CASES for name in case.covers}
    missing = sorted(_public_api() - covered - set(EXEMPT))
    assert not missing, f"no scaling case covers: {missing}"
