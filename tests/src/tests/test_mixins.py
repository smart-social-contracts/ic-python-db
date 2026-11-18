"""Test cases for entity mixins."""

from tester import Tester

from ic_python_db import *
from ic_python_db.context import set_caller_id


class TestEntity(Entity, TimestampedMixin):
    """Test entity class that uses TimestampedMixin."""

    pass


class TestMixins:
    def setUp(self):
        """Reset Entity class variables before each test."""
        Database.get_instance().clear()

    def tearDown(self):
        SystemTime.get_instance().clear_time()
        set_caller_id("system")

    def test_timestamped_mixin(self):
        """Test that TimestampedMixin adds timestamp and ownership functionality."""
        # Set up test caller and time
        set_caller_id("test_user")
        system_time = SystemTime.get_instance()

        # Set initial time
        initial_time = 1000000  # 1000 seconds since epoch
        system_time.set_time(initial_time)

        # Create and save entity
        entity = TestEntity()
        entity._save()

        # Check timestamps and ownership
        assert entity._timestamp_created == initial_time
        assert entity._timestamp_updated == initial_time
        assert entity._creator == "test_user"
        assert entity._updater == "test_user"
        assert entity._owner == "test_user"

        # Update entity
        system_time.advance_time(
            60000
        )  # Advance time by 1 minute (60 seconds = 60,000 milliseconds)
        entity._save()

        # Check that updated timestamp changed
        assert entity._timestamp_updated == initial_time + 60000
        assert entity._timestamp_created == initial_time

        # Try to update with different user
        set_caller_id("other_user")
        Tester.assert_raises(PermissionError, entity._save)

        # Change owner and update
        entity.set_owner("other_user")
        system_time.advance_time(60000)  # Advance time by another minute
        entity._save()  # Should work now

        # Verify changes in dictionary format
        data = entity.serialize()
        assert "timestamp_created" in data
        assert "timestamp_updated" in data
        assert "creator" in data
        assert "updater" in data
        assert "owner" in data
        assert data["creator"] == "test_user"
        assert data["updater"] == "other_user"
        assert data["owner"] == "other_user"

        # Clean up
        system_time.clear_time()

    def _write_is_refused(self, doc, caller_id):
        set_caller_id(caller_id)
        try:
            doc.title = f"changed by {caller_id}"
        except PermissionError:
            return True
        return False

    def test_owner_check_holds_after_reload(self):
        """A new message loads the row again: the owner is read back with it,
        so another caller is still refused and the owner is still accepted."""

        class Doc(Entity, TimestampedMixin):
            title = String(default="")

        set_caller_id("alice")
        Doc(title="v1")
        db = Database.get_instance()
        db.clear_registry()

        doc = Doc.load("1")
        assert doc._owner == "alice"
        assert doc._creator == "alice"
        assert self._write_is_refused(doc, "mallory")
        assert db.load("Doc", "1")["title"] == "v1"

        set_caller_id("alice")
        doc.title = "v2"
        assert db.load("Doc", "1")["title"] == "v2"

    def test_save_after_reload_keeps_the_stored_creation_and_writes_the_update(self):
        """A save writes back the stored creator and timestamp_created as they
        were, and its own updater and timestamp_updated, not the loaded ones.
        The stored text is not read as a time, so this holds where datetime is
        real and where it is a stand-in that cannot write a time back."""

        class Doc(Entity, TimestampedMixin):
            title = String(default="")

        created_text = "2025-02-09 15:26:27.123"
        updated_text = "2025-02-09 16:00:00.000"
        db = Database.get_instance()
        db.save(
            "Doc",
            "1",
            {
                "_type": "Doc",
                "_id": "1",
                "title": "v1",
                "timestamp_created": created_text,
                "timestamp_updated": updated_text,
                "creator": "alice",
                "updater": "alice",
                "owner": "alice",
            },
        )
        db.save("_system", "Doc_id", "1")

        updated_at = 1_800_000_000_000
        SystemTime.get_instance().set_time(updated_at)
        set_caller_id("bob")
        doc = Doc.load("1")
        assert doc.serialize()["timestamp_created"] == created_text
        assert doc.serialize()["timestamp_updated"] == updated_text
        doc.set_owner("bob")
        doc.title = "v2"

        stored = db.load("Doc", "1")
        assert stored["creator"] == "alice"
        assert stored["timestamp_created"] == created_text
        assert stored["owner"] == "bob"
        assert stored["updater"] == "bob"
        assert stored["timestamp_updated"] == SystemTime.format_timestamp(updated_at)

    def test_owner_check_holds_when_the_stored_timestamps_cannot_be_read(self):
        """In a canister datetime is a stand-in and every stored timestamp
        reads "1970-01-01 00:00:00.000"; the owner must not depend on them."""

        class Doc(Entity, TimestampedMixin):
            title = String(default="")

        db = Database.get_instance()
        db.save(
            "Doc",
            "1",
            {
                "_type": "Doc",
                "_id": "1",
                "title": "v1",
                "timestamp_created": "1970-01-01 00:00:00.000",
                "timestamp_updated": "None",
                "creator": "alice",
                "updater": "alice",
                "owner": "alice",
            },
        )
        db.save("_system", "Doc_id", "1")

        doc = Doc.load("1")
        assert self._write_is_refused(doc, "mallory")
        set_caller_id("alice")
        doc.title = "v2"
        stored = db.load("Doc", "1")
        assert stored["creator"] == "alice"
        assert stored["owner"] == "alice"
        assert stored["timestamp_created"] == "1970-01-01 00:00:00.000"

    def test_row_stored_without_the_mixin_fields_is_claimed_by_its_first_save(self):
        """A row written before the class had the mixin has no owner. It
        loads, and the first caller to save it becomes its creator and owner."""

        class Doc(Entity, TimestampedMixin):
            title = String(default="")

        db = Database.get_instance()
        db.save("Doc", "1", {"_type": "Doc", "_id": "1", "title": "v1"})
        db.save("_system", "Doc_id", "1")

        set_caller_id("carol")
        doc = Doc.load("1")
        doc.title = "v2"

        stored = db.load("Doc", "1")
        assert stored["owner"] == "carol"
        assert stored["creator"] == "carol"
        assert self._write_is_refused(doc, "mallory")

    def _rows_stored_at_version_1(self, overrides_save=False):
        """Three rows of version 1, two owned by alice and one by bob, and the
        version 2 code that has to migrate them when they are read. With
        ``overrides_save`` the class overrides ``_save(self)`` as a subclass
        may (ic-basilisk-toolkit's Codex does)."""

        class SavesThroughSuper:
            def _save(self):
                return super()._save()

        class Doc(Entity, TimestampedMixin):
            __version__ = 1
            title = String(default="")

        SystemTime.get_instance().set_time(1_700_000_000_000)
        for owner in ("alice", "alice", "bob"):
            set_caller_id(owner)
            Doc(title=f"by {owner}")
        Database.get_instance().clear_registry()

        bases = (Entity, TimestampedMixin)
        if overrides_save:
            bases = (SavesThroughSuper,) + bases

        class Doc(*bases):  # the same type, new code
            __version__ = 2
            title = String(default="")
            note = String(default="")

        Database.get_instance().register_entity_type(Doc)
        SystemTime.get_instance().set_time(1_800_000_000_000)
        return Doc

    def test_a_read_that_migrates_rows_is_not_checked_as_a_write(self):
        """Migrating a row on load saves it, but the reader asked for no
        change: it is not refused for owning nothing, and the owner, updater
        and timestamps of the row stay as they were."""
        reads = {
            "load_some": lambda Doc: Doc.load_some(1, 10),
            "page": lambda Doc: Doc.page(1, 10)[0],
            "load": lambda Doc: [Doc.load("1"), Doc.load("3")],
        }
        for reader in ("carol", "bob", "alice"):
            for name, read in reads.items():
                Database.get_instance().clear()
                Doc = self._rows_stored_at_version_1()
                db = Database.get_instance()
                before = {i: db.load("Doc", i) for i in ("1", "2", "3")}

                set_caller_id(reader)
                try:
                    rows = read(Doc)
                except PermissionError as e:
                    assert False, f"{name} as {reader} raised PermissionError: {e}"
                assert len(rows) >= 2, (name, reader, rows)

                for row in rows:
                    after = db.load("Doc", row._id)
                    assert after["__version__"] == 2, (name, reader, after)
                    for field in (
                        "owner",
                        "creator",
                        "updater",
                        "timestamp_created",
                        "timestamp_updated",
                    ):
                        assert after[field] == before[row._id][field], (
                            name,
                            reader,
                            field,
                            after,
                        )

    def test_a_read_that_migrates_rows_works_for_a_class_that_overrides_save(self):
        """A subclass may override _save(self); the save that persists a
        migration must not need an argument it does not take."""
        reads = {
            "load_some": lambda Doc: Doc.load_some(1, 10),
            "page": lambda Doc: Doc.page(1, 10)[0],
            "load": lambda Doc: [Doc.load("1"), Doc.load("3")],
        }
        for name, read in reads.items():
            Database.get_instance().clear()
            Doc = self._rows_stored_at_version_1(overrides_save=True)
            set_caller_id("carol")
            rows = read(Doc)
            assert len(rows) >= 2, (name, rows)
            stored = Database.get_instance().load("Doc", rows[0]._id)
            assert stored["__version__"] == 2, (name, stored)
            assert stored["owner"] in ("alice", "bob"), (name, stored)

    def test_a_row_migrated_by_a_read_before_it_has_an_owner_is_claimed_by_a_write(
        self,
    ):
        """A row stored before its class had the mixin has no owner. A read
        that migrates it saves it without claiming it; the first write that
        follows claims it and records a real creation time."""

        class Doc(Entity, TimestampedMixin):
            __version__ = 1
            title = String(default="")

        db = Database.get_instance()
        db.save("Doc", "1", {"_type": "Doc", "_id": "1", "title": "v1"})
        db.save("_system", "Doc_id", "1")
        db.clear_registry()

        class Doc(Entity, TimestampedMixin):  # the same type, new code
            __version__ = 2
            title = String(default="")
            note = String(default="")

        db.register_entity_type(Doc)
        SystemTime.get_instance().set_time(1_700_000_000_000)  # a canister has none
        set_caller_id("carol")
        Doc.load("1")
        assert db.load("Doc", "1")["__version__"] == 2
        assert db.load("Doc", "1")["owner"] is None

        db.clear_registry()  # a later message loads the row again
        doc = Doc.load("1")
        doc.title = "v2"
        stored = db.load("Doc", "1")
        assert stored["owner"] == "carol"
        assert stored["creator"] == "carol"
        assert stored["timestamp_created"] != "None", stored

    def test_a_field_named_like_the_mixin_keeps_its_own_value_on_reload(self):
        class Box(Entity, TimestampedMixin):
            owner = String(default="")

        set_caller_id("alice")
        Box(owner="zed")
        db = Database.get_instance()
        db.clear_registry()

        box = Box.load("1")
        assert box.owner == "zed"
        box._save()
        stored = db.load("Box", "1")
        assert stored["owner"] == "zed"
        assert stored["creator"] == "alice"


def run(test_name: str = None, test_var: str = None):
    tester = Tester(TestMixins)
    return tester.run_tests()


if __name__ == "__main__":
    exit(run())
