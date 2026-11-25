"""Test demonstrating Entity creation and property storage behavior."""

from tester import Tester

from ic_python_db import Database, Entity, String


class User(Entity):
    """User entity with name alias."""

    __alias__ = "name"
    name = String(min_length=2, max_length=50)


class UserWithId(Entity):
    """User entity with id alias to demonstrate
    proper naming of property storage that avoids name conflicts."""

    __alias__ = "id"
    id = String(min_length=1, max_length=20)


class TestAliasAndProperties:
    def setUp(self):
        """Clear database before each test for isolation."""
        Database.get_instance().clear()

    def test_basic_entity_creation(self):
        """Test basic entity creation with auto-generated ID."""
        user = User(name="alice")

        # Verify internal storage
        assert user._id == "1"  # Auto-generated ID
        assert user.name == "alice"
        assert "_prop_name" in user.__dict__  # Property stored with _prop_ prefix
        assert User.count() == 1
        assert len(User.instances()) == 1

    def test_entity_with_custom_id(self):
        """Test entity creation with custom ID field."""
        user_with_id = UserWithId(id="bob")

        # Verify the fix: id property doesn't interfere with _id
        assert user_with_id._id == "1"  # Auto-generated database ID
        assert user_with_id.id == "bob"  # User-provided id field
        assert "_prop_id" in user_with_id.__dict__  # Property stored with _prop_ prefix
        assert UserWithId.count() == 1

    def test_alias_lookup(self):
        """Test entity lookup by alias."""
        # Create entities
        User(name="charlie")
        UserWithId(id="dave")

        # Test alias lookups
        found_user1 = User["charlie"]
        found_user2 = UserWithId["dave"]

        assert found_user1 is not None
        assert found_user1.name == "charlie"
        assert found_user2 is not None
        assert found_user2.id == "dave"

    def test_counting_consistency(self):
        """Test that count() and instances() are consistent."""
        initial_count = UserWithId.count()
        initial_instances = len(UserWithId.instances())

        # Create a new entity
        UserWithId(id="eve")

        final_count = UserWithId.count()
        final_instances = len(UserWithId.instances())

        assert final_count == initial_count + 1
        assert final_instances == initial_instances + 1
        assert final_count == final_instances

    def test_property_storage_separation(self):
        """Test that property storage doesn't interfere with Entity internals."""
        user = UserWithId(id="test_user")

        # Verify separation of concerns
        assert hasattr(user, "_id")  # Entity internal ID
        assert hasattr(user, "id")  # User property
        assert user._id != user.id  # They should be different
        assert user._id == "1"  # Auto-generated
        assert user.id == "test_user"  # User-provided

        # Verify property storage uses correct prefix
        assert "_prop_id" in user.__dict__
        assert user.__dict__["_prop_id"] == "test_user"

    # Alias entries kept in step with the rows (issue #46)

    def _people(self):
        class AlPerson(Entity):
            __alias__ = "name"
            name = String()

        return AlPerson

    def test_a_rename_removes_the_old_alias(self):
        AlPerson = self._people()
        p = AlPerson(name="ann")
        p.name = "anna"
        assert AlPerson["ann"] is None
        assert AlPerson["anna"]._id == p._id

    def test_a_second_row_cannot_take_an_alias_another_row_holds(self):
        """Neither by creation nor by a rename; nothing is left behind."""
        AlPerson = self._people()
        p = AlPerson(name="anna")
        Tester.assert_raises(ValueError, AlPerson, name="anna")
        assert AlPerson.count() == 1
        assert AlPerson.max_id() == 1

        q = AlPerson(name="bob")
        Tester.assert_raises(ValueError, setattr, q, "name", "anna")
        assert q.name == "bob"
        assert AlPerson["bob"]._id == q._id
        assert AlPerson["anna"]._id == p._id

    def test_delete_removes_only_its_own_alias_entry(self):
        """A duplicate an older release stored holds the value, but the entry
        names the first row: deleting the duplicate leaves it."""
        AlPerson = self._people()
        db = Database.get_instance()
        p = AlPerson(name="anna")
        db.save("AlPerson", "2", {"_type": "AlPerson", "_id": "2", "name": "anna"})
        db.save("_system", "AlPerson_id", "2")
        db.save("_system", "AlPerson_count", "2")
        AlPerson.load("2").delete()
        assert AlPerson["anna"]._id == p._id

    def test_a_stale_alias_entry_is_taken_over(self):
        """An entry whose row is gone, or no longer holds the value, does not
        refuse the value to another row."""
        AlPerson = self._people()
        db = Database.get_instance()
        db.save(AlPerson._alias_key(), "ghost", "99")  # no row 99
        assert AlPerson(name="ghost")._id == AlPerson["ghost"]._id


def run(test_name: str = None, test_var: str = None):
    tester = Tester(TestAliasAndProperties)
    if test_name:
        return tester.run_test(test_name, test_var)
    return tester.run_tests()


if __name__ == "__main__":
    exit(run())
