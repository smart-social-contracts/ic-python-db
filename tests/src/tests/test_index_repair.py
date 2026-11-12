"""Field-index entries filed before escaping: still found, moved, repaired.

A release before escaping files a value with a ``|`` under its own text in
``_fi``, and knows no ``_fx``. ``AsTheReleaseBefore`` runs code the way it did,
so the entries it leaves, and what it leaves wrong after a rollback, are the
real thing.

(No ``re`` here: Basilisk's ``re`` is an empty module.)
"""

import random

from tester import Tester

from ic_python_db import *

FIELDS = ("dept", "kind")
VALUES = [
    "V1",
    "V1|n0000000000",
    "a%7Cc",
    "a|c",
    "100%",
    "a|b",
    "x|n0000000000|c",
    "plain",
    "",
    "q|" + "y" * 190,  # a key of 230 bytes
]
CUSTOM_IDS = ["abc", '["7"]', "x:y", "é", "[1]", "a|b"]


class IrNote(Entity):
    dept = String(max_length=300, indexed=True)
    kind = String(max_length=300, indexed=True)


class AsTheReleaseBefore:
    """``with`` it, the index keys are those of the release before escaping."""

    names = ("_fi_key", "_fi_old_key")

    def __enter__(self):
        self.saved = {name: Database.__dict__[name] for name in self.names}
        Database._fi_key = lambda self, t, f, v: f"_fi:{t}:{f}:{v}"
        Database._fi_old_key = lambda self, t, f, v: None

    def __exit__(self, *exception):
        for name, function in self.saved.items():
            setattr(Database, name, function)


class WithoutOldKeys:
    """``with`` it, no call looks at an old key: what the code is once the
    repair has run to its end."""

    def __enter__(self):
        self.saved = Database.__dict__["_fi_old_key"]
        Database._fi_old_key = lambda self, t, f, v: None

    def __exit__(self, *exception):
        Database._fi_old_key = self.saved


def _is_sequential(entity_id):
    return entity_id.isdigit() and entity_id[0] != "0" and len(entity_id) <= 12


def _storage(db):
    return dict(db._db_storage.items())


def _old_keys_left(db):
    """Keys of IrNote's indexes under a value with a ``|`` in ``_fi``."""
    left = []
    for key in db._db_storage.keys():
        if key.startswith("_fi:IrNote:"):
            if "|" in key.split(":", 3)[3].rsplit("|", 1)[0]:
                left.append(key)
    return left


def _check(truth, values=VALUES):
    """Every read of the lists is what the entities say, for every value."""
    db = Database.get_instance()
    for field in FIELDS:
        for value in values:
            want = {i for i, row in truth.items() if row[field] == value}
            sequential = sorted(int(i) for i in want if _is_sequential(i))
            assert db.field_index_count("IrNote", field, value) == len(want), (
                field,
                value,
            )
            got = db.field_index_get("IrNote", field, value)
            assert sorted(got) == sorted(want), (field, value, got, want)
            for count in (3, 50):
                found, start = [], 1
                while True:
                    page = db.field_index_page("IrNote", field, value, start, count)
                    found.extend(int(i) for i in page)
                    if len(page) < count:
                        break
                    start = found[-1] + 1
                assert found == sequential, (field, value, count)


def _create(rng, truth, custom=False):
    row = {"dept": rng.choice(VALUES), "kind": rng.choice(VALUES)}
    if custom:
        free = [i for i in CUSTOM_IDS if i not in truth]
        if free:
            truth[IrNote(_id=rng.choice(free), **row)._id] = row
            return
    truth[IrNote(**row)._id] = row


