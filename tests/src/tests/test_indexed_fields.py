"""Tests for secondary field indexes (indexed=True, issue #11)."""

from tester import Tester

from ic_python_db import *


class Ticket(Entity):
    """Entity with a mix of indexed and non-indexed properties."""

    title = String(max_length=100)
    status = String(max_length=32, indexed=True)
    priority = Integer(indexed=True)
    is_open = Boolean(default=True, indexed=True)


class TestIndexedFields:
    def setUp(self):
        Database.get_instance().clear()

    # ── Basic lookups ────────────────────────────────────────────────────────

    def test_find_by_returns_matches(self):
        t1 = Ticket(title="A", status="open")
        t2 = Ticket(title="B", status="open")
        Ticket(title="C", status="closed")

        entities, next_from_id = Ticket.find_by("status", "open")
        assert {e._id for e in entities} == {t1._id, t2._id}
        assert next_from_id is None

        entities, _ = Ticket.find_by("status", "closed")
        assert len(entities) == 1
        assert entities[0].title == "C"

    def test_find_by_no_matches(self):
        Ticket(title="A", status="open")
        entities, next_from_id = Ticket.find_by("status", "nonexistent")
        assert entities == []
        assert next_from_id is None

    def test_find_by_unindexed_field_raises(self):
        Ticket(title="A", status="open")
        Tester.assert_raises(ValueError, lambda: Ticket.find_by("title", "A"))
        Tester.assert_raises(ValueError, lambda: Ticket.count_by("title", "A"))
        Tester.assert_raises(ValueError, lambda: Ticket.rebuild_field_index("title"))

    def test_count_by(self):
        Ticket(title="A", status="open")
        Ticket(title="B", status="open")
        Ticket(title="C", status="closed")
        assert Ticket.count_by("status", "open") == 2
        assert Ticket.count_by("status", "closed") == 1
        assert Ticket.count_by("status", "missing") == 0

    def test_non_string_values(self):
        t1 = Ticket(title="A", priority=1)
        Ticket(title="B", priority=2)
        t3 = Ticket(title="C", priority=1, is_open=False)

        entities, _ = Ticket.find_by("priority", 1)
        assert {e._id for e in entities} == {t1._id, t3._id}

        entities, _ = Ticket.find_by("is_open", False)
        assert [e._id for e in entities] == [t3._id]

    # ── Index maintenance on update / delete ────────────────────────────────

    def test_update_moves_entity_between_values(self):
        t = Ticket(title="A", status="open")
        Ticket(title="B", status="open")

        t.status = "closed"

        open_entities, _ = Ticket.find_by("status", "open")
        assert t._id not in {e._id for e in open_entities}
        assert len(open_entities) == 1

        closed_entities, _ = Ticket.find_by("status", "closed")
        assert [e._id for e in closed_entities] == [t._id]

    def test_set_to_none_removes_from_index(self):
        t = Ticket(title="A", status="open")
        t.status = None
        assert Ticket.count_by("status", "open") == 0

    def test_delete_removes_from_index(self):
        t = Ticket(title="A", status="open")
        Ticket(title="B", status="open")
        t.delete()
        entities, _ = Ticket.find_by("status", "open")
        assert t._id not in {e._id for e in entities}
        assert len(entities) == 1

    def test_noop_update_keeps_index_consistent(self):
        t = Ticket(title="A", status="open")
        t.status = "open"  # same value
        assert Ticket.count_by("status", "open") == 1

    # ── Persistence across load ──────────────────────────────────────────────

    def test_load_does_not_duplicate_or_corrupt_index(self):
        t = Ticket(title="A", status="open")
        entity_id = t._id
        del t

        db = Database.get_instance()
        db.clear_registry()

        loaded = Ticket.load(entity_id)
        assert loaded.status == "open"
        assert Ticket.count_by("status", "open") == 1

        # Updating after a fresh load must still move the index entry.
        loaded.status = "closed"
        assert Ticket.count_by("status", "open") == 0
        assert Ticket.count_by("status", "closed") == 1

    # ── Pagination ────────────────────────────────────────────────────────────

    def test_pagination_walks_all_matches_in_id_order(self):
        ids = []
        for i in range(7):
            ids.append(Ticket(title=f"T{i}", status="open")._id)
        Ticket(title="other", status="closed")

        collected = []
        cursor = 1
        pages = 0
        while cursor is not None:
            entities, cursor = Ticket.find_by("status", "open", from_id=cursor, count=3)
            collected.extend(e._id for e in entities)
            pages += 1
            assert pages < 10  # safety against infinite loop

        assert collected == sorted(ids, key=int)
        assert pages == 3  # 3 + 3 + 1

    def test_pagination_from_id_skips_earlier_entities(self):
        first = Ticket(title="A", status="open")
        second = Ticket(title="B", status="open")

        entities, _ = Ticket.find_by("status", "open", from_id=int(first._id) + 1)
        assert [e._id for e in entities] == [second._id]

    # ── Rebuild (backfill) ───────────────────────────────────────────────────

    def test_rebuild_field_index_backfills_missing_entries(self):
        t1 = Ticket(title="A", status="open")
        t2 = Ticket(title="B", status="open")
        t3 = Ticket(title="C", status="closed")

        # Simulate entities written before the field was indexed: wipe all
        # index entries for Ticket.status directly from storage.
        db = Database.get_instance()
        for key in list(db._db_storage.keys()):
            if key.startswith("_fi:Ticket:status:"):
                db._db_storage.remove(key)
        assert Ticket.count_by("status", "open") == 0

        cursor = 1
        rounds = 0
        while cursor is not None:
            cursor = Ticket.rebuild_field_index("status", from_id=cursor, batch=2)
            rounds += 1
            assert rounds < 10

        assert rounds == 2  # 3 entities, batch=2 → two rounds
        assert Ticket.count_by("status", "open") == 2
        assert Ticket.count_by("status", "closed") == 1

        entities, _ = Ticket.find_by("status", "open")
        assert {e._id for e in entities} == {t1._id, t2._id}
        assert t3._id not in {e._id for e in entities}

    def test_rebuild_is_idempotent(self):
        Ticket(title="A", status="open")
        cursor = 1
        while cursor is not None:
            cursor = Ticket.rebuild_field_index("status", from_id=cursor)
        assert Ticket.count_by("status", "open") == 1

    def test_rebuild_empty_type_returns_none(self):
        assert Ticket.rebuild_field_index("status") is None

    # ── Posting-list storage (issue #18) ─────────────────────────────────────

    def test_no_stored_value_grows_with_matches(self):
        db = Database.get_instance()
        for i in range(300):
            Ticket(title=f"T{i}", status="open")
        longest = max(len(v) for k, v in db._db_storage.items() if k.startswith("_fi:"))
        assert longest < 20
        assert Ticket.count_by("status", "open") == 300

    def test_large_set_pages_in_order(self):
        for i in range(1200):
            Ticket(title=f"T{i}", status="open" if i % 3 else "closed")
        collected = []
        cursor = 1
        while cursor is not None:
            entities, cursor = Ticket.find_by(
                "status", "open", from_id=cursor, count=97
            )
            collected.extend(int(e._id) for e in entities)
        expected = [i + 1 for i in range(1200) if i % 3]
        assert collected == expected
        assert Ticket.count_by("status", "open") == len(expected)
        assert len(
            Database.get_instance().field_index_get("Ticket", "status", "open")
        ) == len(expected)

    def test_ids_across_digit_counts_stay_ordered(self):
        db = Database.get_instance()
        for entity_id in ("9", "10", "99", "100", "1000"):
            db.field_index_add("Ticket", "status", "open", entity_id)
        assert db.field_index_page("Ticket", "status", "open", 1, 10) == [
            "9",
            "10",
            "99",
            "100",
            "1000",
        ]
        assert db.field_index_page("Ticket", "status", "open", 50, 2) == ["99", "100"]

    def test_add_and_remove_are_idempotent(self):
        db = Database.get_instance()
        db.field_index_add("Ticket", "status", "open", "5")
        db.field_index_add("Ticket", "status", "open", "5")
        assert db.field_index_count("Ticket", "status", "open") == 1
        db.field_index_remove("Ticket", "status", "open", "6")
        assert db.field_index_count("Ticket", "status", "open") == 1
        db.field_index_remove("Ticket", "status", "open", "5")
        db.field_index_remove("Ticket", "status", "open", "5")
        assert db.field_index_count("Ticket", "status", "open") == 0
        assert not [k for k in db._db_storage.keys() if k.startswith("_fi:")]

    def test_custom_ids_are_counted_but_not_paged(self):
        db = Database.get_instance()
        for entity_id in ("abc", "007", "1234567890123", "4"):
            db.field_index_add("Ticket", "status", "open", entity_id)
        assert db.field_index_count("Ticket", "status", "open") == 4
        assert db.field_index_page("Ticket", "status", "open", 1, 10) == ["4"]
        assert sorted(db.field_index_get("Ticket", "status", "open")) == sorted(
            ["abc", "007", "1234567890123", "4"]
        )
        db.field_index_remove("Ticket", "status", "open", "abc")
        assert "abc" not in db.field_index_get("Ticket", "status", "open")

    def test_neighbouring_values_do_not_leak(self):
        db = Database.get_instance()
        db.field_index_add("Ticket", "status", "x", "1")
        # Same key length as x's entries, and inside x's range bytewise
        db.field_index_add("Ticket", "status", "x|n0;abcdefgh", "2")
        db.field_index_add("Ticket", "status", "x|n", "3")
        assert db.field_index_page("Ticket", "status", "x", 1, 10) == ["1"]
        assert db.field_index_get("Ticket", "status", "x") == ["1"]

    def test_legacy_json_list_is_migrated_on_first_touch(self):
        db = Database.get_instance()
        db._db_storage.insert("_fi:Ticket:status:open", '["3", "1", "2"]')
        assert db.field_index_count("Ticket", "status", "open") == 3
        assert db._db_storage.get("_fi:Ticket:status:open") is None
        db.field_index_add("Ticket", "status", "open", "4")
        assert db.field_index_page("Ticket", "status", "open", 1, 10) == [
            "1",
            "2",
            "3",
            "4",
        ]

    def test_works_without_range(self):
        from ic_python_db.storage import Storage

        class ProbeOnlyStorage(Storage):
            def __init__(self):
                self._data = {}

            def insert(self, key, value):
                self._data[key] = value

            def get(self, key):
                return self._data.get(key)

            def remove(self, key):
                del self._data[key]

            def items(self):
                return iter(self._data.items())

            def __contains__(self, key):
                return key in self._data

            def keys(self):
                return iter(self._data.keys())

        db = Database.get_instance()
        original = db._db_storage
        try:
            db._db_storage = ProbeOnlyStorage()
            ids = [Ticket(title=f"T{i}", status="open")._id for i in range(12)]
            entities, cursor = Ticket.find_by("status", "open", count=5)
            assert [e._id for e in entities] == ids[:5]
            assert cursor == int(ids[5])
            assert Ticket.count_by("status", "open") == 12
        finally:
            db._db_storage = original


def run(test_name: str = None, test_var: str = None):
    Database.get_instance().clear()
    tester = Tester(TestIndexedFields)
    return tester.run_tests()


if __name__ == "__main__":
    exit(run())
