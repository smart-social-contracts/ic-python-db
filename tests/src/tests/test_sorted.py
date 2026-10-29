"""Sorted indexes: sorted=True, __sorted_indexes__, top_by, rebuild_sorted_index."""

from tester import Tester

from ic_python_db import Database, Entity, Float, Integer, String


class SoListing(Entity):
    name = String()
    installs = Integer(sorted=True)
    likes = Integer(sorted=True)
    updated_at = Float(sorted=True)
    __sorted_indexes__ = [
        ("installs", "likes"),
        ("likes", "installs"),
        ("installs", "likes", "updated_at"),
    ]


class SoPlain(Entity):
    """Starts without a sorted field (rows written before it was declared)."""

    name = String()
    score = Integer()


class SoVersioned(Entity):
    __version__ = 1
    name = String()
    score = Integer(sorted=True)


def _names(entities):
    return [e.name for e in entities]


def _listing(name, installs, likes, updated_at=0.0, **kw):
    return SoListing(
        name=name, installs=installs, likes=likes, updated_at=updated_at, **kw
    )


def _storage():
    return Database.get_instance()._db_storage


def _entries(prefix):
    return [k for k in _storage().keys() if k.startswith(prefix)]


def _walk(fields, count, **kw):
    """Every entity top_by returns, following the cursor to the end."""
    seen, cursor = [], None
    while True:
        page, cursor = SoListing.top_by(fields, count=count, cursor=cursor, **kw)
        seen.extend(_names(page))
        if cursor is None:
            return seen