def _write_some(rng, truth, count):
    """Create, change and delete entities; ``truth`` says what they hold."""
    for _ in range(count):
        live, roll = list(truth), rng.random()
        if roll < 0.35 or not live:
            _create(rng, truth, custom=rng.random() < 0.1)
        elif roll < 0.85:
            entity_id, field = rng.choice(live), rng.choice(FIELDS)
            value = rng.choice(VALUES)
            setattr(IrNote.load(entity_id), field, value)
            truth[entity_id] = dict(truth[entity_id], **{field: value})
        else:
            entity_id = rng.choice(live)
            IrNote.load(entity_id).delete()
            del truth[entity_id]


def _old_world():
    """The entities of a realm on the release before escaping: some with a
    ``|`` in a value, some with a custom id, all indexed the old way."""
    rng = random.Random(5)
    truth = {}
    with AsTheReleaseBefore():
        for i in range(60):
            _create(rng, truth)
        for i in range(len(CUSTOM_IDS)):
            _create(rng, truth, custom=True)
    return truth


def _repair(db, limit=500):
    calls, cursor = 0, ""
    while cursor is not None:
        cursor = db.repair_field_indexes(cursor, limit)
        calls += 1
        assert calls < 10000
    return calls


def _rebuild(batch=7):
    cursor = 1
    while cursor is not None:
        cursor = IrNote.rebuild_field_indexes(from_id=cursor, batch=batch)


