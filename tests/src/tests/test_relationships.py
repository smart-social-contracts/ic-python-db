"""Tests for relationship properties in IC Python DB."""

from tester import Tester

from ic_python_db import *


class Person(Entity):
    """Test person entity with one-to-one relationship to Profile."""

    name = String()
    profile = OneToOne(["Profile"], "person")  # One-to-one with Profile


class Profile(Entity):
    """Test profile entity with one-to-one relationship to person."""

    bio = String()
    person = OneToOne(["Person"], "profile")  # One-to-one with Person


class Department(Entity):
    """Test department entity with one-to-many relationship to employees."""

    name = String()
    employees = OneToMany(["Employee"], "department")  # One-to-many with Employee
    manager = OneToOne(
        ["Employee"], "managed_department"
    )  # One-to-one with Employee (manager)


class Employee(Entity):
    """Test employee entity."""

    name = String()
    department = ManyToOne(["Department"], "employees")  # Many-to-one with Department
    managed_department = OneToOne(
        ["Department"], "manager"
    )  # One-to-one with Department (as manager)  # Reference to parent department


class Student(Entity):
    """Test student entity with many-to-many relationship to courses."""

    name = String()
    courses = ManyToMany(["Course"], "students")


class Course(Entity):
    """Test course entity with many-to-many relationship to students."""

    name = String()
    students = ManyToMany(["Student"], "courses")


class TestRelationships:
    """Test cases for relationship properties."""

    def setUp(self):
        """Set up test database."""
        Database.get_instance().clear()

    def test_one_to_one(self):
        """Test one-to-one relationships."""
        # Create person and profile
        person = Person(name="Alice")
        profile = Profile(bio="Software Engineer")

        # Link person and profile
        person.profile = profile

        # Verify relationships
        assert person.profile == profile
        assert profile.person == person

        # Verify that we can't assign multiple profiles
        profile2 = Profile(bio="Another bio")
        Tester.assert_raises(
            ValueError, lambda: setattr(person, "profile", [profile, profile2])
        )

        # Test replacing profile
        new_profile = Profile(bio="Updated bio")
        person.profile = new_profile

        # Verify that old profile is unlinked and new profile is linked
        assert person.profile == new_profile
        assert new_profile.person == person
        assert profile.person is None

        # Test department manager one-to-one relationship
        dept = Department(name="Engineering")
        emp = Employee(name="Bob")

        dept.manager = emp
        assert dept.manager == emp
        assert emp.managed_department == dept

    def test_one_to_many(self):
        """Test one-to-many relationships."""
        # Create a department and employees
        dept = Department(name="Engineering")

        emp1 = Employee(name="Alice")
        emp2 = Employee(name="Bob")
        emp3 = Employee(name="Charlie")

        # Add employees to department via the ManyToOne side
        emp1.department = dept
        emp2.department = dept

        # Verify relationships
        assert len(dept.employees) == 2
        assert emp1.department == dept
        assert emp2.department == dept

        # Verify that we can't assign multiple departments
        dept2 = Department(name="Sales")
        Tester.assert_raises(
            ValueError, lambda: setattr(emp1, "department", [dept, dept2])
        )

        # Add another employee
        emp3.department = dept

        # Verify relationships
        assert len(dept.employees) == 3
        assert emp3.department == dept

        # Move employee to new department
        emp1.department = dept2

        # Verify relationships
        assert len(dept.employees) == 2
        assert len(dept2.employees) == 1
        assert emp1.department == dept2

        # Test that employee can't be in multiple departments
        Tester.assert_raises(
            ValueError, lambda: setattr(emp1, "department", [dept, dept2])
        )

        # Verify OneToMany cannot be set directly
        Tester.assert_raises(AttributeError, lambda: setattr(dept, "employees", [emp1]))

    def test_many_to_many(self):
        """Test many-to-many relationships."""
        # Create students
        student1 = Student(name="Alice")
        student2 = Student(name="Bob")

        # Create courses
        course1 = Course(name="Math")
        course2 = Course(name="Physics")
        course3 = Course(name="Chemistry")

        # Add courses to students
        student1.courses = [course1, course2]
        student2.courses = [course2, course3]

        # Verify relationships from both sides
        assert len(student1.courses) == 2
        assert len(student2.courses) == 2
        assert len(course1.students) == 1
        assert len(course2.students) == 2
        assert len(course3.students) == 1

        # Remove a course from student
        student1.courses = [course1]

        # Verify relationships are updated on both sides
        assert len(student1.courses) == 1
        assert len(course2.students) == 1
        assert course1 in student1.courses
        assert student1 in course1.students
        assert student1 not in course2.students

        # Add student to multiple courses at once
        student1.courses = [course1, course2, course3]

        # Verify all relationships are updated
        assert len(student1.courses) == 3
        assert len(course2.students) == 2
        assert len(course3.students) == 2
        for course in [course1, course2, course3]:
            assert course in student1.courses
            assert student1 in course.students


