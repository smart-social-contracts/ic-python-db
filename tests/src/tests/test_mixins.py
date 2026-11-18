"""Test cases for entity mixins."""

from tester import Tester

from ic_python_db import *
from ic_python_db.context import set_caller_id


class TestEntity(Entity, TimestampedMixin):
    """Test entity class that uses TimestampedMixin."""

    pass


def datetime_is_real() -> bool:
    """False in a canister, where datetime is a stand-in that formats every time as 1970."""
    return SystemTime.format_timestamp(
        1_700_000_000_000
    ) != SystemTime.format_timestamp(1_800_000_000_000)


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

    def test_save_after_reload_writes_the_update_and_keeps_the_creation(self):
        """The loaded timestamp_updated and updater are replaced on save, and
        the loaded creator and timestamp_created are kept."""

        class Doc(Entity, TimestampedMixin):
            title = String(default="")

        created_at, updated_at = 1_700_000_000_000, 1_700_000_999_000
        system_time = SystemTime.get_instance()
        system_time.set_time(created_at)
        set_caller_id("alice")
        Doc(title="v1")
        db = Database.get_instance()
        db.clear_registry()

        system_time.set_time(updated_at)
        set_caller_id("bob")
        doc = Doc.load("1")
        doc.set_owner("bob")
        doc.title = "v2"

        stored = db.load("Doc", "1")
        assert stored["creator"] == "alice"
        assert stored["owner"] == "bob"
        assert stored["updater"] == "bob"
        if datetime_is_real():
            assert stored["timestamp_created"] == SystemTime.format_timestamp(
                created_at
            )
            assert stored["timestamp_updated"] == SystemTime.format_timestamp(
                updated_at
            )

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

    def test_parse_timestamp_reads_what_format_timestamp_wrote(self):
        if datetime_is_real():
            for milliseconds in (1_700_000_999_123, 1_742_316_676_123):
                text = SystemTime.format_timestamp(milliseconds)
                assert SystemTime.parse_timestamp(text) == milliseconds, text

    def test_parse_timestamp_gives_0_for_what_is_not_a_timestamp(self):
        for unreadable in ("None", "", "not a time", None):
            assert SystemTime.parse_timestamp(unreadable) == 0, unreadable


def run(test_name: str = None, test_var: str = None):
    tester = Tester(TestMixins)
    return tester.run_tests()


if __name__ == "__main__":
    exit(run())
