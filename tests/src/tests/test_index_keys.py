"""Index keys made of text a caller chooses cannot hide other callers' records.

A field index files the ids of a value under keys that start with the value
(``_fi:Type:field:<value>|n000000000042``). A value that ends like such a key
(``<other value>|n0000000000``) made keys of its own among the entries of the
other value, so a page read of that value came back short and a caller that
stops at a short page never saw the entries after it.
"""

import random
import re

from tester import Tester

from ic_python_db import *

PAGE_SIZES = (1, 3, 10, 50, 97)

# Values that end like the keys of a posting list, or like the escape
LOOK_ALIKES = [
    "V1|n0000000000",  # its count key is among V1's entries, between 99 and 100
    "V1|n0000000001",  # ... between 199 and 200
    "V1|n0000000009",  # ... between 999 and 1000
    "V1|n0000000000|c",
    "V1|d",
    "V1|c",
    "V1|",
    "V1|n",
    "V1|n000000000007",
    "V1|h000000000000000000000000",
    "V1%",
    "V1%7C",
    "V1%257C",
    "V1|n000000000é",
    "",
    "|",
    "%",
    "x" * 150 + "|n0000000000",
]


class IkNote(Entity):
    dept = String(max_length=300, indexed=True)
    kind = String(max_length=40, indexed=True)


def _walk(value, count):
    """Every id find_by gives for ``value``, a page at a time."""
    found, cursor = [], 1
    while cursor is not None:
        entities, cursor = IkNote.find_by("dept", value, from_id=cursor, count=count)
        found.extend(int(e._id) for e in entities)
    return found


def _storage(db):
    return dict(db._db_storage.items())


def _old_keys_left(db):
    """Keys of IkNote's indexes that are not those of a value without ``|``
    or ``%``: what is filed the old way."""
    entry = r"_fi:IkNote:[a-z]+:[^|%]*\|(c|n[0-9]{12}|h[0-9a-f]{24})"
    return [
        k
        for k in _storage(db)
        if k.startswith("_fi:IkNote:") and not re.fullmatch(entry, k)
    ]


def _as_filed_before_escaping(db):
    """Put the entries of values with ``|`` or ``%`` where the code before
    escaping filed them: under ``_fi``, with the value as it is."""

    def unescape(text):
        return re.sub("%(25|7C)", lambda m: "%" if m.group(1) == "25" else "|", text)

    for key, value in _storage(db).items():
        if key.startswith("_fx:"):
            db._db_storage.remove(key)
            db._db_storage.insert("_fi:" + unescape(key[4:]), value)