class BaseParent(Entity):
    """Base entity with a OneToMany relationship."""

    name = String()
    children = OneToMany("Child", "parent")


class DerivedParent(BaseParent):
    """Subclass that inherits the 'children' OneToMany from BaseParent."""

    extra = String()


class Child(Entity):
    """Entity with ManyToOne pointing to DerivedParent (reverse='children').

    The 'children' OneToMany lives on BaseParent but must be found via MRO
    when the target is a DerivedParent instance.
    """

    name = String()
    parent = ManyToOne("DerivedParent", "children")


class TestInheritedRelationships:
    """Regression tests: ManyToOne must find inherited OneToMany reverse props."""

    def setUp(self):
        Database.get_instance().clear()

    def test_many_to_one_with_inherited_reverse(self):
        """ManyToOne.__set__ should walk MRO to find inherited OneToMany."""
        parent = DerivedParent(name="Parent", extra="x")
        child = Child(name="Child")

        # This used to raise:
        #   ValueError: Reverse property 'children' not found in DerivedParent entity
        child.parent = parent

        assert child.parent == parent
        assert child in parent.children
        assert len(parent.children) == 1

    def test_multiple_children_with_inherited_reverse(self):
        """Multiple children can be added to a subclassed parent."""
        parent = DerivedParent(name="Parent", extra="y")
        c1 = Child(name="C1")
        c2 = Child(name="C2")

        c1.parent = parent
        c2.parent = parent

        assert len(parent.children) == 2
        assert c1.parent == parent
        assert c2.parent == parent


class TestLoadPreservesRelations:
    """Regression: Entity.load() must preserve relations populated by __init__.

    Previously, load() unconditionally did ``entity._relations = {}`` after
    __init__ had already resolved relation descriptors, wiping all links.
    """

    def setUp(self):
        Database.get_instance().clear()

    def test_one_to_many_survives_load(self):
        """OneToMany relations must survive save-then-load cycle."""
        dept = Department(name="Engineering")
        emp1 = Employee(name="Alice")
        emp2 = Employee(name="Bob")
        emp1.department = dept
        emp2.department = dept

        # Sanity: relations work in memory
        assert len(dept.employees) == 2
        assert emp1.department == dept

        # Clear entity registry so load() must reconstruct from DB
        Database.get_instance()._entity_registry.clear()
        Entity._context.clear()

        loaded_dept = Department.load(str(dept._id))
        assert loaded_dept is not None
        assert len(loaded_dept.employees) == 2
        names = {e.name for e in loaded_dept.employees}
        assert names == {"Alice", "Bob"}

    def test_many_to_one_survives_load(self):
        """ManyToOne relations must survive save-then-load cycle."""
        dept = Department(name="Sales")
        emp = Employee(name="Carol")
        emp.department = dept

        assert emp.department == dept

        Database.get_instance()._entity_registry.clear()
        Entity._context.clear()

        loaded_emp = Employee.load(str(emp._id))
        assert loaded_emp is not None
        assert loaded_emp.department is not None
        assert loaded_emp.department.name == "Sales"

    def test_one_to_one_survives_load(self):
        """OneToOne relations must survive save-then-load cycle."""
        person = Person(name="Dave")
        profile = Profile(bio="Engineer")
        person.profile = profile

        assert person.profile == profile
        assert profile.person == person

        Database.get_instance()._entity_registry.clear()
        Entity._context.clear()

        loaded_person = Person.load(str(person._id))
        assert loaded_person is not None
        assert loaded_person.profile is not None
        assert loaded_person.profile.bio == "Engineer"


