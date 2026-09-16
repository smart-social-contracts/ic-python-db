"""Tests for entity functionality in IC Python DB."""

from tester import Tester

from ic_python_db import *


class Person(Entity):
    """Test entity class."""

    __alias__ = "name"
    name = String(min_length=2, max_length=50)
    age = Integer()
    department = ManyToOne("Department", "employees")


class Department(Entity):
    name = String(min_length=2, max_length=50)
    employees = OneToMany("Person", "department")


class TestEntity:
    def setUp(self):
        """Reset Entity class variables before each test."""
        Database.get_instance().clear()

    def test_entity_creation_and_save(self):
        """Test creating and saving an entity."""
        person = Person(name="John", age=30)
        loaded = Person[person._id]
        assert loaded is not None
        assert loaded.name == "John"
        assert loaded.age == 30

    def test_entity_update(self):
        """Test updating an entity."""
        person = Person(name="John", age=30)
        person.age = 31

        loaded = Person[person._id]
        assert loaded.age == 31

    def test_entity_relations(self):
        """Test entity relations using ManyToOne/OneToMany descriptors."""
        dept = Department(name="IT")
        person = Person(name="John")

        # Set relation via ManyToOne
        person.department = dept

        # Verify relationship from both sides
        loaded_person = Person[person._id]
        loaded_dept = Department[dept._id]

        assert loaded_person.department == dept
        assert loaded_person in loaded_dept.employees

    def test_entity_duplicate_key(self):
        """Test that saving an entity with a duplicate ID raises an error."""
        # Create and save first entity
        Person(_id="test_id", name="John", age=30)

        # Try to create and save second entity with same ID
        try:
            Person(_id="test_id", name="Jane", age=25)
            assert False, "Entity Person@test_id already exists"
        except ValueError as e:
            assert str(e) == "Entity Person@test_id already exists"

    def test_getitem_by_id(self):
        """Test loading an entity by ID using class[id] syntax."""
        # Create and save an entity
        person = Person(_id="John", name="John", age=30)

        # Load using class[id] syntax
        loaded_person = Person[person._id]
        assert loaded_person is not None
        assert loaded_person._id == "John"
        assert loaded_person.age == 30

        # Test with non-existent ID
        assert Person["non_existent"] is None

    def test_entity_duplicate_relation(self):
        """Test that setting same relation twice doesn't duplicate."""
        dept = Department(name="IT")
        person = Person(name="John")

        # Setting same ManyToOne relation twice
        person.department = dept
        person.department = dept

        # Should still only have one employee
        assert len(dept.employees) == 1

    def test_entity_alias(self):
        """Test entity lookup by alias (__alias__ functionality)."""
        # Create a person with a specific name
        person = Person(name="Jane", age=28)

        # Look up by ID
        by_id = Person[person._id]
        assert by_id is not None
        assert by_id._id == person._id

        # Look up by the aliased field (name)
        by_alias = Person["Jane"]
        assert by_alias is not None
        assert by_alias._id == person._id

        # Make sure they're the same entity
        assert by_id == by_alias

        # Create a numeric ID person to test numeric ID handling
        numeric_person = Person(_id="42", name="NumericTest", age=35)

        # Look up by numeric and string ID
        assert Person[42] == numeric_person
        assert Person["42"] == numeric_person

        # Verify alias lookup still works
        assert Person["NumericTest"] == numeric_person

        # Test that alias lookup returns None after entity deletion

        # Delete the first person (Jane)
        person.delete()

        # Verify that lookup by ID returns None
        assert Person[person._id] is None

        # Verify that lookup by alias (name) returns None
        assert Person["Jane"] is None

    def test_entity_alias_specific_field_lookup(self):
        """Test entity lookup by specific field using tuple syntax."""
        # Create persons
        person1 = Person(name="Alice", age=30)
        person2 = Person(name="Bob", age=25)

        # Lookup by specific field (tuple syntax)
        found = Person["name", "Alice"]
        assert found is not None
        assert found._id == person1._id
        assert found.name == "Alice"

        # Lookup another person
        found2 = Person["name", "Bob"]
        assert found2 is not None
        assert found2._id == person2._id

        # Lookup non-existent value returns None
        not_found = Person["name", "NonExistent"]
        assert not_found is None

        # Lookup by non-aliased field returns None (no mapping stored)
        not_found2 = Person["age", "30"]
        assert not_found2 is None

        # Verify backward compatibility: single-value syntax still works
        found_by_alias = Person["Alice"]
        assert found_by_alias is not None
        assert found_by_alias._id == person1._id

        # Test invalid tuple - non-string field name raises TypeError
        try:
            Person[123, "Alice"]
            assert False, "Should have raised TypeError"
        except TypeError:
            pass

        # Test invalid tuple - empty string field name raises TypeError
        try:
            Person["", "Alice"]
            assert False, "Should have raised TypeError"
        except TypeError:
            pass

    def test_instances_basic(self):
        """Test basic functionality of instances() method."""
        # Create multiple persons
        person1 = Person(name="John", age=30)
        person2 = Person(name="Jane", age=25)

        # Retrieve all Person instances by explicitly calling instances()
        persons = Person.instances()

        # Check that we have the correct number of instances
        print("len(persons)", len(persons))
        assert len(persons) == 2

        # Check that the instances match the saved persons
        saved_ids = {person1._id, person2._id}
        retrieved_ids = {p._id for p in persons}
        assert saved_ids == retrieved_ids

    def test_instances_with_type_name(self):
        """Test instances() method with explicit type name."""
        # Create a Person and a Department
        Person(name="John", age=30)
        Department(name="Engineering")

        # Retrieve Person instances using explicit type name by calling instances()
        persons = Person.instances()
        departments = Department.instances()

        # Check that we have the correct number of instances for each type
        print("len(persons)", len(persons))
        assert len(persons) == 1
        assert len(departments) == 1

        # Check that the instances are of the correct type
        assert all(isinstance(p, Person) for p in persons)
        assert all(isinstance(d, Department) for d in departments)

    def test_instances_empty(self):
        """Test instances() method when no instances exist."""

        # Create a new entity type
        class NewEntity(Entity):
            pass

        # Retrieve instances by calling instances()
        instances = NewEntity.instances()

        # Check that no instances are returned
        assert len(instances) == 0

    def test_instances_with_multiple_types(self):
        """Test instances() method with multiple entity types."""
        # Create multiple entities of different types
        Person(name="John", age=30)
        Person(name="Jane", age=25)
        Department(name="Engineering")

        # Retrieve instances for each type by calling instances()
        persons = Person.instances()
        departments = Department.instances()

        # Check the number of instances for each type
        assert len(persons) == 2
        assert len(departments) == 1

    def test_entity_inheritance(self):
        """Test entity inheritance and type-based instance querying."""

        # Define test classes
        class Animal(Entity, TimestampedMixin):
            pass

        class Dog(Animal):
            pass

        class Cat(Animal):
            pass

        # Create test instances
        animal_a = Animal(name="Alice")
        dog_b = Dog(name="Bob")
        cat_c = Cat(name="Charlie")

        # Test instance querying
        all_animals = Animal.instances()
        assert len(all_animals) == 3
        assert any(a.name == "Alice" for a in all_animals)
        assert any(a.name == "Bob" for a in all_animals)
        assert any(a.name == "Charlie" for a in all_animals)

        dogs = Dog.instances()
        assert len(dogs) == 1
        assert dogs[0].name == "Bob"

        cats = Cat.instances()
        assert len(cats) == 1
        assert cats[0].name == "Charlie"

        # Clean up
        animal_a.delete()
        dog_b.delete()
        cat_c.delete()

    def test_load_some_basic(self):
        """Test basic load_some functionality."""
        # Create 15 test entities
        for i in range(15):
            Person(name=f"Person{i}")

        # Test first page (entities 1-10)
        first_page = Person.load_some(from_id=1, count=10)
        assert len(first_page) == 10
        assert first_page[0].name == "Person0"
        assert first_page[9].name == "Person9"

        # Test second page (entities 11-15)
        second_page = Person.load_some(from_id=11, count=10)
        assert len(second_page) == 5
        assert second_page[0].name == "Person10"
        assert second_page[4].name == "Person14"

        # Test with different count
        custom_page = Person.load_some(from_id=1, count=5)
        assert len(custom_page) == 5
        assert custom_page[0].name == "Person0"
        assert custom_page[4].name == "Person4"

    def test_load_some_edge_cases(self):
        """Test edge cases in load_some."""
        # Create 5 test entities
        for i in range(5):
            Person(name=f"Person{i}")

        # Test loading from start
        first_page = Person.load_some(from_id=1, count=10)
        assert len(first_page) == 5
        assert first_page[0].name == "Person0"
        assert first_page[4].name == "Person4"

        # Test loading from beyond last entity
        empty_page = Person.load_some(from_id=6, count=10)
        assert len(empty_page) == 0

    def test_load_some_errors(self):
        """Test load_some error handling."""
        # Test negative from_id
        try:
            Person.load_some(from_id=-1, count=10)
            assert False, "Should have raised ValueError for negative from_id"
        except ValueError as e:
            assert str(e) == "from_id must be at least 1"

        # Test zero count
        try:
            Person.load_some(from_id=1, count=0)
            assert False, "Should have raised ValueError for zero count"
        except ValueError as e:
            assert str(e) == "count must be at least 1"

        # Test negative count
        try:
            Person.load_some(from_id=1, count=-1)
            assert False, "Should have raised ValueError for negative count"
        except ValueError as e:
            assert str(e) == "count must be at least 1"

    def test_load_some_with_deleted_entities(self):
        """Test load_some with deleted entities."""
        # Create 10 test entities
        for i in range(10):
            Person(name=f"Person{i}")

        # Delete entities 5 and 6
        Person[5].delete()
        Person[6].delete()

        # Test loading from start (should skip deleted entities)
        first_page = Person.load_some(from_id=1, count=10)
        assert len(first_page) == 8
        assert first_page[0].name == "Person0"
        assert first_page[5].name == "Person7"

        # Test loading from beyond last entity
        empty_page = Person.load_some(from_id=11, count=10)
        assert len(empty_page) == 0

    def test_load_some_across_digit_buckets(self):
        """Ids 1..150 span three key lengths ("T@9" < "T@10" < "T@100" on the
        IC); pages must still come back in numeric order across those runs,
        with deleted ids skipped and paging cursors continuing correctly."""
        people = [Person(name=f"P{i}", age=i) for i in range(1, 151)]
        for i in (9, 10, 11, 99, 100, 101, 150):
            people[i - 1].delete()
        db = Database.get_instance()

        def ids(page):
            return [int(p._id) for p in page]

        # Cold: drop the identity map so entities really come from storage
        db.clear_registry()
        page = Person.load_some(from_id=1, count=20)
        assert ids(page) == [
            1,
            2,
            3,
            4,
            5,
            6,
            7,
            8,
            12,
            13,
            14,
            15,
            16,
            17,
            18,
            19,
            20,
            21,
            22,
            23,
        ]
        assert page[8].name == "P12" and page[8].age == 12

        db.clear_registry()
        page = Person.load_some(from_id=95, count=10)
        assert ids(page) == [95, 96, 97, 98, 102, 103, 104, 105, 106, 107]

        # Walk everything with a cursor and compare with the full set
        db.clear_registry()
        seen = []
        cursor = 1
        while True:
            page = Person.load_some(from_id=cursor, count=17)
            seen.extend(ids(page))
            if len(page) < 17:
                break
            cursor = int(page[-1]._id) + 1
        expected = [i for i in range(1, 151) if i not in (9, 10, 11, 99, 100, 101, 150)]
        assert seen == expected
        assert Person.count() == len(expected)
        assert len(Person.find({"age": 42})) == 1
        assert Person.find({"age": 100}) == []

    def test_load_some_ignores_custom_string_ids(self):
        """Custom ids that sort inside a numeric run are not sequential ids
        and must be skipped without ending the page early."""
        for i in range(1, 6):
            Person(name=f"Seq{i}")
        # In the 2-char run, "1x" and "2-" both sort between "19" and "20"
        # ('x' > '9', '-' < '0'); "abc" sorts after every numeric id.
        Person(name="Custom1", _id="1x")
        Person(name="Custom2", _id="2-")
        Person(name="Custom3", _id="abc")
        for i in range(6, 26):
            Person(name=f"Seq{i}")

        db = Database.get_instance()
        db.clear_registry()
        page = Person.load_some(from_id=1, count=100)
        assert [p._id for p in page] == [str(i) for i in range(1, 26)]
        assert Person["1x"].name == "Custom1"
        assert Person["2-"].name == "Custom2"
        assert Person["abc"].name == "Custom3"

        # Pages whose range window contains the custom ids must still be
        # filled up to `count` (custom ids end a raw page but not the result)
        for from_id, count, expected in [
            (18, 3, ["18", "19", "20"]),  # raw page ends on "1x"
            (18, 4, ["18", "19", "20", "21"]),  # raw page ends on "2-"
            (19, 2, ["19", "20"]),
            (20, 2, ["20", "21"]),
            (1, 3, ["1", "2", "3"]),
        ]:
            db.clear_registry()
            page = Person.load_some(from_id=from_id, count=count)
            assert [p._id for p in page] == expected, (
                from_id,
                count,
                [p._id for p in page],
            )

    def test_load_some_falls_back_without_range(self):
        """A storage backend with no range() (older CDK builds, simple test
        doubles) must still work via key probing and give identical results."""
        from ic_python_db.storage import Storage, supports_range

        class ProbeOnlyStorage(Storage):
            def __init__(self):
                self._data = {}
                self.gets = 0

            def insert(self, key, value):
                self._data[key] = value

            def get(self, key):
                self.gets += 1
                return self._data.get(key)

            def remove(self, key):
                del self._data[key]

            def items(self):
                return iter(self._data.items())

            def __contains__(self, key):
                return key in self._data

            def keys(self):
                return iter(self._data.keys())

        assert not supports_range(ProbeOnlyStorage())
        db = Database.get_instance()
        original = db._db_storage
        probe = ProbeOnlyStorage()
        # Mirror the current contents so both backends hold the same rows
        for k, v in original.items():
            probe.insert(k, v)
        try:
            db._db_storage = probe
            for i in range(1, 31):
                Person(name=f"P{i}")
            for i in (3, 4, 20):
                Person[str(i)].delete()
            db.clear_registry()
            page = Person.load_some(from_id=1, count=10)
            assert [int(p._id) for p in page] == [1, 2, 5, 6, 7, 8, 9, 10, 11, 12]
            assert Person.load_some(from_id=31, count=5) == []
            assert len(Person.find({"name": "P25"})) == 1

            # Same rows through the range path give the same answer
            db._db_storage = original
            for k, v in probe.items():
                original.insert(k, v)
            db.clear_registry()
            page_range = Person.load_some(from_id=1, count=10)
            assert [int(p._id) for p in page_range] == [1, 2, 5, 6, 7, 8, 9, 10, 11, 12]
        finally:
            db._db_storage = original

    def test_count_and_instances_method(self):
        """Test the count and instances method."""
        # Test count with no entities
        assert Person.count() == 0
        assert len(Person.instances()) == 0

        # Create some entities
        for i in range(5):
            Person(name=f"Person{i}", age=20 + i)
        assert Person.count() == 5
        assert len(Person.instances()) == 5

        for i in range(5):
            person = Person[f"Person{i}"]
            assert person.name == f"Person{i}"
            assert person.age == 20 + i

        # Delete some entities
        Person[1].delete()
        Person[2].delete()
        assert Person.count() == 3  # Count should still be 5 as it uses last_id
        assert len(Person.instances()) == 3

        # Create more entities
        for i in range(5, 10):
            Person(name=f"Person{i}", age=20 + i)
        assert Person.count() == 8
        assert len(Person.instances()) == 8


def run(test_name: str = None, test_var: str = None):
    tester = Tester(TestEntity)
    return tester.run_tests()


if __name__ == "__main__":
    exit(run())
