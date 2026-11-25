"""Test cases for entity mixins."""

from tester import Tester

from ic_python_db import *
from ic_python_db.context import get_caller_id, set_caller_id


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

        # The owner hands it over, then the new owner updates it
        set_caller_id("test_user")
        entity.set_owner("other_user")
        set_caller_id("other_user")
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
        set_caller_id("alice")
        doc = Doc.load("1")
        assert doc.serialize()["timestamp_created"] == created_text
        assert doc.serialize()["timestamp_updated"] == updated_text
        doc.set_owner("bob")
        set_caller_id("bob")
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

    # Who may claim and write (issues #51, #52, #53)

    def _alices_doc(self):
        class Doc(Entity, TimestampedMixin):
            title = String(default="")

        set_caller_id("alice")
        doc = Doc(title="v1")
        return Doc, doc

    def _stored(self, doc):
        row = Database.get_instance().load(doc._type, doc._id)
        return {k: row[k] for k in ("title", "owner", "creator", "updater")}

    def test_set_owner_is_refused_to_a_caller_who_does_not_own_the_entity(self):
        """A caller refused a write cannot take the entity and write."""
        Doc, doc = self._alices_doc()
        set_caller_id("mallory")
        Tester.assert_raises(PermissionError, doc.set_owner, "mallory")
        assert doc._owner == "alice"
        assert self._write_is_refused(doc, "mallory")
        assert self._stored(doc) == {
            "title": "v1",
            "owner": "alice",
            "creator": "alice",
            "updater": "alice",
        }

    def test_set_owner_by_the_owner_is_stored_at_once(self):
        """The transfer is the owner's write: stored without a further save,
        so it holds in the next message, where the old owner is refused."""
        Doc, doc = self._alices_doc()
        doc.set_owner("bob")
        assert self._stored(doc) == {
            "title": "v1",
            "owner": "bob",
            "creator": "alice",
            "updater": "alice",
        }

        Database.get_instance().clear_registry()  # the next message
        doc = Doc.load(doc._id)
        assert self._write_is_refused(doc, "alice")
        set_caller_id("bob")
        doc.title = "v2"
        assert self._stored(doc)["title"] == "v2"
        assert self._stored(doc)["updater"] == "bob"

    def test_set_owner_on_an_unclaimed_row_claims_it_and_hands_it_over(self):
        """A row stored before its class had the mixin has no owner: any
        caller may write it, so any caller may give it away. The caller is
        recorded as its creator."""

        class Doc(Entity, TimestampedMixin):
            title = String(default="")

        db = Database.get_instance()
        db.save("Doc", "1", {"_type": "Doc", "_id": "1", "title": "v1"})
        db.save("_system", "Doc_id", "1")

        set_caller_id("carol")
        Doc.load("1").set_owner("dave")
        stored = db.load("Doc", "1")
        assert stored["owner"] == "dave", stored
        assert stored["creator"] == "carol", stored
        assert self._write_is_refused(Doc.load("1"), "carol")

    def test_set_owner_refuses_a_missing_owner(self):
        """None means "unclaimed", which anyone may write."""
        Doc, doc = self._alices_doc()
        for missing in (None, ""):
            Tester.assert_raises(ValueError, doc.set_owner, missing)
            assert doc._owner == "alice"
            assert self._write_is_refused(doc, "mallory")
            set_caller_id("alice")
        assert self._stored(doc)["owner"] == "alice"

    def test_a_missing_caller_id_is_refused(self):
        """A None caller would claim nothing, so every save would claim the
        row again and move its creation time."""
        db = Database.get_instance()
        set_caller_id("alice")
        for missing in (None, "", 7):
            Tester.assert_raises(ValueError, set_caller_id, missing)
            assert get_caller_id() == "alice"

            def enter_as_missing():
                with db.as_user(missing):
                    pass

            Tester.assert_raises(ValueError, enter_as_missing)
            assert get_caller_id() == "alice"

    def test_deserialize_of_a_new_row_ignores_the_provenance_in_the_data(self):
        """The importer claims the row: the owner, creator and updater the
        data names are not stored, and are not left as plain attributes."""

        class Doc(Entity, TimestampedMixin):
            title = String(default="")

        set_caller_id("mallory")
        doc = Doc.deserialize(
            {
                "_type": "Doc",
                "_id": "1",
                "title": "x",
                "owner": "alice",
                "creator": "alice",
                "updater": "alice",
                "timestamp_created": "2020-01-01 00:00:00.000",
            }
        )
        assert self._stored(doc) == {
            "title": "x",
            "owner": "mallory",
            "creator": "mallory",
            "updater": "mallory",
        }
        assert doc._owner == "mallory"
        for name in TimestampedMixin._STATE_FIELDS:
            assert name not in doc.__dict__, name
        stored = Database.get_instance().load("Doc", "1")
        assert stored["timestamp_created"] != "2020-01-01 00:00:00.000"

    def test_deserialize_by_the_owner_ignores_the_provenance_in_the_data(self):
        """An update by the owner stores the owner and updater it has, not
        the data's, and a set_owner after it is the owner that is stored."""
        Doc, doc = self._alices_doc()
        Database.get_instance().clear_registry()
        doc = Doc.deserialize(
            {
                "_type": "Doc",
                "_id": doc._id,
                "title": "y",
                "owner": "carol",
                "updater": "carol",
            }
        )
        assert self._stored(doc) == {
            "title": "y",
            "owner": "alice",
            "creator": "alice",
            "updater": "alice",
        }
        assert doc.serialize()["owner"] == "alice"

        doc.set_owner("bob")
        Database.get_instance().clear_registry()  # the next message
        set_caller_id("bob")
        Doc.load(doc._id).title = "bob writes"
        assert self._stored(doc)["title"] == "bob writes"
        assert self._stored(doc)["owner"] == "bob"

    def test_deserialize_keeps_a_field_named_like_the_mixin(self):
        """A class that declares a field "owner" stores what the data gives
        it; the mixin's own owner is still the caller."""

        class Box(Entity, TimestampedMixin):
            owner = String(default="")

        set_caller_id("alice")
        Box.deserialize({"_type": "Box", "_id": "1", "owner": "zed"})
        stored = Database.get_instance().load("Box", "1")
        assert stored["owner"] == "zed", stored
        assert stored["creator"] == "alice", stored

    def test_a_refused_field_write_changes_nothing(self):
        """The owner check comes before the hook, the value, the field index
        and the sorted index: a refused caller cannot move alice's row out of
        find_by or up in top_by."""
        hook_calls = []

        class Doc(Entity, TimestampedMixin):
            tag = String(indexed=True)
            score = Integer(sorted=True)

            @staticmethod
            def on_event(entity, field_name, old_value, new_value, action):
                hook_calls.append(field_name)
                return True, new_value

        set_caller_id("alice")
        doc = Doc(tag="a", score=1)
        hook_calls.clear()
        set_caller_id("mallory")
        for field, value in (("tag", "evil"), ("score", 99)):
            Tester.assert_raises(PermissionError, setattr, doc, field, value)
        assert hook_calls == []
        assert (doc.tag, doc.score) == ("a", 1)
        assert Doc.count_by("tag", "a") == 1
        assert Doc.count_by("tag", "evil") == 0
        assert [(e._id, e.score) for e in Doc.top_by("score")[0]] == [(doc._id, 1)]

    def test_a_refused_relation_write_changes_nothing(self):
        """A relation's reverse index is written before the save: the owner
        check must come first, for every way of changing a relation."""

        class Team(Entity, TimestampedMixin):
            name = String()
            members = OneToMany("Member", "team")

        class Member(Entity, TimestampedMixin):
            name = String()
            team = ManyToOne("Team", "members")
            tags = ManyToMany("Label", "members")

        class Label(Entity):
            name = String()
            members = ManyToMany("Member", "tags")

        set_caller_id("alice")
        team = Team(name="alice's")
        red = Label(name="red")
        member = Member(name="m", team=team)
        member.tags.add(red)
        set_caller_id("mallory")
        other = Team(name="mallory's")
        blue = Label(name="blue")

        Tester.assert_raises(PermissionError, setattr, member, "team", other)
        Tester.assert_raises(PermissionError, setattr, member, "tags", [blue])
        Tester.assert_raises(PermissionError, member.tags.add, blue)
        Tester.assert_raises(PermissionError, member.tags.discard, red)
        assert [m._id for m in team.members] == [member._id]
        assert list(other.members) == []
        assert [t.name for t in member.tags] == ["red"]
        assert [m._id for m in red.members] == [member._id]
        assert list(blue.members) == []

    def test_delete_is_refused_to_a_caller_who_does_not_own_the_entity(self):
        """Deleting is a write: mallory cannot delete alice's row, alice can."""

        class Doc(Entity, TimestampedMixin):
            tag = String(indexed=True)

        set_caller_id("alice")
        doc = Doc(tag="a")
        set_caller_id("mallory")
        Tester.assert_raises(PermissionError, doc.delete)
        db = Database.get_instance()
        assert db.load("Doc", doc._id) is not None
        assert Doc.count() == 1
        assert Doc.count_by("tag", "a") == 1

        set_caller_id("alice")
        doc.delete()
        assert db.load("Doc", doc._id) is None
        assert Doc.count_by("tag", "a") == 0

    def test_rows_of_code_that_never_sets_a_caller_stay_writable(self):
        """No consumer of this library sets a caller: every write runs as
        "system", the default, and every row is owned by "system", or by
        nobody if it was stored before its class had the mixin. Each write
        path gets a row of each kind it has not touched yet (the first write
        claims an unowned row, so sharing one would test it only once)."""
        assert get_caller_id() == "system"

        class Team(Entity, TimestampedMixin):
            name = String()
            members = OneToMany("Member", "team")

        class Label(Entity, TimestampedMixin):
            name = String()
            members = ManyToMany("Member", "tags")

        class Badge(Entity, TimestampedMixin):
            name = String()
            member = OneToOne("Member", "badge")

        class Member(Entity, TimestampedMixin):
            tag = String(indexed=True)
            score = Integer(sorted=True)
            team = ManyToOne("Team", "members")
            tags = ManyToMany("Label", "members")
            badge = OneToOne("Badge", "member")

        db = Database.get_instance()
        team, red = Team(name="t"), Label(name="red")

        def unowned(tagged=False):
            row_id = str(Member.max_id() + 1)
            db.save("Member", row_id, {"_type": "Member", "_id": row_id, "tag": "a"})
            db.save("_system", "Member_id", row_id)
            db.save("_system", "Member_count", str(Member.count() + 1))
            db.field_index_add("Member", "tag", "a", row_id)
            if tagged:  # linked as stored, so the path's write is the row's first
                db.reverse_index_add("Member", row_id, "tags", red._id)
                db.reverse_index_add("Label", red._id, "members", row_id)
            db.clear_registry()
            member = Member.load(row_id)
            assert member._owner is None
            return member

        def owned_by_system(tagged=False):
            member = Member(tag="a")
            if tagged:
                member.tags.add(red)
            db.clear_registry()
            member = Member.load(member._id)
            assert member._owner == "system"
            return member

        def stored(m):
            return db.load("Member", m._id)

        def add_red(m):
            m.tags.add(red)

        paths = {  # name: (write, what it must have done)
            "field": (
                lambda m: setattr(m, "tag", "b"),
                lambda m: m._id in [e._id for e in Member.find_by("tag", "b")[0]],
            ),
            "sorted field": (
                lambda m: setattr(m, "score", 5),
                lambda m: m._id in [e._id for e in Member.top_by("score")[0]],
            ),
            "many-to-one": (
                lambda m: setattr(m, "team", team),
                lambda m: m._id in [e._id for e in team.members],
            ),
            "many-to-many set": (
                lambda m: setattr(m, "tags", [red]),
                lambda m: m._id in [e._id for e in red.members],
            ),
            "many-to-many add": (
                add_red,
                lambda m: m._id in [e._id for e in red.members],
            ),
            "many-to-many discard": (
                lambda m: m.tags.discard(red),
                lambda m: m._id not in [e._id for e in red.members],
            ),
            "one-to-one": (
                lambda m: setattr(m, "badge", Badge(name=f"b{m._id}")),
                lambda m: m.badge is not None and m.badge.member._id == m._id,
            ),
            "deserialize": (
                lambda m: Member.deserialize(
                    {"_type": "Member", "_id": m._id, "tag": "c"}
                ),
                lambda m: stored(m)["tag"] == "c",
            ),
            "set_owner": (
                lambda m: m.set_owner("system"),
                lambda m: stored(m)["owner"] == "system",
            ),
            "delete": (
                lambda m: m.delete(),
                lambda m: stored(m) is None
                and m._id not in [e._id for e in Member.find_by("tag", "a")[0]],
            ),
        }
        for name, (write, done) in paths.items():
            for kind in (unowned, owned_by_system):
                member = kind(tagged=name == "many-to-many discard")
                try:
                    write(member)
                except PermissionError as e:
                    assert False, f"{name} on a row {kind.__name__}: {e}"
                assert done(member), (name, kind.__name__, stored(member))

    def test_rows_of_code_that_never_sets_a_caller_migrate_on_load(self):
        """A version bump migrates rows of both kinds when they are read, and
        they take a write afterwards."""
        assert get_caller_id() == "system"

        class Old(Entity, TimestampedMixin):
            __version__ = 1
            tag = String(indexed=True)

        db = Database.get_instance()
        Old(tag="a")  # owned by "system"
        db.save("Old", "2", {"_type": "Old", "_id": "2", "tag": "a"})
        db.save("_system", "Old_id", "2")
        db.save("_system", "Old_count", "2")
        db.clear_registry()

        class Old(Entity, TimestampedMixin):  # the same type, new code
            __version__ = 2
            tag = String(indexed=True)
            note = String(default="")

        db.register_entity_type(Old)
        for row_id, owner in (("1", "system"), ("2", None)):
            row = Old.load(row_id)
            assert db.load("Old", row_id)["__version__"] == 2
            assert db.load("Old", row_id).get("owner") == owner
            row.note = "after the migration"
            assert db.load("Old", row_id)["note"] == "after the migration"

    def test_a_refused_one_to_one_write_changes_nothing_on_either_side(self):
        """Linking a OneToOne can clear a link another entity holds, and save
        that entity: when it is not the caller's, nothing is cleared."""

        class Person(Entity, TimestampedMixin):
            name = String()
            profile = OneToOne("Profile", "person")

        class Profile(Entity, TimestampedMixin):
            name = String()
            person = OneToOne("Person", "profile")

        db = Database.get_instance()
        set_caller_id("alice")
        alice, card = Person(name="alice"), Profile(name="card")
        card.person = alice  # the link is stored on alice's card

        # The target side: mallory links her own person to alice's card
        set_caller_id("mallory")
        mallory = Person(name="mallory")
        Tester.assert_raises(PermissionError, setattr, mallory, "profile", card)
        assert alice.profile._id == card._id
        assert card.person._id == alice._id
        assert db.load("Profile", card._id)["person"] == alice._id
        assert mallory.profile is None

        # The inverse side: alice's person links to mallory's page; mallory
        # links that page elsewhere, which would clear alice's reference
        set_caller_id("mallory")
        page = Profile(name="page")
        set_caller_id("alice")
        bob = Person(name="bob")
        bob.profile = page
        set_caller_id("mallory")
        Tester.assert_raises(PermissionError, setattr, page, "person", mallory)
        assert bob.profile._id == page._id
        assert db.load("Person", bob._id)["profile"] == page._id
        assert page.person._id == bob._id

    def test_restoring_an_export_makes_the_importer_the_owner(self):
        """An export restored by another caller does not keep who wrote it:
        the importer becomes owner and creator, and the original owner can no
        longer write the row."""

        class Doc(Entity, TimestampedMixin):
            title = String()

        db = Database.get_instance()
        set_caller_id("alice")
        exported = Doc(title="v1").serialize()
        assert exported["owner"] == "alice"
        db.clear()

        set_caller_id("restorer")
        restored = Doc.deserialize(exported)
        stored = db.load("Doc", restored._id)
        assert (stored["owner"], stored["creator"]) == ("restorer", "restorer")
        assert stored["title"] == "v1"
        db.clear_registry()
        assert self._write_is_refused(Doc.load(restored._id), "alice")

    def test_a_row_with_a_creator_but_no_owner_is_claimed_by_its_next_write(self):
        """set_owner(None) used to leave a row with a creator and no owner,
        which any caller could write. Its next write makes the writer the
        owner; the creator stays."""

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
                "creator": "alice",
                "owner": None,
            },
        )
        db.save("_system", "Doc_id", "1")
        set_caller_id("bob")
        Doc.load("1").title = "v2"
        stored = db.load("Doc", "1")
        assert (stored["owner"], stored["creator"]) == ("bob", "alice"), stored
        assert self._write_is_refused(Doc.load("1"), "mallory")

    def test_older_code_cannot_delete_a_row_newer_code_stored(self):
        """It may not know the newer code's index entries, so a delete would
        leave them behind: refused like a write."""

        class Note(Entity):
            __version__ = 2
            text = String()

        note_id = Note(text="a")._id
        db = Database.get_instance()
        db.clear_registry()

        class Note(Entity):  # rolled back
            __version__ = 1
            text = String()

        db.register_entity_type(Note)
        Tester.assert_raises(MigrationError, Note.load(note_id).delete)
        assert db.load("Note", note_id) is not None


def run(test_name: str = None, test_var: str = None):
    tester = Tester(TestMixins)
    return tester.run_tests()


if __name__ == "__main__":
    exit(run())
