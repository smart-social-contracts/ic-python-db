"""Index keys made of text a caller chooses cannot hide other callers' records.

A field index files the ids of a value under keys that start with the value
(``_fi:Type:field:<value>|n000000000042``). A value that ends like such a key
(``<other value>|n0000000000``) made keys of its own among the entries of the
other value, so a page read of that value came back short and a caller that
stops at a short page never saw the entries after it.

(No ``re`` here: Basilisk's ``re`` is an empty module.)
"""

import hashlib
import random

from tester import Tester

from ic_python_db import *
from ic_python_db.db_engine import _escape_key_text, _unescape_key_text

PAGE_SIZES = (3, 10, 50)

# Values that end like the keys of a posting list, or like the escape
LOOK_ALIKES = [
    "V1|n0000000000",  # its count key is among V1's entries, between 99 and 100
    "V1|n0000000001",  # ... between 199 and 200
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


def _walk_ids(db, value, count):
    """Every sequential id ``field_index_page`` gives for ``value``."""
    found, start = [], 1
    while True:
        page = db.field_index_page("IkNote", "dept", value, start, count)
        found.extend(int(i) for i in page)
        if len(page) < count:
            return found
        start = found[-1] + 1


def _storage(db):
    return dict(db._db_storage.items())


class TestIndexKeys:
    def setUp(self):
        Database.get_instance().clear()
        Entity._context.clear()

    # ── The reported failure ─────────────────────────────────────────────────

    def test_look_alike_values_do_not_hide_ids(self):
        db = Database.get_instance()
        mine = [int(IkNote(dept="V1")._id) for _ in range(130)]  # 99 -> 100
        others = {v: IkNote(dept=v) for v in LOOK_ALIKES}

        for count in PAGE_SIZES:
            assert _walk("V1", count) == mine, count
        assert db.field_index_page("IkNote", "dept", "V1", 90, 20) == [
            str(i) for i in range(90, 110)
        ]
        assert db.field_index_count("IkNote", "dept", "V1") == 130
        assert db.field_index_get("IkNote", "dept", "V1") == [str(i) for i in mine]
        for value, note in others.items():
            entities, cursor = IkNote.find_by("dept", value)
            assert [e._id for e in entities] == [note._id], value
            assert cursor is None
            assert IkNote.count_by("dept", value) == 1, value

    def test_look_alike_values_that_came_first_do_not_hide_ids(self):
        for value in LOOK_ALIKES:
            IkNote(dept=value)
        mine = [int(IkNote(dept="V1")._id) for _ in range(130)]
        for count in PAGE_SIZES:
            assert _walk("V1", count) == mine, count

    def test_look_alike_values_do_not_hide_ids_past_a_thousand(self):
        db = Database.get_instance()
        ids = [str(i) for i in range(1, 1101)]  # 99 -> 100, 999 -> 1000
        for entity_id in ids:
            db.field_index_add("IkNote", "dept", "V1", entity_id)
        for value in ("V1|n0000000000", "V1|n0000000009", "V1|n0000000010"):
            db.field_index_add("IkNote", "dept", value, "7")
        for count in (1, 3, 10, 50, 97):
            assert _walk_ids(db, "V1", count) == list(range(1, 1101)), count
        assert db.field_index_page("IkNote", "dept", "V1", 990, 20) == ids[989:1009]
        assert db.field_index_get("IkNote", "dept", "V1") == ids

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

    def test_a_value_with_a_bar_is_escaped_into_fx_and_a_percent_alone_is_not(self):
        db = Database.get_instance()
        note = IkNote(dept="a|n0000000000|c")
        other = IkNote(dept="100%")
        keys = {k for k in _storage(db) if k.startswith(("_fi:", "_fx:"))}
        assert f"_fx:IkNote:dept:a%7Cn0000000000%7Cc|n{int(note._id):012d}" in keys
        assert "_fx:IkNote:dept:a%7Cn0000000000%7Cc|c" in keys
        assert f"_fi:IkNote:dept:100%|n{int(other._id):012d}" in keys
        assert "_fi:IkNote:dept:100%|c" in keys
        assert len(keys) == 4

    def test_the_escape_is_percent_encoding_of_bar_and_percent(self):
        # What a caller that escapes its own values first (realms-gos tags)
        # writes: its values hold no "|", so they keep their _fi keys
        db = Database.get_instance()
        for value in ("a|c", "a%|b", "|", "%|%"):
            theirs = value.replace("%", "%25").replace("|", "%7C")
            assert db._fi_key("T", "f", value) == f"_fx:T:f:{theirs}"
            assert db._fi_key("T", "f", theirs) == f"_fi:T:f:{theirs}"

    def test_the_escape_is_undone_exactly(self):
        rng = random.Random(3)
        alphabet = ["a", "|", "%", "7", "C", "2", "5", "%7C", "%25", "é"]
        for _ in range(2000):
            text = "".join(rng.choice(alphabet) for _ in range(rng.randint(0, 8)))
            assert _unescape_key_text(_escape_key_text(text)) == text, text
            assert "|" not in _escape_key_text(text)

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
            assert _walk_ids(db, "V1", count) == list(range(1, 301)), count
        assert db.field_index_get("IkNote", "dept", "V1") == [
            str(i) for i in range(1, 301)
        ]

    def test_a_scan_restarts_past_chars_whose_successor_is_longer(self):
        # A key left by a release before 0.13 (an old list of a value) can end
        # with any char; the key after it is not "that char + 1": U+007F, U+07FF
        # and U+FFFF go on in more bytes, U+10FFFF has no successor, U+D7FF's
        # is a surrogate that cannot be encoded
        db = Database.get_instance()
        for last in ("\x7f", "߿", "￿", "\U0010ffff", "퟿", "z"):
            db.clear()
            width = len(last.encode("utf-8"))
            top = 10**width
            first = max(1, top - 20)
            for i in range(first, top + 20):
                db.field_index_add("IkNote", "dept", "V1", str(i))
            tail = "0" * (12 - width) + last  # 12 bytes, as long as an entry
            db._db_storage.insert(f"_fi:IkNote:dept:V1|n{tail}", '["5"]')
            start = max(1, top - 9)
            want = [str(i) for i in range(start, start + 10)]
            assert db.field_index_page("IkNote", "dept", "V1", start, 10) == want, last
            assert _walk_ids(db, "V1", 10) == list(range(first, top + 20)), last

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

    def test_a_parent_id_that_makes_a_look_alike_key_hides_no_child(self):
        # Parent "X:kids|n00000" has the count key
        # _ri:IkNote:X:kids|n00000:kids|c, as long as X's entries and between
        # those below and above ten million
        db = Database.get_instance()
        ids = [str(i) for i in range(1, 31)] + [
            str(i) for i in range(9999995, 10000005)
        ]
        for child in ids:
            db.reverse_index_add("IkNote", "X", "kids", child)
        db.reverse_index_add("IkNote", "X:kids|n00000", "kids", "1")
        for count in (1, 3, 50):
            got, start = [], 1
            while True:
                page = db.reverse_index_page("IkNote", "X", "kids", start, count)
                got.extend(page)
                if len(page) < count:
                    break
                start = int(got[-1]) + 1
            assert got == ids, count

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


def run(test_name: str = None, test_var: str = None):
    Database.get_instance().clear()
    tester = Tester(TestIndexKeys)
    return tester.run_tests()


if __name__ == "__main__":
    exit(run())
