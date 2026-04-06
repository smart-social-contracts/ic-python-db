"""Tests for automatic Entity type registration at class definition time.

Verifies that Entity subclasses are registered in Database._entity_types
when the class is defined, not just when instances are created.

See: https://github.com/smart-social-contracts/ic-python-db/issues/6
"""

from tester import Tester

from ic_python_db import Database, Entity, Integer, String


class TestAutoRegister:
    def setUp(self):
        """Clear database before each test."""
        Database.get_instance().clear()

    def test_type_registered_at_definition_time(self):
        """Entity subclass should be in _entity_types immediately after class definition."""
        db = Database.get_instance()

        class Dog(Entity):
            __alias__ = "name"
            name = String(max_length=50)

        assert (
            "Dog" in db._entity_types
        ), f"Dog not in _entity_types: {list(db._entity_types.keys())}"
        assert db._entity_types["Dog"] is Dog

    def test_multiple_types_registered(self):
        """Multiple Entity subclasses should all be registered."""
        db = Database.get_instance()

        class Cat(Entity):
            name = String(max_length=50)

        class Fish(Entity):
            name = String(max_length=50)

        assert "Cat" in db._entity_types
        assert "Fish" in db._entity_types

    def test_type_registered_without_creating_instances(self):
        """Type should be registered even if no instances exist."""
        db = Database.get_instance()

        class Bird(Entity):
            __alias__ = "name"
            name = String(max_length=50)

        assert "Bird" in db._entity_types
        assert Bird.count() == 0

    def test_type_still_works_after_instance_creation(self):
        """Creating instances should not break or duplicate registration."""
        db = Database.get_instance()

        class Horse(Entity):
            __alias__ = "name"
            name = String(max_length=50)
            legs = Integer(default=4)

        assert "Horse" in db._entity_types
        horse = Horse(name="Spirit", legs=4)
        assert "Horse" in db._entity_types
        assert db._entity_types["Horse"] is Horse
        loaded = Horse[horse._id]
        assert loaded.name == "Spirit"

    def test_namespaced_entity_registered(self):
        """Entity with __namespace__ should register under full type name."""
        db = Database.get_instance()

        class MyExtEntity(Entity):
            __namespace__ = "ext_test"
            name = String(max_length=50)

        full_name = MyExtEntity.get_full_type_name()
        assert full_name == "ext_test::MyExtEntity"
        assert (
            full_name in db._entity_types
        ), f"{full_name} not in _entity_types: {list(db._entity_types.keys())}"

    def test_clear_preserves_type_registration(self):
        """Database.clear() should not lose entity type registrations."""
        db = Database.get_instance()

        class Lizard(Entity):
            name = String(max_length=50)

        assert "Lizard" in db._entity_types
        db.clear()
        assert "Lizard" in db._entity_types

    def test_flush_deferred_types_is_idempotent(self):
        """Calling _flush_deferred_types when list is empty is a no-op."""
        Entity._flush_deferred_types()
        assert Entity._deferred_types == []

    def test_deferred_types_flushed_on_db_init(self):
        """Types defined before Database exists should register when DB is created."""
        old_instance = Database._instance
        Database._instance = None

        class Frog(Entity):
            name = String(max_length=50)

        assert Frog in Entity._deferred_types

        Database._instance = None
        db = Database.init(audit_enabled=False)
        assert "Frog" in db._entity_types
        assert Entity._deferred_types == []

        # Restore original DB instance for other tests
        Database._instance = None
        Database._instance = old_instance


def run(test_name: str = None, test_var: str = None):
    tester = Tester(TestAutoRegister)
    return tester.run_tests()


if __name__ == "__main__":
    Database.get_instance().clear()
    exit(run())
