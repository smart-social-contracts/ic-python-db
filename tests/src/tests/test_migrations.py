"""Tests for schema migration functionality in IC Python DB."""

import json

from tester import Tester

from ic_python_db import *
from ic_python_db import errors


def _store():
    """A copy of every key and value in the store."""
    return dict(Database.get_instance()._db_storage.items())


def _changed_keys(before, after):
    """The keys added, removed or rewritten between two copies of the store."""
    return {k for k in before.keys() | after.keys() if before.get(k) != after.get(k)}


class TestMigrations:
    def setUp(self):
        """Reset database before each test."""
        Database.get_instance().clear()

    def test_default_version(self):
        """Test that entities have default version 1."""

        class Product(Entity):
            name = String()

        product = Product(name="Widget")

        # Version is stored in database, not in public serialize()
        db = Database.get_instance()
        stored_data = db.load("Product", product._id)
        assert "__version__" in stored_data
        assert stored_data["__version__"] == 1

    def test_custom_version(self):
        """Test that entities can have custom versions."""

        class Product(Entity):
            __version__ = 3
            name = String()

        product = Product(name="Widget")

        # Version is stored in database, not in public serialize()
        db = Database.get_instance()
        stored_data = db.load("Product", product._id)
        assert stored_data["__version__"] == 3

    def test_migration_on_load(self):
        """Loading a row of an older version migrates it, and the save that
        persists the migration rewrites that row and nothing else."""

        class Product(Entity):
            __version__ = 1
            name = String()

        product = Product(name="Widget")
        product_id = product._id

        Database.get_instance().clear_registry()

        class Product(Entity):
            __version__ = 2
            name = String()
            price = Float(default=0.0)

            @classmethod
            def migrate(cls, obj, from_version, to_version):
                if from_version == 1 and to_version >= 2:
                    if "price" not in obj:
                        obj["price"] = (
                            2.5  # not the default, so a skipped migrate() shows
                        )
                return obj

        Database.get_instance().register_entity_type(Product)

        before = _store()
        loaded = Product.load(product_id)

        assert loaded is not None
        assert loaded.name == "Widget"
        assert loaded.price == 2.5
        assert _changed_keys(before, _store()) == {f"Product@{product_id}"}
        assert Database.get_instance().load("Product", product_id) == {
            "_type": "Product",
            "_id": product_id,
            "name": "Widget",
            "price": 2.5,
            "__version__": 2,
        }

    def test_migration_multiple_versions(self):
        """Test migration across multiple versions."""

        class Product(Entity):
            __version__ = 1
            name = String()

        product = Product(name="Widget")
        product_id = product._id

        Database.get_instance().clear_registry()

        class Product(Entity):
            __version__ = 3
            name = String()
            price = Float(default=0.0)
            category = String(default="general")

            @classmethod
            def migrate(cls, obj, from_version, to_version):
                if from_version == 1 and to_version >= 2:
                    if "price" not in obj:
                        obj["price"] = 1.0

                if from_version <= 2 and to_version >= 3:
                    if "category" not in obj:
                        obj["category"] = "tools"

                return obj

        Database.get_instance().register_entity_type(Product)

        loaded = Product.load(product_id)

        assert loaded is not None
        assert loaded.name == "Widget"
        assert loaded.price == 1.0
        assert loaded.category == "tools"

    def test_migration_field_rename(self):
        """Test migration that renames a field."""

        class Product(Entity):
            __version__ = 1
            old_name = String()

        product = Product(old_name="Widget")
        product_id = product._id

        Database.get_instance().clear_registry()

        class Product(Entity):
            __version__ = 2
            new_name = String()

            @classmethod
            def migrate(cls, obj, from_version, to_version):
                if from_version == 1 and to_version >= 2:
                    if "old_name" in obj:
                        obj["new_name"] = obj.pop("old_name")
                return obj

        Database.get_instance().register_entity_type(Product)

        loaded = Product.load(product_id)

        assert loaded is not None
        assert loaded.new_name == "Widget"
        assert not hasattr(loaded, "old_name")
        stored = Database.get_instance().load("Product", product_id)
        assert stored["new_name"] == "Widget"
        assert "old_name" not in stored

    def test_migration_on_deserialize(self):
        """Test that migration is triggered during deserialization."""

        class Product(Entity):
            __version__ = 1
            name = String()

        product = Product(name="Widget")
        data = product.serialize()

        Database.get_instance().clear()

        class Product(Entity):
            __version__ = 2
            name = String()
            price = Float(default=0.0)

            @classmethod
            def migrate(cls, obj, from_version, to_version):
                if from_version == 1 and to_version >= 2:
                    if "price" not in obj:
                        obj["price"] = 10.0
                return obj

        Database.get_instance().register_entity_type(Product)

        loaded = Product.deserialize(data)

        assert loaded is not None
        assert loaded.name == "Widget"
        assert loaded.price == 10.0

    def test_no_migration_same_version(self):
        """Test that no migration occurs when versions match."""

        class Product(Entity):
            __version__ = 2
            name = String()
            price = Float(default=0.0)

            @classmethod
            def migrate(cls, obj, from_version, to_version):
                obj["migrated"] = True
                return obj

        product = Product(name="Widget", price=5.0)
        product_id = product._id

        Database.get_instance().clear_registry()
        loaded = Product.load(product_id)

        assert loaded is not None
        assert loaded.name == "Widget"
        assert loaded.price == 5.0
        data = loaded.serialize()
        assert "migrated" not in data

    def test_migration_preserves_data(self):
        """Test that migrations preserve existing data fields."""

        class Product(Entity):
            __version__ = 1
            name = String()
            description = String()

        product = Product(name="Widget", description="A useful widget")
        product_id = product._id

        Database.get_instance().clear_registry()

        class Product(Entity):
            __version__ = 2
            name = String()
            description = String()
            price = Float(default=0.0)

            @classmethod
            def migrate(cls, obj, from_version, to_version):
                if from_version == 1 and to_version >= 2:
                    if "price" not in obj:
                        obj["price"] = 3.0
                return obj

        Database.get_instance().register_entity_type(Product)

        loaded_product = Product.load(product_id)

        assert loaded_product is not None
        assert loaded_product.name == "Widget"
        assert loaded_product.description == "A useful widget"
        assert loaded_product.price == 3.0
        assert Database.get_instance().load("Product", product_id) == {
            "_type": "Product",
            "_id": product_id,
            "name": "Widget",
            "description": "A useful widget",
            "price": 3.0,
            "__version__": 2,
        }

    def test_migration_with_data_transformation(self):
        """Test migration that transforms existing data."""

        class Product(Entity):
            __version__ = 1
            name = String()
            price_cents = Integer()

        product = Product(name="Widget", price_cents=1000)
        product_id = product._id

        Database.get_instance().clear_registry()

        class Product(Entity):
            __version__ = 2
            name = String()
            price_dollars = Float()

            @classmethod
            def migrate(cls, obj, from_version, to_version):
                if from_version == 1 and to_version >= 2:
                    if "price_cents" in obj:
                        obj["price_dollars"] = obj.pop("price_cents") / 100.0
                return obj

        Database.get_instance().register_entity_type(Product)

        loaded = Product.load(product_id)

        assert loaded is not None
        assert loaded.name == "Widget"
        assert loaded.price_dollars == 10.0
        assert not hasattr(loaded, "price_cents")
        stored = Database.get_instance().load("Product", product_id)
        assert stored["price_dollars"] == 10.0
        assert "price_cents" not in stored

    def test_migration_backward_compatible(self):
        """Test that entities without version field default to version 1."""

        db = Database.get_instance()
        old_data = {"_type": "Product", "_id": "1", "name": "Widget"}
        db.save("Product", "1", old_data)

        class Product(Entity):
            __version__ = 2
            name = String()
            price = Float(default=0.0)

            @classmethod
            def migrate(cls, obj, from_version, to_version):
                if from_version == 1 and to_version >= 2:
                    if "price" not in obj:
                        obj["price"] = 5.0
                return obj

        db.register_entity_type(Product)

        loaded = Product.load("1")

        assert loaded is not None
        assert loaded.name == "Widget"
        assert loaded.price == 5.0

    def test_migration_with_one_to_one_relation(self):
        """Test migration of an entity that refers to another by a plain id
        field (not a relation: see test_migration_relation_field_rename)."""

        class Profile(Entity):
            __version__ = 1
            bio = String()

        class User(Entity):
            __version__ = 1
            name = String()

        profile = Profile(bio="Developer")
        user = User(name="Alice")
        user_id = user._id
        profile_id = profile._id

        # Store relation ID in database
        db = Database.get_instance()
        user_data = db.load("User", user_id)
        user_data["profile_id"] = profile_id  # Simple ID reference
        db.save("User", user_id, user_data)

        Database.get_instance().clear_registry()

        class Profile(Entity):
            __version__ = 2
            bio = String()
            website = String(default="")

            @classmethod
            def migrate(cls, obj, from_version, to_version):
                if from_version == 1 and to_version >= 2:
                    if "website" not in obj:
                        obj["website"] = "unknown"
                return obj

        class User(Entity):
            __version__ = 2
            name = String()
            email = String(default="")
            profile_id = String(default="")  # Relation stored as ID

            @classmethod
            def migrate(cls, obj, from_version, to_version):
                if from_version == 1 and to_version >= 2:
                    if "email" not in obj:
                        obj["email"] = "unknown"
                return obj

        Database.get_instance().register_entity_type(Profile)
        Database.get_instance().register_entity_type(User)

        loaded_user = User.load(user_id)
        loaded_profile = Profile.load(profile_id)

        assert loaded_user is not None
        assert loaded_user.name == "Alice"
        assert loaded_user.email == "unknown"
        assert loaded_user.profile_id == profile_id  # Relation ID preserved
        assert loaded_profile is not None
        assert loaded_profile.bio == "Developer"
        assert loaded_profile.website == "unknown"

    def test_migration_with_simple_properties(self):
        """Test migration adding properties to entity (no complex relations)."""

        class Comment(Entity):
            __version__ = 1
            text = String()

        comment1 = Comment(text="Great!")
        comment2 = Comment(text="Nice!")
        comment1_id = comment1._id
        comment2_id = comment2._id

        Database.get_instance().clear_registry()

        class Comment(Entity):
            __version__ = 2
            text = String()
            approved = Boolean(default=True)
            author = String(default="Anonymous")

            @classmethod
            def migrate(cls, obj, from_version, to_version):
                if from_version == 1 and to_version >= 2:
                    if "approved" not in obj:
                        obj["approved"] = False
                    if "author" not in obj:
                        obj["author"] = "imported"
                return obj

        Database.get_instance().register_entity_type(Comment)

        loaded_comment1 = Comment.load(comment1_id)
        loaded_comment2 = Comment.load(comment2_id)

        assert loaded_comment1 is not None
        assert loaded_comment1.text == "Great!"
        assert loaded_comment1.approved is False
        assert loaded_comment1.author == "imported"
        assert loaded_comment2 is not None
        assert loaded_comment2.text == "Nice!"
        assert loaded_comment2.approved is False

    def test_migration_with_multiple_entities(self):
        """Test migrating multiple independent entities."""

        class Student(Entity):
            __version__ = 1
            name = String()

        class Course(Entity):
            __version__ = 1
            title = String()

        course1 = Course(title="Math")
        course2 = Course(title="Physics")
        student = Student(name="Bob")
        student_id = student._id
        course1_id = course1._id
        course2_id = course2._id

        Database.get_instance().clear_registry()

        class Student(Entity):
            __version__ = 2
            name = String()
            grade = String(default="A")

            @classmethod
            def migrate(cls, obj, from_version, to_version):
                if from_version == 1 and to_version >= 2:
                    if "grade" not in obj:
                        obj["grade"] = "B"
                return obj

        class Course(Entity):
            __version__ = 2
            title = String()
            credits = Integer(default=3)

            @classmethod
            def migrate(cls, obj, from_version, to_version):
                if from_version == 1 and to_version >= 2:
                    if "credits" not in obj:
                        obj["credits"] = 4
                return obj

        Database.get_instance().register_entity_type(Student)
        Database.get_instance().register_entity_type(Course)

        loaded_student = Student.load(student_id)
        loaded_course1 = Course.load(course1_id)
        loaded_course2 = Course.load(course2_id)

        assert loaded_student is not None
        assert loaded_student.name == "Bob"
        assert loaded_student.grade == "B"
        assert loaded_course1 is not None
        assert loaded_course1.title == "Math"
        assert loaded_course1.credits == 4
        assert loaded_course2 is not None
        assert loaded_course2.title == "Physics"
        assert loaded_course2.credits == 4

    def test_migration_relation_field_rename(self):
        """Test migration that renames a relation field."""

        class Profile(Entity):
            __version__ = 1
            bio = String()
            owner = OneToOne("User", "profile")

        class User(Entity):
            __version__ = 1
            name = String()
            profile = OneToOne("Profile", "owner")

        profile = Profile(bio="Developer")
        user = User(name="Alice", profile=profile)
        user_id = user._id
        profile_id = profile._id

        Database.get_instance().clear_registry()

        class Profile(Entity):
            __version__ = 2
            bio = String()
            user = OneToOne("User", "user_profile")

            @classmethod
            def migrate(cls, obj, from_version, to_version):
                if from_version == 1 and to_version >= 2:
                    if "owner" in obj:
                        obj["user"] = obj.pop("owner")
                return obj

        class User(Entity):
            __version__ = 2
            name = String()
            user_profile = OneToOne("Profile", "user")

            @classmethod
            def migrate(cls, obj, from_version, to_version):
                if from_version == 1 and to_version >= 2:
                    if "profile" in obj:
                        obj["user_profile"] = obj.pop("profile")
                return obj

        Database.get_instance().register_entity_type(Profile)
        Database.get_instance().register_entity_type(User)

        loaded_user = User.load(user_id)

        assert loaded_user is not None
        assert loaded_user.name == "Alice"
        # Verify migration renamed the relation field in stored data (owning side only)
        db = Database.get_instance()
        user_data = db.load("User", user_id)
        assert user_data["user_profile"] == profile_id
        assert "profile" not in user_data
        assert loaded_user.user_profile._id == profile_id
        # Note: In the new architecture, only the owning side stores the FK.
        # The inverse side (Profile) reads from the reverse index.

    def test_migration_persistence_verification(self):
        """Test that migration changes are persisted to database."""

        class Product(Entity):
            __version__ = 1
            name = String()

        product = Product(name="Widget")
        product_id = product._id

        Database.get_instance().clear_registry()

        class Product(Entity):
            __version__ = 2
            name = String()
            price = Float(default=0.0)

            @classmethod
            def migrate(cls, obj, from_version, to_version):
                if from_version == 1 and to_version >= 2:
                    if "price" not in obj:
                        obj["price"] = 9.99
                return obj

        Database.get_instance().register_entity_type(Product)

        # First load triggers migration
        loaded1 = Product.load(product_id)
        assert loaded1.price == 9.99

        # Verify migration was persisted to database
        db = Database.get_instance()
        db_data = db.load("Product", product_id)
        assert db_data == {
            "_type": "Product",
            "_id": product_id,
            "name": "Widget",
            "price": 9.99,
            "__version__": 2,
        }

        # Clear registry and load again - should not trigger migration
        db.clear_registry()
        loaded2 = Product.load(product_id)
        assert loaded2.price == 9.99

        # Verify version in DB is still 2
        db_data_after = db.load("Product", product_id)
        assert db_data_after["__version__"] == 2

    def test_migration_identity_map_consistency(self):
        """Test that entity identity map works correctly after migration."""

        class Product(Entity):
            __version__ = 1
            name = String()

        product = Product(name="Widget")
        product_id = product._id

        Database.get_instance().clear_registry()

        class Product(Entity):
            __version__ = 2
            name = String()
            price = Float(default=0.0)

            @classmethod
            def migrate(cls, obj, from_version, to_version):
                if from_version == 1 and to_version >= 2:
                    if "price" not in obj:
                        obj["price"] = 5.0
                return obj

        Database.get_instance().register_entity_type(Product)

        # Load same entity twice - should return same instance
        loaded1 = Product.load(product_id)
        loaded2 = Product.load(product_id)

        assert loaded1 is loaded2
        assert loaded1.price == 5.0
        assert loaded2.price == 5.0

        # Modify via one reference, should reflect in other
        loaded1.price = 10.0
        assert loaded2.price == 10.0

    def test_migration_with_timestamped_mixin(self):
        """Test that TimestampedMixin fields survive migration."""

        class Product(Entity, TimestampedMixin):
            __version__ = 1
            name = String()

        product = Product(name="Widget")
        product_id = product._id
        db = Database.get_instance()
        stored_data = db.load("Product", product_id)

        Database.get_instance().clear_registry()

        class Product(Entity, TimestampedMixin):
            __version__ = 2
            name = String()
            price = Float(default=0.0)

            @classmethod
            def migrate(cls, obj, from_version, to_version):
                if from_version == 1 and to_version >= 2:
                    if "price" not in obj:
                        obj["price"] = 4.0
                return obj

        Database.get_instance().register_entity_type(Product)

        loaded = Product.load(product_id)

        assert loaded is not None
        assert loaded.name == "Widget"
        assert loaded.price == 4.0
        # The migration is not a write by the caller: every mixin field stays as stored
        loaded_data = db.load("Product", product_id)
        for field in (
            "owner",
            "creator",
            "updater",
            "timestamp_created",
            "timestamp_updated",
        ):
            assert loaded_data[field] == stored_data[field], (field, loaded_data)

    def test_migration_error_handling(self):
        """Test that migration errors are handled gracefully."""

        class Product(Entity):
            __version__ = 1
            name = String()
            old_price = String()  # String type

        product = Product(name="Widget", old_price="invalid")
        product_id = product._id

        Database.get_instance().clear_registry()

        class Product(Entity):
            __version__ = 2
            name = String()
            price = Float()

            @classmethod
            def migrate(cls, obj, from_version, to_version):
                if from_version == 1 and to_version >= 2:
                    # This will raise ValueError for invalid string
                    obj["price"] = float(obj["old_price"])
                    del obj["old_price"]
                return obj

        Database.get_instance().register_entity_type(Product)

        # Migration should fail, and say which row and why
        try:
            Product.load(product_id)
            assert False, "Expected MigrationError to be raised"
        except errors.MigrationError as e:
            assert isinstance(e.__cause__, ValueError)
            assert f"Product@{product_id}" in str(e)

    def test_migration_downgrade_scenario(self):
        """A row written by newer code (the code was rolled back) is read as
        stored and never overwritten: the older code would stamp it with its
        own version, and the newer code would then migrate it a second time."""

        class Account(Entity):
            __version__ = 2
            balance = Integer()

            @classmethod
            def migrate(cls, obj, from_version, to_version):
                if from_version < 2:
                    obj["balance"] = obj["balance"] * 100  # whole units to cents
                return obj

        account_id = Account(balance=500)._id
        db = Database.get_instance()
        written = db.load("Account", account_id)
        db.clear_registry()

        class Account(Entity):  # rolled back to the version 1 code
            __version__ = 1
            balance = Integer()

        db.register_entity_type(Account)
        old = Account.load(account_id)
        assert old.balance == 500
        assert db.load("Account", account_id) == written
        assert [a._id for a in Account.load_some(1, 10)] == [account_id]
        try:
            old.balance = 7
            assert False, "older code overwrote a row of a newer version"
        except errors.MigrationError as e:
            assert f"Account@{account_id}" in str(e)
        assert db.load("Account", account_id) == written
        db.clear_registry()

        class Account(Entity):  # rolled forward again
            __version__ = 2
            balance = Integer()

            @classmethod
            def migrate(cls, obj, from_version, to_version):
                if from_version < 2:
                    obj["balance"] = obj["balance"] * 100
                return obj

        db.register_entity_type(Account)
        assert Account.load(account_id).balance == 500

    def test_migration_deserialize_simple(self):
        """Test migration during deserialize with simple properties."""

        class Product(Entity):
            __version__ = 1
            name = String()

        product = Product(name="Widget")

        # Serialize v1 entity
        product_data = product.serialize()

        Database.get_instance().clear()

        # Define v2 schema
        class Product(Entity):
            __version__ = 2
            name = String()
            price = Float(default=0.0)
            category = String(default="general")

            @classmethod
            def migrate(cls, obj, from_version, to_version):
                if from_version == 1 and to_version >= 2:
                    if "price" not in obj:
                        obj["price"] = 19.99
                    if "category" not in obj:
                        obj["category"] = "general"
                return obj

        Database.get_instance().register_entity_type(Product)

        # Deserialize v1 data with v2 schema (should trigger migration)
        deserialized_product = Product.deserialize(product_data)

        assert deserialized_product is not None
        assert deserialized_product.name == "Widget"
        assert deserialized_product.price == 19.99
        assert deserialized_product.category == "general"
        # Verify migration was persisted
        db = Database.get_instance()
        product_db_data = db.load("Product", deserialized_product._id)
        assert product_db_data["__version__"] == 2
        assert product_db_data["price"] == 19.99

    def test_migration_large_version_jump(self):
        """Test migration with large version jump (v1 to v10)."""

        class Product(Entity):
            __version__ = 1
            name = String()

        product = Product(name="Widget")
        product_id = product._id

        Database.get_instance().clear_registry()

        class Product(Entity):
            __version__ = 10
            name = String()
            price = Float(default=0.0)
            category = String(default="general")
            in_stock = Boolean(default=True)

            @classmethod
            def migrate(cls, obj, from_version, to_version):
                # Simulate multiple version migrations
                if from_version < 5 and to_version >= 5:
                    obj["price"] = 1.0
                if from_version < 7 and to_version >= 7:
                    obj["category"] = "archive"
                if from_version < 10 and to_version >= 10:
                    obj["in_stock"] = False
                return obj

        Database.get_instance().register_entity_type(Product)

        loaded = Product.load(product_id)

        assert loaded is not None
        assert loaded.name == "Widget"
        assert loaded.price == 1.0
        assert loaded.category == "archive"
        assert loaded.in_stock is False

    def test_migrated_values_move_in_the_field_index(self):
        """A value that migrate() changes leaves the old value's index entry
        and is found under the new one."""

        class Ticket(Entity):
            __version__ = 1
            status = String(indexed=True)

        Ticket(status="open")
        Ticket(status="closed")
        Database.get_instance().clear_registry()

        class Ticket(Entity):  # the same type, new code
            __version__ = 2
            status = String(indexed=True)

            @classmethod
            def migrate(cls, obj, from_version, to_version):
                obj["status"] = obj["status"].upper()
                return obj

        Database.get_instance().register_entity_type(Ticket)
        Ticket.load_some(1, 10)  # migrates and saves both

        assert [t._id for t in Ticket.find_by("status", "OPEN")[0]] == ["1"]
        assert [t._id for t in Ticket.find_by("status", "CLOSED")[0]] == ["2"]
        assert Ticket.count_by("status", "open") == 0
        assert Ticket.count_by("status", "closed") == 0

    def test_migrated_alias_value_finds_the_row(self):
        """A row whose alias value migrate() changes is found by the new value."""

        class Tag(Entity):
            __alias__ = "label"
            __version__ = 1
            label = String()

        tag_id = Tag(label="urgent")._id
        Database.get_instance().clear_registry()

        class Tag(Entity):  # the same type, new code
            __alias__ = "label"
            __version__ = 2
            label = String()

            @classmethod
            def migrate(cls, obj, from_version, to_version):
                obj["label"] = obj["label"].upper()
                return obj

        Database.get_instance().register_entity_type(Tag)
        Tag.load(tag_id)
        Database.get_instance().clear_registry()

        assert Tag["URGENT"]._id == tag_id

    def _cfg_rows_one_of_which_migrate_cannot_read(self):
        """Three rows stored at version 1, and the version 2 code, whose
        migrate() cannot read the middle one."""

        class Cfg(Entity):
            __version__ = 1
            data = String(default="{}")

        Cfg(data='{"a": 1}')
        Cfg(data="not json")
        Cfg(data='{"a": 3}')
        Database.get_instance().clear_registry()

        class Cfg(Entity):  # the same type, new code
            __version__ = 2
            data = String(default="{}")

            @classmethod
            def migrate(cls, obj, from_version, to_version):
                json.loads(obj["data"])  # a row stored as text that is not JSON
                return obj

        Database.get_instance().register_entity_type(Cfg)
        return Cfg

    def test_migrate_error_propagates_from_every_listing(self):
        """A row whose migrate() raises is an error in load_some, page and
        instances as it is in load, not a row that is missing from the list
        (the rows around it come back, so a short list looks complete)."""
        Cfg = self._cfg_rows_one_of_which_migrate_cannot_read()
        assert Cfg.count() == 3

        reads = {
            "load_some": lambda: Cfg.load_some(1, 10),
            "page": lambda: Cfg.page(1, 10),
            "instances": lambda: Cfg.instances(),
            "load": lambda: Cfg.load("2"),
        }
        for name, read in reads.items():
            try:
                result = read()
            except Exception as e:
                error = e
            else:
                assert False, f"Cfg.{name}() returned {result!r} instead of raising"
            assert isinstance(error, errors.MigrationError), (name, error)
            assert "Cfg@2" in str(error), (name, str(error))
            assert isinstance(error.__cause__, ValueError), (name, error)

    def test_rows_that_cannot_be_built_are_still_skipped_by_listings(self):
        """Two shapes of a stored row that cannot be built, each skipped by the
        listings as before, with the rows around them returned: one that
        carries a value for a OneToMany (which has no setter), and one whose
        ManyToMany list names a row that is gone."""

        class Account(Entity):
            name = String()
            entries = OneToMany("Entry", "account")
            tags = ManyToMany("Tag", "accounts")

        class Entry(Entity):
            account = ManyToOne("Account", "entries")

        class Tag(Entity):
            accounts = ManyToMany("Account", "tags")

        gone = Tag()
        gone_id = gone._id
        gone.delete()
        db = Database.get_instance()

        Account(name="first")
        db.save(
            "Account",
            "2",
            {"_type": "Account", "_id": "2", "name": "second", "entries": ["7"]},
        )
        db.save(
            "Account",
            "3",
            {"_type": "Account", "_id": "3", "name": "third", "tags": [gone_id]},
        )
        db.save("_system", "Account_id", "3")
        Account(name="fourth")
        db.clear_registry()

        assert [a.name for a in Account.load_some(1, 10)] == ["first", "fourth"]
        accounts, next_from_id = Account.page(1, 10)
        assert [a.name for a in accounts] == ["first", "fourth"]
        assert next_from_id is None


def run(test_name: str = None, test_var: str = None):
    tester = Tester(TestMigrations)
    return tester.run_tests()


if __name__ == "__main__":
    exit(run())
