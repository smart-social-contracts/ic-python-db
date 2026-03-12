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

        # Add employees to department
        dept.employees = [emp1, emp2]

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
        dept.employees = [emp1, emp2, emp3]

        # Verify relationships
        assert len(dept.employees) == 3
        assert emp3.department == dept

        # Move employee to new department
        dept2.employees = [emp1]

        # Verify relationships
        assert len(dept.employees) == 2
        assert len(dept2.employees) == 1
        assert emp1.department == dept2

        # Test that employee can't be in multiple departments
        Tester.assert_raises(
            ValueError, lambda: setattr(emp1, "department", [dept, dept2])
        )

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


def run(test_name: str = None, test_var: str = None):
    tester = Tester(TestRelationships)
    results = tester.run_tests()
    tester2 = Tester(TestInheritedRelationships)
    results2 = tester2.run_tests()
    return results or results2


if __name__ == "__main__":
    exit(run())