class TestSorted:
    def setUp(self):
        Database.get_instance().clear()

    # Order

    def test_highest_first_and_lowest_first(self):
        for name, installs in (("a", 5), ("b", 9), ("c", 1)):
            _listing(name, installs, 0)
        assert _names(SoListing.top_by("installs")[0]) == ["b", "a", "c"]
        assert _names(SoListing.top_by("installs", descending=False)[0]) == [
            "c",
            "a",
            "b",
        ]

    def test_ties_go_to_the_higher_id(self):
        for name in ("old", "mid", "new"):
            _listing(name, 7, 0)
        assert _names(SoListing.top_by("installs")[0]) == ["new", "mid", "old"]

    def test_negative_and_float_values_sort_numerically(self):
        values = [-3.5, 0.0, 2.25, -1e9, 1e12, -0.001, 7.0]
        for i, v in enumerate(values):
            _listing(f"f{i}", 0, 0, updated_at=v)
        order = [
            e.updated_at
            for e in SoListing.top_by("updated_at", count=10, descending=False)[0]
        ]
        assert order == sorted(values)
        for i, v in enumerate([-(2**62), -1, 0, 1, 2**62]):
            _listing(f"i{i}", v, 0)
        ints = [e.installs for e in SoListing.top_by("installs", count=20)[0]]
        assert ints == sorted(ints, reverse=True) and ints[0] == 2**62

    def test_values_outside_64_bits_and_nan_are_refused(self):
        Tester.assert_raises(ValueError, _listing, "big", 2**63, 0)
        Tester.assert_raises(ValueError, _listing, "nan", 0, 0, updated_at=float("nan"))
        Tester.assert_raises(ValueError, _listing, "inf", 0, 0, updated_at=float("inf"))

    def test_none_is_not_indexed(self):
        SoListing(name="no-installs", likes=3, updated_at=0.0)
        _listing("full", 1, 1)
        assert _names(SoListing.top_by("installs")[0]) == ["full"]
        assert _names(SoListing.top_by("likes")[0]) == ["no-installs", "full"]

    # Multiple criteria

    def test_several_sorted_fields_on_one_type(self):
        _listing("popular", 100, 1, updated_at=1.0)
        _listing("loved", 1, 100, updated_at=2.0)
        _listing("fresh", 2, 2, updated_at=3.0)
        assert _names(SoListing.top_by("installs")[0])[0] == "popular"
        assert _names(SoListing.top_by("likes")[0])[0] == "loved"
        assert _names(SoListing.top_by("updated_at")[0])[0] == "fresh"

    def test_composite_index_breaks_ties_with_the_next_field(self):
        rows = [("a", 5, 1), ("b", 5, 9), ("c", 9, 0), ("d", 5, 4), ("e", 1, 99)]
        for name, installs, likes in rows:
            _listing(name, installs, likes)
        by_installs = sorted(rows, key=lambda r: (r[1], r[2]), reverse=True)
        by_likes = sorted(rows, key=lambda r: (r[2], r[1]), reverse=True)
        assert _names(SoListing.top_by(("installs", "likes"))[0]) == [
            r[0] for r in by_installs
        ]
        assert _names(SoListing.top_by(("likes", "installs"))[0]) == [
            r[0] for r in by_likes
        ]
        lowest = SoListing.top_by(("installs", "likes"), descending=False)[0]
        assert _names(lowest) == [r[0] for r in reversed(by_installs)]

    def test_three_field_index(self):
        _listing("x", 5, 5, updated_at=1.0)
        _listing("y", 5, 5, updated_at=3.0)
        _listing("z", 5, 5, updated_at=2.0)
        assert _names(SoListing.top_by(("installs", "likes", "updated_at"))[0]) == [
            "y",
            "z",
            "x",
        ]

    def test_undeclared_indexes_and_bad_cursors_are_refused(self):
        _listing("a", 1, 1)
        Tester.assert_raises(ValueError, SoListing.top_by, "name")
        Tester.assert_raises(ValueError, SoListing.top_by, ("likes", "updated_at"))
        Tester.assert_raises(
            ValueError, SoListing.top_by, "installs", 20, "not-a-cursor"
        )
        _, cursor = SoListing.top_by("installs", count=1)
        assert cursor is None  # one row, nothing left
        _listing("b", 2, 2)
        _, cursor = SoListing.top_by("installs", count=1)
        Tester.assert_raises(ValueError, SoListing.top_by, "likes", 1, cursor)

    # Writes keep the index current

    def test_updates_move_the_entity_in_every_index(self):
        a, b = _listing("a", 1, 1), _listing("b", 2, 2)
        a.installs = 10
        assert _names(SoListing.top_by("installs")[0]) == ["a", "b"]
        assert _names(SoListing.top_by(("installs", "likes"))[0]) == ["a", "b"]
        b.likes = 50
        assert _names(SoListing.top_by(("likes", "installs"))[0]) == ["b", "a"]
        # One entry per entity and direction, whatever was updated
        assert len(_entries("_si:SoListing:installs:d|")) == 2

    def test_delete_removes_every_entry(self):
        a = _listing("a", 1, 1)
        _listing("b", 2, 2)
        a.delete()
        assert _names(SoListing.top_by("installs")[0]) == ["b"]
        assert not [k for k in _entries("_s") if k.endswith(f":{a._id}")]
        assert (
            len(_entries("_si:SoListing:")) == 2 * 6
        )  # b only: 6 indexes x 2 directions

    def test_deserialize_upsert_updates_the_index(self):
        a = _listing("a", 1, 1)
        _listing("b", 5, 5)
        SoListing.deserialize({"_type": "SoListing", "_id": a._id, "installs": 99})
        assert _names(SoListing.top_by("installs")[0]) == ["a", "b"]

    def test_custom_ids(self):
        SoListing(_id="alpha", name="alpha", installs=3, likes=0, updated_at=0.0)
        _listing("seq", 5, 0)
        assert _names(SoListing.top_by("installs")[0]) == ["seq", "alpha"]

    # Paging

    def test_pages_cover_everything_once(self):
        for i in range(23):
            _listing(f"l{i}", i % 7, i % 3)
        expected = _names(SoListing.top_by(("installs", "likes"), count=100)[0])
        assert len(expected) == 23
        for size in (1, 4, 10, 23, 50):
            assert _walk(("installs", "likes"), size) == expected

    def test_where_filters_and_scan_limit_bounds_each_call(self):
        for i in range(12):
            _listing(f"l{i}", i, 0)
        odd = lambda e: e.installs % 2 == 1  # noqa: E731
        page, cursor = SoListing.top_by("installs", count=10, where=odd, scan_limit=4)
        # 4 entries read: l11, l10, l9, l8; two pass, and more remain
        assert _names(page) == ["l11", "l9"] and cursor is not None
        assert _walk("installs", 10, where=odd, scan_limit=4) == [
            f"l{i}" for i in (11, 9, 7, 5, 3, 1)
        ]

    # Migrations

    def test_rebuild_indexes_rows_written_before_the_field_was_sorted(self):
        for i in range(5):
            SoPlain(name=f"p{i}", score=i * 10)
        SoPlain.score.sorted = True
        SoPlain.__dict__.get("_sorted_cache") is not None and delattr(
            SoPlain, "_sorted_cache"
        )
        try:
            assert SoPlain.top_by("score")[0] == []
            cursor = 1
            while cursor is not None:
                cursor = SoPlain.rebuild_sorted_index("score", from_id=cursor, batch=2)
            assert _names(SoPlain.top_by("score")[0]) == ["p4", "p3", "p2", "p1", "p0"]
            SoPlain.rebuild_sorted_index("score")  # idempotent
            assert len(_entries("_si:SoPlain:score:d|")) == 5
        finally:
            SoPlain.score.sorted = False
            delattr(SoPlain, "_sorted_cache")

    def test_rebuild_replaces_stale_entries_whatever_they_were(self):
        """E.g. after a field's type changed: the back pointer finds the old
        entry without knowing the old value."""
        a = _listing("a", 3, 3)
        db = Database.get_instance()
        db.sorted_index_set("SoListing", "installs", a._id, "0" * 20)  # a stale entry
        assert len(_entries("_si:SoListing:installs:a|")) == 1
        SoListing.rebuild_sorted_index("installs")
        assert _names(SoListing.top_by("installs", descending=False)[0]) == ["a"]
        assert _entries("_si:SoListing:installs:a|")[0].startswith(
            "_si:SoListing:installs:a|" + "09223372036854775811"  # 3 + 2**63
        )

    def test_migrated_values_move_in_the_index_on_load(self):
        low, high = SoVersioned(name="low", score=1), SoVersioned(name="high", score=2)
        SoVersioned.__version__ = 2
        original = SoVersioned.__dict__.get("migrate")
        SoVersioned.migrate = classmethod(
            lambda cls, obj, f, t: {
                **obj,
                "score": obj["score"] * (100 if obj["name"] == "low" else 1),
            }
        )
        try:
            Database.get_instance().clear_registry()
            del low, high
            SoVersioned.load_some(1, 10)  # migrates and saves both
            assert _names(SoVersioned.top_by("score")[0]) == ["low", "high"]
            assert len(_entries("_si:SoVersioned:score:d|")) == 2
        finally:
            SoVersioned.__version__ = 1
            if original is None:
                del SoVersioned.migrate
            else:
                SoVersioned.migrate = original


def run(test_name: str = None, test_var: str = None):
    tester = Tester(TestSorted)
    return tester.run_tests()


if __name__ == "__main__":
    exit(run())