class TestIndexKeys:
    def setUp(self):
        Database.get_instance().clear()
        Entity._context.clear()

    # ── The reported failure ─────────────────────────────────────────────────

    def test_look_alike_values_do_not_hide_ids(self):
        db = Database.get_instance()
        mine = [int(IkNote(dept="V1")._id) for _ in range(1100)]  # 99->100, 999->1000
        others = {v: IkNote(dept=v) for v in LOOK_ALIKES}

        for count in PAGE_SIZES:
            assert _walk("V1", count) == mine, count
        assert db.field_index_page("IkNote", "dept", "V1", 90, 20) == [
            str(i) for i in range(90, 110)
        ]
        assert db.field_index_page("IkNote", "dept", "V1", 990, 20) == [
            str(i) for i in range(990, 1010)
        ]
        assert db.field_index_count("IkNote", "dept", "V1") == 1100
        assert db.field_index_get("IkNote", "dept", "V1") == [str(i) for i in mine]
        for value, note in others.items():
            entities, cursor = IkNote.find_by("dept", value)
            assert [e._id for e in entities] == [note._id], value
            assert cursor is None
            assert IkNote.count_by("dept", value) == 1, value

    def test_look_alike_values_that_came_first_do_not_hide_ids(self):
        for value in LOOK_ALIKES:
            IkNote(dept=value)
        mine = [int(IkNote(dept="V1")._id) for _ in range(300)]
        for count in PAGE_SIZES:
            assert _walk("V1", count) == mine, count

    def test_a_look_alike_removed_leaves_nothing_behind(self):
        db = Database.get_instance()
        notes = [IkNote(dept=v) for v in LOOK_ALIKES]
        for note in notes:
            note.delete()
        assert not [k for k in _storage(db) if k.startswith(("_fi:", "_fx:"))]

    # ── Keys of different values cannot meet ─────────────────────────────────

    def test_ordinary_values_keep_their_keys(self):
        db = Database.get_instance()
        note = IkNote(dept="open")
        keys = {k for k in _storage(db) if k.startswith("_fi:")}
        assert keys == {
            "_fi:IkNote:dept:open|c",
            f"_fi:IkNote:dept:open|n{int(note._id):012d}",
        }

    def test_values_with_a_bar_or_a_percent_are_escaped_into_fx(self):
        db = Database.get_instance()
        note = IkNote(dept="a|n0000000000|c")
        IkNote(dept="100%")
        keys = {k for k in _storage(db) if k.startswith(("_fi:", "_fx:"))}
        assert f"_fx:IkNote:dept:a%7Cn0000000000%7Cc|n{int(note._id):012d}" in keys
        assert "_fx:IkNote:dept:100%25|c" in keys
        assert not [k for k in keys if k.startswith("_fi:") and "%" in k]

    def test_the_escape_is_percent_encoding_of_bar_and_percent(self):
        # What a caller that escapes its own values first (realms-gos tags) writes
        db = Database.get_instance()
        for value in ("a|c", "a%7Cc", "%", "|", "a%|b"):
            theirs = value.replace("%", "%25").replace("|", "%7C")
            assert db._fi_key("T", "f", value) == f"_fx:T:f:{theirs}"

    def test_no_two_values_share_a_base_and_none_holds_a_bar(self):
        db = Database.get_instance()
        alphabet = ["a", "|", "%", "n", "c", "0", "7", "C", "2", "5", "é"]
        rng = random.Random(1)
        values = {
            "".join(rng.choice(alphabet) for _ in range(rng.randint(0, 6)))
            for _ in range(4000)
        }
        bases = {}
        for value in values:
            base = db._fi_key("T", "f", value)
            assert "|" not in base.split(":", 3)[3], (value, base)
            assert base not in bases, (value, bases[base])
            bases[base] = value

    def test_a_custom_id_cannot_make_another_value_swallow_its_entry(self):
        # An id that is a JSON list, in the entry of one value, was read as the
        # old array of a value that ended with that entry's key, and removed
        import hashlib

        db = Database.get_instance()
        custom_id = '["7"]'
        db.field_index_add("IkNote", "dept", "V1", custom_id)
        digest = hashlib.sha256(custom_id.encode()).hexdigest()[:24]
        assert db.field_index_count("IkNote", "dept", f"V1|h{digest}") == 0
        assert db.field_index_get("IkNote", "dept", "V1") == [custom_id]

    # ── Readers go on past keys that are not entries ─────────────────────────

    def test_a_page_goes_on_past_keys_of_other_bases(self):
        db = Database.get_instance()
        base = "_fi:IkNote:dept:V1"
        for i in range(1, 301):
            db.field_index_add("IkNote", "dept", "V1", str(i))
        # What the old keys of look-alike values left among the entries
        for first in range(0, 4):
            for tail in ("|c", "|d"):
                db._db_storage.insert(f"{base}|n000000000{first}{tail}", "junk")
        for count in PAGE_SIZES + (250,):
            got, start = [], 1
            while True:
                page = db.field_index_page("IkNote", "dept", "V1", start, count)
                got.extend(int(i) for i in page)
                if len(page) < count:
                    break
                start = got[-1] + 1
            assert got == list(range(1, 301)), count
        assert db.field_index_get("IkNote", "dept", "V1") == [
            str(i) for i in range(1, 301)
        ]

    def test_a_reverse_index_page_goes_on_past_keys_of_other_bases(self):
        db = Database.get_instance()
        for i in range(1, 251):
            db.reverse_index_add("IkNote", "P", "kids", str(i))
        for first in range(0, 3):
            db._db_storage.insert(f"_ri:IkNote:P:kids|n000000000{first}|c", "junk")
        page = db.reverse_index_page("IkNote", "P", "kids", 90, 20)
        assert page == [str(i) for i in range(90, 110)]
        page = db.reverse_index_page("IkNote", "P", "kids", 1, 250)
        assert page == [str(i) for i in range(1, 251)]

    def test_a_page_is_one_range_read_when_nothing_is_in_the_way(self):
        from ic_python_db.storage import MemoryStorage

        class CountingRanges(MemoryStorage):
            limits = []

            def range(self, start, end=None, limit=1000):
                self.limits.append(limit)
                return super().range(start, end, limit)

        db = Database.get_instance()
        original, counting = db._db_storage, CountingRanges()
        try:
            db._db_storage = counting
            for i in range(1, 121):
                db.field_index_add("IkNote", "dept", "open", str(i))
            assert len(db.field_index_page("IkNote", "dept", "open", 1, 50)) == 50
            assert len(db.field_index_page("IkNote", "dept", "open", 100, 50)) == 21
        finally:
            db._db_storage = original
        assert counting.limits == [50, 50]

    # ── The same answers as a plain model, for any values ────────────────────

    def test_reads_agree_with_a_model_under_random_writes(self):
        db = Database.get_instance()
        rng = random.Random(7)
        values = [
            base + tail
            for base in ("a", "a|", "a%", "")
            for tail in (
                "",
                "|c",
                "|d",
                "|",
                "|n",
                "|n0000000000",
                "|n0000000001",
                "|n0000000000|c",
                "%7C",
                "%25",
                "é",
            )
        ]
        ids = (
            [str(i) for i in range(1, 13)]
            + [str(i) for i in range(95, 106)]
            + [str(i) for i in range(995, 1006)]
            + ["99999", "abc", "007", "12345678901234"]
        )
        model = {value: set() for value in values}

        def check():
            for value, expected in model.items():
                assert db.field_index_count("IkNote", "dept", value) == len(expected)
                got = db.field_index_get("IkNote", "dept", value)
                assert sorted(got) == sorted(expected), value
                sequential = sorted(
                    (
                        int(i)
                        for i in expected
                        if i.isdigit() and i[0] != "0" and len(i) <= 12
                    )
                )
                assert [int(i) for i in got[: len(sequential)]] == sequential, value
                for start, count in ((1, 4), (90, 8), (99, 3), (995, 7), (1, 1000)):
                    page = db.field_index_page("IkNote", "dept", value, start, count)
                    want = [str(i) for i in sequential if i >= start][:count]
                    assert page == want, (value, start, count)

        for step in range(1500):
            value, entity_id = rng.choice(values), rng.choice(ids)
            if rng.random() < 0.6:
                db.field_index_add("IkNote", "dept", value, entity_id)
                model[value].add(entity_id)
            else:
                db.field_index_remove("IkNote", "dept", value, entity_id)
                model[value].discard(entity_id)
            if step % 150 == 149:
                check()
        check()

    # ── Entries filed before escaping ────────────────────────────────────────

    def _old_world(self):
        """Entities of five values, the ones with ``|`` or ``%`` filed the old way."""
        values = ["V1", "V1|n0000000000", "a%7Cc", "a|c", "100%"]
        notes = [IkNote(dept=values[i % 5]) for i in range(120)]
        _as_filed_before_escaping(Database.get_instance())
        return values, notes

    @staticmethod
    def _assert_found(values, notes):
        for value in values:
            ids = [int(n._id) for n in notes if n.dept == value]
            assert IkNote.count_by("dept", value) == len(ids), value
            for count in (3, 50):
                assert _walk(value, count) == ids, (value, count)

    def test_entries_filed_the_old_way_are_still_found(self):
        self._assert_found(*self._old_world())

    def test_an_old_key_is_not_taken_for_the_new_key_of_another_value(self):
        # The old key of "a%7Cc" is the escaped form of "a|c"
        db = Database.get_instance()
        db._ix_add("_fi:IkNote:dept:a%7Cc", "1")
        db.field_index_add("IkNote", "dept", "a|c", "2")
        assert db.field_index_get("IkNote", "dept", "a|c") == ["2"]
        assert db.field_index_get("IkNote", "dept", "a%7Cc") == ["1"]
        assert db.field_index_count("IkNote", "dept", "a|c") == 1

    def test_adding_or_removing_an_id_takes_it_off_its_old_list(self):
        db = Database.get_instance()
        db._ix_add("_fi:IkNote:dept:a|b", "1")
        db._ix_add("_fi:IkNote:dept:a|b", "2")
        db.field_index_add("IkNote", "dept", "a|b", "1")
        assert db._ix_count("_fi:IkNote:dept:a|b") == 1
        assert db.field_index_count("IkNote", "dept", "a|b") == 2
        db.field_index_remove("IkNote", "dept", "a|b", "2")
        assert db.field_index_get("IkNote", "dept", "a|b") == ["1"]
        db.field_index_remove("IkNote", "dept", "a|b", "1")
        assert db.field_index_count("IkNote", "dept", "a|b") == 0
        assert not [k for k in _storage(db) if k.startswith(("_fi:", "_fx:"))]

    def test_a_page_merges_the_old_list_and_the_new(self):
        db = Database.get_instance()
        for i in (1, 4, 100):
            db._ix_add("_fi:IkNote:dept:a|b", str(i))
        for i in (2, 3, 101):
            db.field_index_add("IkNote", "dept", "a|b", str(i))
        assert db.field_index_page("IkNote", "dept", "a|b", 1, 4) == [
            "1",
            "2",
            "3",
            "4",
        ]
        assert db.field_index_page("IkNote", "dept", "a|b", 4, 3) == ["4", "100", "101"]
        assert db.field_index_get("IkNote", "dept", "a|b") == [
            "1",
            "2",
            "3",
            "4",
            "100",
            "101",
        ]

    def test_readers_are_right_at_every_step_of_the_rebuild(self):
        values, notes = self._old_world()
        cursor, rounds = 1, 0
        while cursor is not None:
            self._assert_found(values, notes)
            cursor = IkNote.rebuild_field_indexes(from_id=cursor, batch=7)
            rounds += 1
            assert rounds < 100
        self._assert_found(values, notes)
        assert rounds == 18  # 120 entities, 7 at a time
        assert not _old_keys_left(Database.get_instance())

    def test_a_value_written_during_the_rebuild_is_listed_once(self):
        values, notes = self._old_world()
        IkNote.rebuild_field_indexes(from_id=1, batch=50)
        # One entity the rebuild has reached and one it has not, both off
        # lists filed the old way or the new
        for note in (notes[1], notes[102]):
            note.dept = "a|c"
        self._assert_found(values, notes)
        notes[102].delete()
        notes.remove(notes[102])
        self._assert_found(values, notes)

    def test_rebuild_field_indexes_does_every_indexed_field(self):
        notes = [IkNote(dept="a|b", kind="x%y") for _ in range(5)]
        db = Database.get_instance()
        _as_filed_before_escaping(db)
        assert IkNote.rebuild_field_indexes(batch=5) is None
        assert not _old_keys_left(db)
        assert IkNote.count_by("dept", "a|b") == 5
        assert IkNote.count_by("kind", "x%y") == 5
        assert IkNote.rebuild_field_indexes(batch=5) is None  # again: nothing changes
        assert IkNote.count_by("dept", "a|b") == 5
        assert [n._id for n in IkNote.find_by("kind", "x%y")[0]] == [
            n._id for n in notes
        ]

    def test_rebuild_field_indexes_validates_its_arguments(self):
        for arguments in ({"from_id": 0}, {"batch": 0}):
            try:
                IkNote.rebuild_field_indexes(**arguments)
            except ValueError:
                continue
            raise AssertionError(f"{arguments} was accepted")
        assert IkNote.rebuild_field_indexes() is None  # no entities


def run(test_name: str = None, test_var: str = None):
    Database.get_instance().clear()
    tester = Tester(TestIndexKeys)
    return tester.run_tests()


if __name__ == "__main__":
    exit(run())