class TestIndexRepair:
    def setUp(self):
        Database.get_instance().clear()
        Entity._context.clear()

    # ── Entries filed the old way ────────────────────────────────────────────

    def test_entries_filed_the_old_way_are_still_found(self):
        _check(_old_world())

    def test_only_a_value_with_a_bar_has_an_old_key(self):
        db = Database.get_instance()
        assert db._fi_old_key("T", "f", "100%") is None
        assert db._fi_old_key("T", "f", "a%7Cc") is None
        assert db._fi_old_key("T", "f", "a|b") == "_fi:T:f:a|b"

    def test_a_percent_value_and_the_escaped_form_of_a_bar_value_do_not_meet(self):
        db = Database.get_instance()
        db.field_index_add("IrNote", "dept", "a%7Cc", "1")
        db.field_index_add("IrNote", "dept", "a|c", "2")
        assert db.field_index_get("IrNote", "dept", "a|c") == ["2"]
        assert db.field_index_get("IrNote", "dept", "a%7Cc") == ["1"]
        assert db.field_index_count("IrNote", "dept", "a|c") == 1

    def test_adding_or_removing_an_id_takes_it_off_its_old_list(self):
        db = Database.get_instance()
        db._ix_add("_fi:IrNote:dept:a|b", "1")
        db._ix_add("_fi:IrNote:dept:a|b", "2")
        db.field_index_add("IrNote", "dept", "a|b", "1")
        assert db._ix_count("_fi:IrNote:dept:a|b") == 1
        assert db.field_index_count("IrNote", "dept", "a|b") == 2
        db.field_index_remove("IrNote", "dept", "a|b", "2")
        assert db.field_index_get("IrNote", "dept", "a|b") == ["1"]
        db.field_index_remove("IrNote", "dept", "a|b", "1")
        assert db.field_index_count("IrNote", "dept", "a|b") == 0
        assert not [k for k in _storage(db) if k.startswith(("_fi:", "_fx:"))]

    def test_a_page_merges_the_old_list_and_the_new(self):
        db = Database.get_instance()
        for i in (1, 4, 100):
            db._ix_add("_fi:IrNote:dept:a|b", str(i))
        for i in (2, 3, 101):
            db.field_index_add("IrNote", "dept", "a|b", str(i))
        page = db.field_index_page("IrNote", "dept", "a|b", 1, 4)
        assert page == ["1", "2", "3", "4"]
        page = db.field_index_page("IrNote", "dept", "a|b", 4, 3)
        assert page == ["4", "100", "101"]
        got = db.field_index_get("IrNote", "dept", "a|b")
        assert got == ["1", "2", "3", "4", "100", "101"]

    def test_an_id_on_both_lists_is_listed_once(self):
        db = Database.get_instance()
        db._ix_add("_fi:IrNote:dept:a|b", "2")
        db._ix_add("_fi:IrNote:dept:a|b", "abc")
        db._ix_add("_fx:IrNote:dept:a%7Cb", "2")
        db._ix_add("_fx:IrNote:dept:a%7Cb", "abc")
        assert db.field_index_get("IrNote", "dept", "a|b") == ["2", "abc"]
        assert db.field_index_page("IrNote", "dept", "a|b", 1, 5) == ["2"]

    # ── Entity.rebuild_field_indexes ─────────────────────────────────────────

    def test_readers_are_right_at_every_step_of_the_rebuild(self):
        truth = _old_world()
        cursor, rounds = 1, 0
        while cursor is not None:
            _check(truth)
            cursor = IrNote.rebuild_field_indexes(from_id=cursor, batch=7)
            rounds += 1
            assert rounds < 100
        _check(truth)
        assert rounds == 9  # 60 entities with sequential ids, 7 at a time
        # It reaches sequential ids: custom ids are still under their old keys
        left = _old_keys_left(Database.get_instance())
        sequential = [k for k in left if k[-14:-12] == "|n" and k[-12:].isdigit()]
        assert left and not sequential, sequential

    def test_a_value_written_during_the_rebuild_is_listed_once(self):
        truth = _old_world()
        IrNote.rebuild_field_indexes(from_id=1, batch=30)
        for entity_id in ("2", "50"):  # one the rebuild has reached, one not yet
            IrNote.load(entity_id).dept = "a|c"
            truth[entity_id] = dict(truth[entity_id], dept="a|c")
        IrNote.load("51").delete()
        del truth["51"]
        _check(truth)

    def test_rebuild_field_indexes_does_every_indexed_field(self):
        notes = [IrNote(dept="a|b", kind="x|y") for _ in range(5)]
        with AsTheReleaseBefore():
            for note in notes:  # filed the old way
                note.dept, note.kind = "", ""
                note.dept, note.kind = "a|b", "x|y"
        db = Database.get_instance()
        assert db._ix_count("_fi:IrNote:kind:x|y") == 5
        assert IrNote.rebuild_field_indexes(batch=5) is None
        assert not _old_keys_left(db)
        assert IrNote.count_by("dept", "a|b") == 5
        assert IrNote.count_by("kind", "x|y") == 5
        assert IrNote.rebuild_field_indexes(batch=5) is None  # again: no change
        assert IrNote.count_by("dept", "a|b") == 5

    def test_rebuild_field_indexes_validates_its_arguments(self):
        for arguments in ({"from_id": 0}, {"batch": 0}):
            try:
                IrNote.rebuild_field_indexes(**arguments)
            except ValueError:
                continue
            raise AssertionError(f"{arguments} was accepted")
        assert IrNote.rebuild_field_indexes() is None  # no entities

    # ── Database.repair_field_indexes ────────────────────────────────────────

    def test_repair_alone_moves_every_old_entry_custom_ids_too(self):
        db = Database.get_instance()
        truth = _old_world()
        assert _old_keys_left(db)
        _repair(db)
        assert not _old_keys_left(db)
        _check(truth)
        with WithoutOldKeys():  # nothing is left for the old-key reads to find
            _check(truth)

    def test_repair_after_the_rebuild_finishes_what_it_does_not_reach(self):
        db = Database.get_instance()
        truth = _old_world()
        _rebuild()
        assert _old_keys_left(db)  # the custom ids
        _repair(db)
        assert not _old_keys_left(db)
        with WithoutOldKeys():
            _check(truth)

    def test_repair_does_nothing_to_a_right_index(self):
        db = Database.get_instance()
        rng = random.Random(2)
        truth = {}
        for _ in range(40):
            _create(rng, truth, custom=rng.random() < 0.2)
        before = _storage(db)
        _repair(db)
        assert _storage(db) == before

    def test_repair_a_call_reads_at_most_limit_keys_and_any_limit_gives_one_result(
        self,
    ):
        db = Database.get_instance()
        results = []
        for limit in (1, 4, 500):
            db.clear()
            _old_world()
            seen = []
            original = db._repair_entry
            db._repair_entry = lambda key, value: (
                seen.append(key),
                original(key, value),
            )
            try:
                cursor, calls = "", 0
                while cursor is not None:
                    before = len(seen)
                    cursor = db.repair_field_indexes(cursor, limit)
                    assert len(seen) - before <= limit, limit
                    calls += 1
                    assert calls < 20000
            finally:
                del db._repair_entry
            results.append(_storage(db))
        assert results[0] == results[1] == results[2]
        assert not _old_keys_left(db)

    def test_repair_validates_its_arguments(self):
        db = Database.get_instance()
        for arguments in (
            {"limit": 0},
            {"after": "x"},
            {"after": "_ri:IrNote:1:kids|c"},
        ):
            try:
                db.repair_field_indexes(**arguments)
            except ValueError:
                continue
            raise AssertionError(f"{arguments} was accepted")
        assert db.repair_field_indexes() is None  # nothing to walk

    def test_repair_leaves_an_entry_it_cannot_judge(self):
        db = Database.get_instance()
        db.save("IrNote", "9", {"kind": "x"})  # a row with no "dept"
        entries = [
            ("_fx:Ghost:dept:q%7Cr", "1"),  # a type nobody registered
            ("_fx:IrNote:other:q%7Cr", "1"),  # a field that is not indexed
            ("_fx:IrNote:dept:q%7Cr", "9"),  # a row from before the field
        ]
        for base, entity_id in entries:
            db._ix_add(base, entity_id)
        before = _storage(db)
        _repair(db)
        assert _storage(db) == before

    # ── A rollback to the release before, and the upgrade again ──────────────

    def test_a_rollback_leaves_stale_entries_and_the_repair_removes_them(self):
        db = Database.get_instance()
        a, b, c = (IrNote(dept="a|b", kind="") for _ in range(3))  # filed under _fx
        with AsTheReleaseBefore():
            a.dept = "c"  # takes it off _fi: it is not there; _fx keeps a
            b.dept = "c"
            b.dept = "a|b"  # filed under _fi as well: b is on both lists
            c.delete()  # likewise: _fx keeps c
        truth = {a._id: {"dept": "c", "kind": ""}, b._id: {"dept": "a|b", "kind": ""}}
        assert IrNote.count_by("dept", "a|b") != 1  # a, c stale, b twice
        found = IrNote.find_by("dept", "a|b")[0]
        assert [e.dept for e in found] != ["a|b"] * len(found)  # a is not "a|b"
        _repair(db)
        assert IrNote.count_by("dept", "a|b") == 1
        assert [e._id for e in IrNote.find_by("dept", "a|b")[0]] == [b._id]
        assert not _old_keys_left(db)
        assert not [k for k in _storage(db) if k.startswith("_fx:") and a._id in k]
        _check(truth, ["c", "a|b", ""])

    def test_after_any_rollback_the_repair_makes_everything_exact(self):
        db = Database.get_instance()
        for seed in (1, 2, 3):
            db.clear()
            Entity._context.clear()
            rng, truth = random.Random(seed), {}
            _write_some(rng, truth, 60)  # this release
            with AsTheReleaseBefore():
                _write_some(rng, truth, 60)  # rolled back to the release before
            _write_some(rng, truth, 40)  # this release again
            _repair(db)
            _check(truth)
            assert not _old_keys_left(db)
            before = _storage(db)
            _repair(db)
            assert _storage(db) == before  # a second run changes nothing
            with WithoutOldKeys():
                _check(truth)


def run(test_name: str = None, test_var: str = None):
    Database.get_instance().clear()
    tester = Tester(TestIndexRepair)
    return tester.run_tests()


if __name__ == "__main__":
    exit(run())