class TestReverseIndexResolution:
    """Core regression test for GitHub issue #9.

    The fundamental problem: in the old architecture, OneToMany/ManyToMany
    resolution depended on loading ALL child entities (via instances()) to
    populate an in-memory _relations dict. This meant:

    1. After a canister upgrade (or GC clearing weak refs), calling
       parent.children returned [] unless you first called
       list(Child.instances()) to repopulate the in-memory dict.

    2. As entity count grew, this O(max_id) scan exceeded IC instruction
       limits, making relationship access impossible at scale.

    The fix: persisted reverse indexes in stable storage. Relationships
    are now resolved in O(k) by reading a small index entry, with no
    dependence on loading unrelated entities.
    """

    def setUp(self):
        Database.get_instance().clear()

    def test_one_to_many_without_loading_all_children(self):
        """OneToMany resolves from reverse index — no need to load all children.

        This is the exact scenario that broke the demo simulator:
        parent.children must work by loading ONLY the parent, without
        scanning all Child entities first.
        """
        dept = Department(name="Engineering")
        emp1 = Employee(name="Alice")
        emp2 = Employee(name="Bob")
        emp1.department = dept
        emp2.department = dept

        # Also create unrelated entities to prove we don't scan them
        other_dept = Department(name="Sales")
        emp3 = Employee(name="Carol")
        emp3.department = other_dept

        # Simulate canister upgrade: clear ALL in-memory state
        Database.get_instance()._entity_registry.clear()
        Entity._context.clear()

        # Load ONLY the parent — do NOT load any children first
        loaded_dept = Department.load(str(dept._id))

        # OneToMany must resolve without having loaded children beforehand
        employees = loaded_dept.employees
        assert len(employees) == 2, (
            f"Expected 2 employees from reverse index, got {len(employees)}. "
            "This fails in the old architecture unless instances() is called first."
        )
        names = {e.name for e in employees}
        assert names == {"Alice", "Bob"}

    def test_many_to_many_without_loading_all_related(self):
        """ManyToMany resolves from reverse index without scanning."""
        s1 = Student(name="Alice")
        s2 = Student(name="Bob")
        c1 = Course(name="Math")
        c2 = Course(name="Physics")

        s1.courses = [c1, c2]
        s2.courses = [c1]

        # Simulate canister upgrade
        Database.get_instance()._entity_registry.clear()
        Entity._context.clear()

        # Load ONLY course1 — do NOT load students first
        loaded_course = Course.load(str(c1._id))

        # ManyToMany must resolve from reverse index
        students = loaded_course.students
        assert (
            len(students) == 2
        ), f"Expected 2 students from reverse index, got {len(students)}."
        names = {s.name for s in students}
        assert names == {"Alice", "Bob"}

    def test_one_to_one_inverse_without_loading_target(self):
        """OneToOne inverse side resolves from reverse index."""
        person = Person(name="Eve")
        profile = Profile(bio="Researcher")
        person.profile = profile  # person owns FK

        # Simulate canister upgrade
        Database.get_instance()._entity_registry.clear()
        Entity._context.clear()

        # Load ONLY the profile (inverse side) — do NOT load person first
        loaded_profile = Profile.load(str(profile._id))

        # Inverse OneToOne must resolve from reverse index
        assert loaded_profile.person is not None, (
            "Inverse OneToOne must resolve from reverse index without "
            "loading the owning entity first."
        )
        assert loaded_profile.person.name == "Eve"

    def test_scalability_no_full_scan(self):
        """Relationship access cost is O(k) not O(max_id).

        Create many unrelated entities and verify that accessing a
        relationship on one parent doesn't require touching them.
        """
        dept = Department(name="Target")
        emp = Employee(name="OnlyEmployee")
        emp.department = dept

        # Create 50 unrelated employees in a different department
        other = Department(name="Other")
        for i in range(50):
            e = Employee(name=f"Other_{i}")
            e.department = other

        # Simulate canister upgrade
        Database.get_instance()._entity_registry.clear()
        Entity._context.clear()

        # Access should only load the 1 child, not all 51 employees
        loaded_dept = Department.load(str(dept._id))
        employees = loaded_dept.employees
        assert len(employees) == 1
        assert employees[0].name == "OnlyEmployee"


def run(test_name: str = None, test_var: str = None):
    tester = Tester(TestRelationships)
    results = tester.run_tests()
    tester2 = Tester(TestInheritedRelationships)
    results2 = tester2.run_tests()
    tester3 = Tester(TestLoadPreservesRelations)
    results3 = tester3.run_tests()
    tester4 = Tester(TestReverseIndexResolution)
    results4 = tester4.run_tests()
    return results or results2 or results3 or results4


if __name__ == "__main__":
    exit(run())
