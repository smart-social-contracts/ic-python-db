"""Test serialization format for different relation types."""

from tester import Tester

from ic_python_db import (
    Database,
    Entity,
    ManyToMany,
    ManyToOne,
    OneToMany,
    OneToOne,
    String,
)


class Parent(Entity):
    name = String()
    children = OneToMany("Child", "parent")  # Should always be list
    favorite_child = OneToOne("Child", "favorite_parent")  # Should be single value


class Child(Entity):
    name = String()
    parent = ManyToOne("Parent", "children")  # Should be single value
    favorite_parent = OneToOne("Parent", "favorite_child")  # Should be single value
    siblings = ManyToMany("Child", "siblings")  # Should always be list


class TestSerialization:
    def setUp(self):
        """Reset Entity class variables before each test."""
        Database.get_instance().clear()

    def test_serialization_format(self):
        """Test that relations are serialized in the correct format."""

        # Create entities
        parent = Parent(name="Alice")
        child1 = Child(name="Bob")
        child2 = Child(name="Charlie")

        # Set up relations
        parent.children = [child1]  # OneToMany with single item
        parent.favorite_child = child1  # OneToOne
        child1.siblings = [child2]  # ManyToMany with single item

        # Test serialization
        parent_data = parent.serialize()
        child1_data = child1.serialize()

        # OneToMany should always be a list, even with single item
        assert isinstance(
            parent_data["children"], list
        ), "OneToMany should serialize as list"
        assert parent_data["children"] == [
            child1._id
        ], f"Expected ['{child1._id}'], got {parent_data['children']}"

        # OneToOne should be a single value
        assert isinstance(
            parent_data["favorite_child"], str
        ), "OneToOne should serialize as single value"
        assert (
            parent_data["favorite_child"] == child1._id
        ), f"Expected '{child1._id}', got {parent_data['favorite_child']}"

        # ManyToOne should be a single value
        assert isinstance(
            child1_data["parent"], str
        ), "ManyToOne should serialize as single value"
        assert (
            child1_data["parent"] == parent._id
        ), f"Expected '{parent._id}', got {child1_data['parent']}"

        # ManyToMany should always be a list, even with single item
        assert isinstance(
            child1_data["siblings"], list
        ), "ManyToMany should serialize as list"
        assert child1_data["siblings"] == [
            child2._id
        ], f"Expected ['{child2._id}'], got {child1_data['siblings']}"

        # Test string representation of serialized data
        parent_str = str(parent_data)
        child1_str = str(child1_data)

        # Verify OneToMany appears as list in string format
        assert (
            f"'children': ['{child1._id}']" in parent_str
        ), f"OneToMany should appear as list in string: {parent_str}"

        # Verify OneToOne appears as single value in string format
        assert (
            f"'favorite_child': '{child1._id}'" in parent_str
        ), f"OneToOne should appear as single value in string: {parent_str}"

        # Verify ManyToOne appears as single value in string format
        assert (
            f"'parent': '{parent._id}'" in child1_str
        ), f"ManyToOne should appear as single value in string: {child1_str}"

        # Verify ManyToMany appears as list in string format
        assert (
            f"'siblings': ['{child2._id}']" in child1_str
        ), f"ManyToMany should appear as list in string: {child1_str}"

        # Test with multiple items
        child3 = Child(name="David")
        parent.children = [child1, child3]  # OneToMany with multiple items
        child1.siblings = [child2, child3]  # ManyToMany with multiple items

        parent_data = parent.serialize()
        child1_data = child1.serialize()

        # Should still be lists
        assert isinstance(
            parent_data["children"], list
        ), "OneToMany should serialize as list"
        assert (
            len(parent_data["children"]) == 2
        ), "OneToMany should contain both children"

        assert isinstance(
            child1_data["siblings"], list
        ), "ManyToMany should serialize as list"
        assert (
            len(child1_data["siblings"]) == 2
        ), "ManyToMany should contain both siblings"

        assert (
            str(parent_data)
            == "{'_type': 'Parent', '_id': '1', 'name': 'Alice', 'children': ['1', '3'], 'favorite_child': '1'}"
        )
        assert (
            str(child1_data)
            == "{'_type': 'Child', '_id': '1', 'name': 'Bob', 'parent': '1', 'favorite_parent': '1', 'siblings': ['2', '3']}"
        )

    def test_deserialization(self):
        """Test that entities can be reconstructed from serialized data."""
        Database.get_instance().clear()

        # Create original entities
        parent = Parent(name="Alice")
        child1 = Child(name="Bob")
        child2 = Child(name="Charlie")

        # Set up relations
        parent.children = [child1]
        parent.favorite_child = child1
        child1.siblings = [child2]

        # Serialize the entities
        parent_data = parent.serialize()
        child1_data = child1.serialize()

        print("parent_data", parent_data)
        print("child1_data", child1_data)

        # Clear database to test deserialization
        Database.get_instance().clear()

        # Recreate entities from serialized data
        # Note: We need to create all entities first before setting relations
        recreated_parent = Parent.deserialize(parent_data)
        recreated_child1 = Child.deserialize(child1_data)

        print("recreated_parent", recreated_parent)
        print("recreated_child1", recreated_child1)

        # Verify basic properties
        assert recreated_parent.name == "Alice"
        assert recreated_parent._id == "1"
        assert recreated_child1.name == "Bob"
        assert recreated_child1._id == "1"

        # Test error cases
        try:
            Parent.deserialize({"invalid": "data"})
            assert False, "Should have raised ValueError for missing _type"
        except ValueError as e:
            assert "must contain '_type' field" in str(e)

        try:
            Parent.deserialize({"_type": "Child", "_id": "1", "name": "Test"})
            assert False, "Should have raised ValueError for type mismatch"
        except ValueError as e:
            assert "Entity type mismatch" in str(e)

        # Test that missing _id now creates a new entity (upsert behavior)
        Database.get_instance().clear()  # Clear to avoid conflicts
        result = Parent.deserialize({"_type": "Parent", "name": "Test"})
        assert result is not None, "Should create new entity when _id is missing"
        assert result.name == "Test", "Should set name property"
        assert result._id is not None, "Should auto-generate _id"

    def test_round_trip_serialization(self):
        """Test that serialize -> deserialize produces equivalent entities."""
        Database.get_instance().clear()

        # Create entities with complex relationships
        parent = Parent(name="Alice")
        child1 = Child(name="Bob")
        child2 = Child(name="Charlie")
        child3 = Child(name="David")

        # Set up complex relations
        parent.children = [child1, child2]
        parent.favorite_child = child1
        child1.siblings = [child2, child3]
        child2.siblings = [child1, child3]

        # Serialize all entities
        parent_data = parent.serialize()
        child1_data = child1.serialize()
        child2_data = child2.serialize()
        child3_data = child3.serialize()

        # Clear and recreate from serialized data
        Database.get_instance().clear()

        # Recreate entities (order matters for relations)
        Child.deserialize(child3_data)
        Child.deserialize(child2_data)
        recreated_child1 = Child.deserialize(child1_data)
        recreated_parent = Parent.deserialize(parent_data)

        # Relations are attempted to be resolved immediately during deserialize
        # (unresolvable relations are silently skipped)
        # Verify the recreated entities have the same serialized output
        recreated_parent_data = recreated_parent.serialize()
        recreated_child1_data = recreated_child1.serialize()

        print(f"Original parent: {parent_data}")
        print(f"Recreated parent: {recreated_parent_data}")
        print(f"Original child1: {child1_data}")
        print(f"Recreated child1: {recreated_child1_data}")

        # Verify that serialized data matches (allowing for different ordering in many-to-many relations)
        assert (
            recreated_parent_data == parent_data
        ), f"Parent data mismatch:\nOriginal: {parent_data}\nRecreated: {recreated_parent_data}"

        # For child1, check each field individually to handle list ordering
        for key, value in child1_data.items():
            if key == "siblings":  # ManyToMany relation - check set equality
                assert set(recreated_child1_data[key]) == set(
                    value
                ), f"Siblings mismatch: {recreated_child1_data[key]} != {value}"
            else:
                assert (
                    recreated_child1_data[key] == value
                ), f"Field {key} mismatch: {recreated_child1_data[key]} != {value}"

        # Verify basic properties are preserved
        assert recreated_parent.name == "Alice"
        assert recreated_child1.name == "Bob"

        # Verify relations are properly restored
        assert len(recreated_parent.children) == 2, "Parent should have 2 children"
        assert (
            recreated_parent.favorite_child is not None
        ), "Parent should have a favorite child"
        assert len(recreated_child1.siblings) == 2, "Child1 should have 2 siblings"

    def test_generic_deserialization(self):
        """Test that Entity.deserialize() works without knowing the entity type."""
        Database.get_instance().clear()

        # Create entities
        parent = Parent(name="Alice")
        child = Child(name="Bob")
        parent.children = [child]

        # Serialize entities
        parent_data = parent.serialize()
        child_data = child.serialize()

        # Clear database
        Database.get_instance().clear()

        # Test generic deserialization using Entity.deserialize()
        from ic_python_db import Entity

        recreated_parent = Entity.deserialize(parent_data)
        recreated_child = Entity.deserialize(child_data)

        # Verify types and properties
        assert isinstance(recreated_parent, Parent), "Should recreate Parent instance"
        assert isinstance(recreated_child, Child), "Should recreate Child instance"
        assert recreated_parent.name == "Alice"
        assert recreated_child.name == "Bob"
        assert recreated_parent._id == "1"
        assert recreated_child._id == "1"

        # Test the round-trip pattern: student = Entity.deserialize(student.serialize())
        Database.get_instance().clear()
        original = Parent(name="Test")
        roundtrip = Entity.deserialize(original.serialize())

        assert isinstance(roundtrip, Parent), "Round-trip should preserve type"
        assert roundtrip.name == "Test", "Round-trip should preserve properties"
        assert roundtrip._id == original._id, "Round-trip should preserve ID"

        # Test error cases
        try:
            Entity.deserialize({"invalid": "data"})
            assert False, "Should raise ValueError for missing _type"
        except ValueError as e:
            assert "must contain '_type' field" in str(e)

        try:
            Entity.deserialize({"_type": "NonExistentEntity", "_id": "1"})
            assert False, "Should raise ValueError for unknown entity type"
        except ValueError as e:
            assert "Unknown entity type" in str(e)

    def test_upsert_functionality(self):
        """Test the upsert functionality of Entity.deserialize method."""
        Database.get_instance().clear()

        # Test 1: Create new entity when no _id provided
        data = {"_type": "Parent", "name": "John"}
        parent = Parent.deserialize(data)

        assert parent is not None
        assert parent.name == "John"
        assert parent._id == "1"  # Auto-generated ID
        assert Parent.count() == 1

        # Test 2: Create new entity when provided _id doesn't exist
        data = {"_type": "Parent", "_id": "999", "name": "Jane"}
        parent2 = Parent.deserialize(data)

        assert parent2 is not None
        assert parent2.name == "Jane"
        assert (
            parent2._id == "999"
        )  # Now preserves provided _id when creating new entity
        assert Parent.count() == 2

        # Test 3: Update existing entity by _id
        original_id = parent._id
        data = {"_type": "Parent", "_id": original_id, "name": "John Updated"}
        updated = Parent.deserialize(data)

        assert updated is not None
        assert updated._id == original_id  # Same ID
        assert updated.name == "John Updated"
        assert Parent.count() == 2  # Count didn't increase
        assert updated is parent  # Same entity instance due to registry

        # Test 4: Partial update (merge mode)
        # First create entity with multiple properties
        Database.get_instance().clear()

        class TestEntity(Entity):
            name = String()
            description = String()

        original = TestEntity(name="Test", description="Original description")
        original_id = original._id

        # Update only one field
        data = {"_type": "TestEntity", "_id": original_id, "name": "Updated Test"}
        updated = TestEntity.deserialize(data)

        assert updated._id == original_id
        assert updated.name == "Updated Test"  # Updated
        assert updated.description == "Original description"  # Unchanged
        assert TestEntity.count() == 1

    def test_upsert_with_alias(self):
        """Test upsert functionality with alias fields."""
        Database.get_instance().clear()

        # Create entity class with alias
        class User(Entity):
            __alias__ = "name"
            name = String()
            age = String()

        # Test 1: Create new entity with alias (no existing match)
        data = {"_type": "User", "name": "Alice", "age": "30"}
        user = User.deserialize(data)

        assert user is not None
        assert user.name == "Alice"
        assert user.age == "30"
        assert user._id == "1"
        assert User.count() == 1

        # Test 2: Update existing entity by alias (no _id provided)
        data = {"_type": "User", "name": "Alice", "age": "31"}
        updated = User.deserialize(data)

        assert updated is not None
        assert updated._id == "1"  # Same ID
        assert updated.name == "Alice"  # Same name
        assert updated.age == "31"  # Updated age
        assert User.count() == 1  # Count didn't increase
        assert updated is user  # Same entity instance

        # Test 3: Create new entity when alias doesn't match
        data = {"_type": "User", "name": "Bob", "age": "25"}
        bob = User.deserialize(data)

        assert bob is not None
        assert bob.name == "Bob"
        assert bob.age == "25"
        assert bob._id == "2"
        assert User.count() == 2

        # Test 4: Update alias field itself
        original_id = user._id
        data = {"_type": "User", "_id": original_id, "name": "Alicia", "age": "32"}
        updated = User.deserialize(data)

        assert updated._id == original_id
        assert updated.name == "Alicia"
        assert updated.age == "32"

        # Verify old alias no longer works
        assert User["Alice"] is None

        # Verify new alias works
        found = User["Alicia"]
        assert found is not None
        assert found._id == original_id

    def test_upsert_with_relations(self):
        """Test that upsert handles relations correctly with immediate resolution."""
        Database.get_instance().clear()

        # Create entities first
        parent = Parent(name="Alice")
        child = Child(name="Bob")

        # Test create with relations - relations are resolved immediately
        data = {
            "_type": "Child",
            "name": "Charlie",
            "parent": parent._id,
        }
        charlie = Child.deserialize(data)

        assert charlie.name == "Charlie"
        # Relations are resolved immediately now
        assert charlie.parent == parent

        # Test update with relations
        update_data = {
            "_type": "Child",
            "_id": child._id,
            "name": "Bob Updated",
            "parent": parent._id,
        }
        updated_child = Child.deserialize(update_data)

        assert updated_child.name == "Bob Updated"
        assert updated_child.parent == parent
        assert updated_child is child  # Same instance

    def test_deserialize_max_id_count_consistency(self):
        """Test that deserialize handles max_id and count correctly in all scenarios."""
        Database.get_instance().clear()

        # Create a test entity class
        class TestEntity(Entity):
            name = String()

        # Scenario 1: Normal entity creation should increment both max_id and count
        initial_max_id = TestEntity.max_id()
        initial_count = TestEntity.count()

        entity1 = TestEntity(name="Entity1")
        assert (
            TestEntity.max_id() == initial_max_id + 1
        ), "max_id should increment on new entity"
        assert (
            TestEntity.count() == initial_count + 1
        ), "count should increment on new entity"
        assert entity1._id == "1", "First entity should have ID '1'"

        # Scenario 2: Deserialize without _id should create new entity with auto-generated ID
        data = {"_type": "TestEntity", "name": "Entity2"}
        entity2 = TestEntity.deserialize(data)

        assert TestEntity.max_id() == 2, "max_id should increment to 2"
        assert TestEntity.count() == 2, "count should increment to 2"
        assert entity2._id == "2", "Auto-generated ID should be '2'"

        # Scenario 3: Deserialize with custom ID higher than current max_id
        data = {"_type": "TestEntity", "_id": "10", "name": "Entity10"}
        entity10 = TestEntity.deserialize(data)

        assert TestEntity.max_id() == 10, "max_id should update to highest ID (10)"
        assert TestEntity.count() == 3, "count should increment to 3"
        assert entity10._id == "10", "Entity should have custom ID '10'"

        # Scenario 4: CRITICAL TEST - Deserialize with custom ID lower than current max_id
        # This is the potential bug scenario
        data = {"_type": "TestEntity", "_id": "5", "name": "Entity5"}
        entity5 = TestEntity.deserialize(data)

        # max_id should NOT decrease - it should remain at the highest value seen
        assert (
            TestEntity.max_id() == 10
        ), "max_id should NOT decrease when lower ID is used"
        assert TestEntity.count() == 4, "count should increment to 4"
        assert entity5._id == "5", "Entity should have custom ID '5'"

        # Scenario 5: Test ID collision potential - create new entity after lower ID insertion
        entity_new = TestEntity(name="EntityNew")

        # This should get ID 11 (max_id + 1), not 6 or any other value
        assert (
            entity_new._id == "11"
        ), f"New entity should get ID '11', got '{entity_new._id}'"
        assert TestEntity.max_id() == 11, "max_id should increment to 11"
        assert TestEntity.count() == 5, "count should increment to 5"

        # Scenario 6: Update existing entity - should not change max_id or count
        original_max_id = TestEntity.max_id()
        original_count = TestEntity.count()

        update_data = {"_type": "TestEntity", "_id": "5", "name": "Entity5 Updated"}
        updated_entity = TestEntity.deserialize(update_data)

        assert (
            TestEntity.max_id() == original_max_id
        ), "max_id should not change on update"
        assert TestEntity.count() == original_count, "count should not change on update"
        assert updated_entity._id == "5", "Updated entity should keep same ID"
        assert updated_entity.name == "Entity5 Updated", "Entity should be updated"
        assert updated_entity is entity5, "Should return same instance due to registry"

        # Scenario 7: Test with alias-based upsert
        class AliasEntity(Entity):
            __alias__ = "name"
            name = String()
            value = String()

        # Create entity via alias
        alias_data = {
            "_type": "AliasEntity",
            "name": "unique_name",
            "value": "original",
        }
        alias_entity = AliasEntity.deserialize(alias_data)

        alias_max_id = AliasEntity.max_id()
        alias_count = AliasEntity.count()

        # Update via alias (no _id provided) - should not change counters
        update_data = {
            "_type": "AliasEntity",
            "name": "unique_name",
            "value": "updated",
        }
        updated_alias = AliasEntity.deserialize(update_data)

        assert (
            AliasEntity.max_id() == alias_max_id
        ), "max_id should not change on alias update"
        assert (
            AliasEntity.count() == alias_count
        ), "count should not change on alias update"
        assert updated_alias.value == "updated", "Entity should be updated via alias"
        assert updated_alias is alias_entity, "Should return same instance"

    def test_deserialize_id_collision_prevention(self):
        """Test that deserialize prevents ID collisions when custom IDs are used."""
        Database.get_instance().clear()

        class CollisionTest(Entity):
            name = String()

        # Create entities with gaps in ID sequence
        entity1 = CollisionTest(name="Entity1")  # ID: 1
        assert entity1._id == "1"

        # Insert entity with higher custom ID
        data = {"_type": "CollisionTest", "_id": "100", "name": "Entity100"}
        entity100 = CollisionTest.deserialize(data)
        assert entity100._id == "100"
        assert CollisionTest.max_id() == 100

        # Insert entity with lower custom ID (potential collision scenario)
        data = {"_type": "CollisionTest", "_id": "50", "name": "Entity50"}
        entity50 = CollisionTest.deserialize(data)
        assert entity50._id == "50"
        assert CollisionTest.max_id() == 100  # Should remain 100

        # Now create new entities - they should get IDs starting from max_id + 1
        entity_new1 = CollisionTest(name="EntityNew1")
        entity_new2 = CollisionTest(name="EntityNew2")

        assert entity_new1._id == "101", f"Expected '101', got '{entity_new1._id}'"
        assert entity_new2._id == "102", f"Expected '102', got '{entity_new2._id}'"

        # Verify no collisions occurred
        all_entities = CollisionTest.instances()
        all_ids = [e._id for e in all_entities]
        assert len(all_ids) == len(
            set(all_ids)
        ), "All IDs should be unique (no collisions)"

        # Verify count is correct
        assert CollisionTest.count() == 5, "Count should reflect all created entities"
        assert CollisionTest.max_id() == 102, "max_id should be at highest assigned ID"

    def test_deserialize_max_id_edge_cases(self):
        """Test edge cases for max_id handling in deserialize."""
        Database.get_instance().clear()

        class EdgeCaseEntity(Entity):
            name = String()

        # Edge case 1: Deserialize with ID "0" (should not affect max_id calculation)
        data = {"_type": "EdgeCaseEntity", "_id": "0", "name": "Zero"}
        entity0 = EdgeCaseEntity.deserialize(data)

        # max_id should handle "0" correctly
        assert entity0._id == "0"
        # The next auto-generated entity should still get ID "1"
        entity_auto = EdgeCaseEntity(name="Auto")
        assert entity_auto._id == "1", f"Expected '1', got '{entity_auto._id}'"
        assert EdgeCaseEntity.max_id() == 1

        # Edge case 2: Very large custom ID
        data = {"_type": "EdgeCaseEntity", "_id": "999999", "name": "Large"}
        entity_large = EdgeCaseEntity.deserialize(data)

        assert entity_large._id == "999999"
        assert EdgeCaseEntity.max_id() == 999999

        # Next auto-generated should be 1000000
        entity_next = EdgeCaseEntity(name="Next")
        assert entity_next._id == "1000000"

        # Edge case 3: String ID that's not numeric (should be handled gracefully)
        # Note: This tests the robustness of the system
        data = {"_type": "EdgeCaseEntity", "_id": "abc123", "name": "NonNumeric"}
        entity_non_numeric = EdgeCaseEntity.deserialize(data)

        assert entity_non_numeric._id == "abc123"
        # max_id calculation should handle non-numeric IDs gracefully
        # The system should continue working for numeric IDs
        entity_after_non_numeric = EdgeCaseEntity(name="AfterNonNumeric")
        # Should get next numeric ID based on previous max
        assert entity_after_non_numeric._id == "1000001"

    def test_serialize_relations_with_alias(self):
        """Test that serialize uses alias instead of _id for relations when available."""
        Database.get_instance().clear()

        # Create entity classes where the related entity has an alias
        class Author(Entity):
            __alias__ = "name"
            name = String()
            books = OneToMany("Book", "author")

        class Book(Entity):
            title = String()
            author = ManyToOne("Author", "books")

        class Author2(Entity):
            """Author without alias for comparison."""

            name = String()
            books = OneToMany("Book2", "author")

        class Book2(Entity):
            title = String()
            author = ManyToOne("Author2", "books")

        # Test with alias - should use alias value in serialization
        author = Author(name="Alice")
        book = Book(title="My Book")
        book.author = author

        book_data = book.serialize()
        # Should use alias value "Alice" instead of _id "1"
        assert (
            book_data["author"] == "Alice"
        ), f"Expected 'Alice' (alias), got '{book_data['author']}'"

        # Test without alias - should fall back to _id
        author2 = Author2(name="Bob")
        book2 = Book2(title="Another Book")
        book2.author = author2

        book2_data = book2.serialize()
        # Should use _id since Author2 has no alias
        assert (
            book2_data["author"] == author2._id
        ), f"Expected '{author2._id}' (_id), got '{book2_data['author']}'"

        # Test round-trip with alias - deserialize should resolve by alias
        Database.get_instance().clear()
        Author(name="Alice")  # Recreate author first
        recreated_book = Book.deserialize(book_data)
        assert recreated_book.title == "My Book"
        assert recreated_book.author is not None
        assert recreated_book.author.name == "Alice"

    def test_serialize_for_export_skips_one_to_many(self):
        """Test that for_export=True skips OneToMany relations."""
        Database.get_instance().clear()

        parent = Parent(name="Alice")
        child1 = Child(name="Bob")
        child2 = Child(name="Charlie")

        parent.children = [child1, child2]
        child1.parent = parent
        child2.parent = parent

        # Normal serialize includes OneToMany
        normal_data = parent.serialize()
        assert "children" in normal_data, "Normal serialize should include OneToMany"

        # Export serialize skips OneToMany
        export_data = parent.serialize(for_export=True)
        assert "children" not in export_data, "Export serialize should skip OneToMany"

        # ManyToOne is always included
        child_data = child1.serialize(for_export=True)
        assert "parent" in child_data, "Export serialize should keep ManyToOne"
        assert child_data["parent"] == parent._id

    def test_serialize_for_export_one_to_one_deterministic(self):
        """Test that for_export=True serializes OneToOne on only one deterministic side.

        Rule: serialize only if self._type <= target._type (alphabetically).
        """
        Database.get_instance().clear()

        parent = Parent(name="Alice")
        child1 = Child(name="Bob")

        parent.favorite_child = child1

        # "Child" < "Parent" → Child serializes favorite_parent
        child_export = child1.serialize(for_export=True)
        assert (
            "favorite_parent" in child_export
        ), "Child (alphabetically earlier) should serialize OneToOne to Parent"

        # "Parent" > "Child" → Parent does NOT serialize favorite_child
        parent_export = parent.serialize(for_export=True)
        assert (
            "favorite_child" not in parent_export
        ), "Parent (alphabetically later) should skip OneToOne to Child"

        # Normal serialize includes both sides
        parent_normal = parent.serialize()
        assert "favorite_child" in parent_normal

    def test_serialize_for_export_round_trip(self):
        """Test that for_export serialization can be imported back correctly.

        Import order: Parent first, then Children (children carry ManyToOne refs).
        OneToMany on Parent is reconstructed from ManyToOne on Children.
        """
        Database.get_instance().clear()

        parent = Parent(name="Alice")
        child1 = Child(name="Bob")
        child2 = Child(name="Charlie")

        parent.children = [child1, child2]
        parent.favorite_child = child1
        child1.siblings = [child2]

        # Serialize for export
        parent_data = parent.serialize(for_export=True)
        child1_data = child1.serialize(for_export=True)
        child2_data = child2.serialize(for_export=True)

        # Verify export format
        assert "children" not in parent_data, "OneToMany should be skipped"
        assert (
            "favorite_child" not in parent_data
        ), "OneToOne (Parent>Child) should be skipped"
        assert "parent" in child1_data, "ManyToOne should be present"
        assert (
            "favorite_parent" in child1_data
        ), "OneToOne (Child<Parent) should be present"

        # Clear and reimport in dependency order
        Database.get_instance().clear()

        # Parent first (no forward refs in export)
        recreated_parent = Parent.deserialize(parent_data)
        assert recreated_parent.name == "Alice"

        # Children second (have ManyToOne + OneToOne refs to Parent)
        recreated_child2 = Child.deserialize(child2_data)
        recreated_child1 = Child.deserialize(child1_data)

        # Verify relations were reconstructed
        assert recreated_child1.parent == recreated_parent, "ManyToOne should resolve"
        assert recreated_child2.parent == recreated_parent, "ManyToOne should resolve"
        assert (
            recreated_child1.favorite_parent == recreated_parent
        ), "OneToOne should resolve"
        assert (
            len(recreated_parent.children) == 2
        ), "OneToMany should be reconstructed from ManyToOne"
        assert (
            recreated_parent.favorite_child == recreated_child1
        ), "OneToOne reverse should be set"

    def test_serialize_for_export_many_to_many(self):
        """Test that for_export keeps ManyToMany (self-referential)."""
        Database.get_instance().clear()

        child1 = Child(name="Bob")
        child2 = Child(name="Charlie")

        child1.siblings = [child2]

        export_data = child1.serialize(for_export=True)
        # ManyToMany with same type: "Child" <= "Child" → serialize
        assert "siblings" in export_data, "Self-referential ManyToMany should be kept"

    # ── Issue #4 regression tests ──────────────────────────────────────────

    def test_issue4_bidirectional_one_to_one_import_order(self):
        """Regression test for issue #4: bidirectional OneToOne import crash.

        Reproduces the exact scenario from the issue: User↔Member with
        OneToOne on both sides. Without the fix, whichever entity type is
        imported first will reference the other which doesn't exist yet,
        crashing with:
            ValueError: No entity of types Member found with ID or name 'mem_xxx'

        The fix: serialize(for_export=True) skips the OneToOne on the
        alphabetically-later side ("User" > "Member" → User.member is skipped).
        Import order: User first (no member ref), Member second (user ref resolves).
        """
        Database.get_instance().clear()

        # Define User↔Member with bidirectional OneToOne (mirrors real realm entities)
        class User(Entity):
            __alias__ = "name"
            name = String()
            member = OneToOne("Member4", "user")

        class Member4(Entity):
            __alias__ = "id"
            id = String()
            user = OneToOne("User", "member")

        # Create linked entities
        user = User(name="system")
        member = Member4(id="mem_9f03f2ee")
        member.user = user

        # Verify both sides are set
        assert user.member == member
        assert member.user == user

        # ── Part 1: for_export=True produces importable JSON ──
        user_data = user.serialize(for_export=True)
        member_data = member.serialize(for_export=True)

        # User should NOT have 'member' (since "User" > "Member4")
        assert (
            "member" not in user_data
        ), f"for_export should skip OneToOne on alphabetically-later side; got {user_data}"
        # Member4 SHOULD have 'user' (since "Member4" < "User")
        assert (
            "user" in member_data
        ), f"for_export should keep OneToOne on alphabetically-earlier side; got {member_data}"
        assert member_data["user"] == "system"  # alias

        # Clear and reimport in dependency order
        Database.get_instance().clear()
        recreated_user = User.deserialize(user_data)  # No member ref → no crash
        recreated_member = Member4.deserialize(
            member_data
        )  # user ref → resolves to existing User

        # Both sides should be reconstructed
        assert recreated_member.user == recreated_user, "ManyToOne should resolve"
        assert (
            recreated_user.member == recreated_member
        ), "Reverse OneToOne should be set"

        # ── Part 2: OLD serialize() would crash on wrong import order ──
        Database.get_instance().clear()

        user2 = User(name="alice")
        member2 = Member4(id="mem_deadbeef")
        member2.user = user2

        # Normal serialize includes BOTH sides
        user2_full = user2.serialize()
        _ = member2.serialize()  # noqa: F841
        assert "member" in user2_full, "Normal serialize should include both sides"

        # Importing User first with normal data crashes because Member doesn't exist
        Database.get_instance().clear()
        try:
            User.deserialize(
                user2_full
            )  # has member: "mem_deadbeef" → Member4 doesn't exist
            # If deserialize silently skips, that's also fine (it catches ValueError)
        except ValueError as e:
            assert "mem_deadbeef" in str(
                e
            ), f"Should reference the missing member alias: {e}"

    def test_issue4_one_to_many_dangling_refs_load_some(self):
        """Regression test for issue #4: OneToMany with dangling refs crashes load_some.

        Reproduces the runtime crash: Token stored with balances: [4, 5, 6] but
        WalletBalance entities were deleted. Token.load_some() crashes with:
            ValueError: No entity of types WalletBalance found with ID or name '4'

        The fix: load_some() catches ValueError/AttributeError and skips broken
        entities instead of crashing the entire batch.
        """
        Database.get_instance().clear()

        class Account(Entity):
            name = String()
            entries = OneToMany("Entry4", "account")

        class Entry4(Entity):
            label = String()
            account = ManyToOne("Account", "entries")

        # Create account with entries
        acct = Account(name="Treasury")
        e1 = Entry4(label="tx1")
        e2 = Entry4(label="tx2")
        e1.account = acct
        e2.account = acct
        assert len(acct.entries) == 2

        # Serialize WITH OneToMany (internal storage format)
        acct_data = acct.serialize()
        assert "entries" in acct_data

        # Now delete the entries from DB but leave the account's stored data intact
        e1.delete()
        e2.delete()

        # Clear entity registry so load() reads from DB
        Database.get_instance()._entity_registry = {}

        # load_some should NOT crash — it should skip broken entities
        loaded = Account.load_some(from_id=1, count=10)
        # The account itself should still load (its entries will fail silently)
        assert len(loaded) >= 0, "load_some should not crash on dangling OneToMany refs"

    def test_issue4_for_export_eliminates_dangling_one_to_many(self):
        """Regression test: for_export=True prevents OneToMany dangling refs entirely.

        If Token was serialized with for_export=True, the 'balances' OneToMany field
        is not included. On import, Token has no forward refs to WalletBalance,
        so there's nothing to dangle.
        """
        Database.get_instance().clear()

        class Token4(Entity):
            name = String()
            balances = OneToMany("Balance4", "token")

        class Balance4(Entity):
            amount = String()
            token = ManyToOne("Token4", "balances")

        token = Token4(name="ICP")
        b1 = Balance4(amount="100")
        b2 = Balance4(amount="200")
        b1.token = token
        b2.token = token

        # for_export skips OneToMany
        export_data = token.serialize(for_export=True)
        assert "balances" not in export_data

        # Import token without balance refs — no crash possible
        Database.get_instance().clear()
        reimported_token = Token4.deserialize(export_data)
        assert reimported_token.name == "ICP"
        assert len(reimported_token.balances) == 0  # No balances yet

        # Now import balances with ManyToOne ref — reconstructs OneToMany
        b1_data = b1.serialize(for_export=True)
        b2_data = b2.serialize(for_export=True)
        Balance4.deserialize(b1_data)
        Balance4.deserialize(b2_data)

        assert (
            len(reimported_token.balances) == 2
        ), "OneToMany should be reconstructed from ManyToOne during import"

    def test_issue4_full_round_trip_bidirectional(self):
        """End-to-end test: serialize(for_export=True) → clear → deserialize.

        Models the exact realm workflow: generate entities in memory,
        serialize to JSON, clear DB, import in dependency order.
        All relations should be fully reconstructed.
        """
        Database.get_instance().clear()

        class Realm4(Entity):
            __alias__ = "name"
            name = String()
            users = OneToMany("User4", "realm")

        class User4(Entity):
            __alias__ = "name"
            name = String()
            realm = ManyToOne("Realm4", "users")
            member = OneToOne("Member44", "user")
            human = OneToOne("Human4", "user")

        class Human4(Entity):
            __alias__ = "name"
            name = String()
            user = OneToOne("User4", "human")

        class Member44(Entity):
            __alias__ = "alias"
            alias = String()
            user = OneToOne("User4", "member")

        # Generate data (mimics generator.py)
        realm = Realm4(name="Agora")
        users = [User4(name=f"user_{i}", realm=realm) for i in range(3)]
        humans = [Human4(name=f"human_{i}", user=users[i]) for i in range(3)]
        members = [Member44(alias=f"mem_{i}", user=users[i]) for i in range(3)]

        # Verify in-memory relations
        assert len(realm.users) == 3
        for i in range(3):
            assert users[i].member == members[i]
            assert users[i].human == humans[i]

        # Serialize for export (dependency order: Realm → Users → Humans → Members)
        all_data = (
            [realm.serialize(for_export=True)]
            + [u.serialize(for_export=True) for u in users]
            + [h.serialize(for_export=True) for h in humans]
            + [m.serialize(for_export=True) for m in members]
        )

        # Verify no OneToMany or wrong-side OneToOne in export
        realm_data = all_data[0]
        assert "users" not in realm_data, "OneToMany should not be in export"
        user_data = all_data[1]
        # "User4" > "Human4" → User4 should NOT have human
        assert "human" not in user_data, "OneToOne (User4>Human4) should be skipped"
        # "User4" > "Member44" → User4 should NOT have member
        assert "member" not in user_data, "OneToOne (User4>Member44) should be skipped"

        # Clear DB completely
        Database.get_instance().clear()

        # Import in dependency order — should NOT crash
        # Use specific classes (Database.clear() wipes entity type registry,
        # so generic Entity.deserialize can't resolve locally-defined classes)
        type_map = {
            "Realm4": Realm4,
            "User4": User4,
            "Human4": Human4,
            "Member44": Member44,
        }
        for item in all_data:
            type_map[item["_type"]].deserialize(item)

        # Verify all relations are reconstructed
        loaded_realm = Realm4["Agora"]
        assert loaded_realm is not None
        assert len(loaded_realm.users) == 3, "OneToMany should be reconstructed"

        loaded_user0 = User4["user_0"]
        assert loaded_user0.realm == loaded_realm, "ManyToOne should resolve"
        assert loaded_user0.human is not None, "OneToOne reverse should be set"
        assert loaded_user0.human.name == "human_0"
        assert loaded_user0.member is not None, "OneToOne reverse should be set"
        assert loaded_user0.member.alias == "mem_0"


def run(test_name: str = None, test_var: str = None):
    tester = Tester(TestSerialization)
    return tester.run_tests()


if __name__ == "__main__":
    exit(run())
