"""Tests for schema introspection, diffing, and upgrade compatibility checking."""

from tester import Tester

from ic_python_db import *
from ic_python_db.schema import (
    ChangeType,
    SchemaChange,
    SchemaIncompatibleError,
    _has_custom_migrate,
    build_field_descriptor,
    build_schema,
    check_upgrade_compatibility,
    diff_schemas,
    schema_hash,
)


class TestSchema:
    def setUp(self):
        """Reset database before each test."""
        Database.get_instance().clear()

    # ── Schema descriptor builder ──────────────────────────────────────

    def test_build_schema_basic(self):
        """Test building schema from a simple entity."""

        class Product(Entity):
            name = String()
            price = Float(default=0.0)

        db = Database.get_instance()
        schema = db.build_schema_from_entities()

        assert "Product" in schema
        assert schema["Product"]["version"] == 1
        assert "name" in schema["Product"]["fields"]
        assert schema["Product"]["fields"]["name"]["type"] == "String"
        assert schema["Product"]["fields"]["name"]["kind"] == "property"
        assert "price" in schema["Product"]["fields"]
        assert schema["Product"]["fields"]["price"]["type"] == "Float"
        assert schema["Product"]["fields"]["price"]["default"] == 0.0

    def test_build_schema_with_constraints(self):
        """Test that field constraints are captured."""

        class User(Entity):
            name = String(min_length=2, max_length=50)
            age = Integer(min_value=0, max_value=120)

        db = Database.get_instance()
        schema = db.build_schema_from_entities()

        name_field = schema["User"]["fields"]["name"]
        assert name_field["constraints"]["min_length"] == 2
        assert name_field["constraints"]["max_length"] == 50

        age_field = schema["User"]["fields"]["age"]
        assert age_field["constraints"]["min_value"] == 0
        assert age_field["constraints"]["max_value"] == 120

    def test_build_schema_with_relationships(self):
        """Test that relationships are captured in schema."""

        class Author(Entity):
            name = String()
            books = OneToMany("Book", "author")

        class Book(Entity):
            title = String()
            author = ManyToOne("Author", "books")

        db = Database.get_instance()
        schema = db.build_schema_from_entities()

        assert "Author" in schema
        books_rel = schema["Author"]["relationships"]["books"]
        assert books_rel["type"] == "OneToMany"
        assert books_rel["target"] == "Book"
        assert books_rel["inverse"] == "author"
        assert books_rel["many"] is True

        author_rel = schema["Book"]["relationships"]["author"]
        assert author_rel["type"] == "ManyToOne"
        assert author_rel["target"] == "Author"

    def test_build_schema_with_version(self):
        """Test that entity version is captured."""

        class Product(Entity):
            __version__ = 3
            name = String()

        db = Database.get_instance()
        schema = db.build_schema_from_entities()

        assert schema["Product"]["version"] == 3

    def test_build_schema_with_migrate(self):
        """Test that has_migrate flag is set when migrate() is overridden."""

        class Product(Entity):
            __version__ = 2
            name = String()

            @classmethod
            def migrate(cls, obj, from_version, to_version):
                return obj

        db = Database.get_instance()
        schema = db.build_schema_from_entities()

        assert schema["Product"]["has_migrate"] is True

    def test_build_schema_without_migrate(self):
        """Test that has_migrate is absent when using default migrate()."""

        class Item(Entity):
            name = String()

        db = Database.get_instance()
        schema = db.build_schema_from_entities()

        assert "has_migrate" not in schema["Item"]

    def test_build_schema_boolean(self):
        """Test Boolean property in schema."""

        class Feature(Entity):
            name = String()
            enabled = Boolean(default=True)

        db = Database.get_instance()
        schema = db.build_schema_from_entities()

        enabled_field = schema["Feature"]["fields"]["enabled"]
        assert enabled_field["type"] == "Boolean"
        assert enabled_field["default"] is True

    # ── Schema hash ────────────────────────────────────────────────────

    def test_schema_hash_deterministic(self):
        """Test that schema hash is deterministic."""

        class Widget(Entity):
            name = String()

        db = Database.get_instance()
        schema = db.build_schema_from_entities()

        hash1 = schema_hash(schema)
        hash2 = schema_hash(schema)
        assert hash1 == hash2
        assert len(hash1) == 64  # SHA-256 hex

    def test_schema_hash_changes_with_schema(self):
        """Test that different schemas produce different hashes."""
        schema_a = {"Product": {"version": 1, "fields": {"name": {"type": "String"}}, "relationships": {}}}
        schema_b = {"Product": {"version": 2, "fields": {"name": {"type": "String"}}, "relationships": {}}}

        assert schema_hash(schema_a) != schema_hash(schema_b)

    # ── Schema diff engine ─────────────────────────────────────────────

    def test_diff_no_changes(self):
        """Test that identical schemas produce no changes."""
        schema = {"Product": {"version": 1, "fields": {"name": {"type": "String", "kind": "property"}}, "relationships": {}}}
        changes = diff_schemas(schema, schema)
        assert len(changes) == 0

    def test_diff_new_entity(self):
        """Test detecting a new entity type."""
        old = {}
        new = {"Product": {"version": 1, "fields": {}, "relationships": {}}}

        changes = diff_schemas(old, new)
        assert len(changes) == 1
        assert changes[0].change_type == ChangeType.ENTITY_ADDED
        assert changes[0].safe is True

    def test_diff_removed_entity(self):
        """Test detecting a removed entity type."""
        old = {"Product": {"version": 1, "fields": {}, "relationships": {}}}
        new = {}

        changes = diff_schemas(old, new)
        assert len(changes) == 1
        assert changes[0].change_type == ChangeType.ENTITY_REMOVED
        assert changes[0].safe is True

    def test_diff_added_field_with_default(self):
        """Test that adding a field with a default is safe."""
        old = {"Product": {"version": 1, "fields": {"name": {"type": "String", "kind": "property"}}, "relationships": {}}}
        new = {"Product": {"version": 2, "fields": {
            "name": {"type": "String", "kind": "property"},
            "price": {"type": "Float", "kind": "property", "default": 0.0},
        }, "relationships": {}}}

        changes = diff_schemas(old, new)
        field_changes = [c for c in changes if c.field == "price"]
        assert len(field_changes) == 1
        assert field_changes[0].change_type == ChangeType.ADDED
        assert field_changes[0].safe is True

    def test_diff_added_field_without_default(self):
        """Test that adding a field without a default is breaking."""
        old = {"Product": {"version": 1, "fields": {"name": {"type": "String", "kind": "property"}}, "relationships": {}}}
        new = {"Product": {"version": 2, "fields": {
            "name": {"type": "String", "kind": "property"},
            "price": {"type": "Float", "kind": "property"},
        }, "relationships": {}}}

        changes = diff_schemas(old, new)
        field_changes = [c for c in changes if c.field == "price"]
        assert len(field_changes) == 1
        assert field_changes[0].change_type == ChangeType.ADDED
        assert field_changes[0].safe is False

    def test_diff_removed_field(self):
        """Test that removing a field is safe."""
        old = {"Product": {"version": 1, "fields": {
            "name": {"type": "String", "kind": "property"},
            "old_field": {"type": "String", "kind": "property"},
        }, "relationships": {}}}
        new = {"Product": {"version": 2, "fields": {
            "name": {"type": "String", "kind": "property"},
        }, "relationships": {}}}

        changes = diff_schemas(old, new)
        field_changes = [c for c in changes if c.field == "old_field"]
        assert len(field_changes) == 1
        assert field_changes[0].change_type == ChangeType.REMOVED
        assert field_changes[0].safe is True

    def test_diff_type_changed(self):
        """Test that changing a field type is breaking."""
        old = {"Product": {"version": 1, "fields": {"age": {"type": "String", "kind": "property"}}, "relationships": {}}}
        new = {"Product": {"version": 2, "fields": {"age": {"type": "Integer", "kind": "property"}}, "relationships": {}}}

        changes = diff_schemas(old, new)
        type_changes = [c for c in changes if c.change_type == ChangeType.TYPE_CHANGED]
        assert len(type_changes) == 1
        assert type_changes[0].safe is False
        assert type_changes[0].old_value == "String"
        assert type_changes[0].new_value == "Integer"

    def test_diff_constraints_changed(self):
        """Test that changing constraints is safe."""
        old = {"Product": {"version": 1, "fields": {
            "name": {"type": "String", "kind": "property", "constraints": {"max_length": 50}},
        }, "relationships": {}}}
        new = {"Product": {"version": 2, "fields": {
            "name": {"type": "String", "kind": "property", "constraints": {"max_length": 100}},
        }, "relationships": {}}}

        changes = diff_schemas(old, new)
        constraint_changes = [c for c in changes if c.change_type == ChangeType.CONSTRAINTS_CHANGED]
        assert len(constraint_changes) == 1
        assert constraint_changes[0].safe is True

    def test_diff_relationship_type_changed(self):
        """Test that changing relationship type is breaking."""
        old = {"User": {"version": 1, "fields": {}, "relationships": {
            "profile": {"type": "OneToOne", "target": "Profile", "kind": "relationship"},
        }}}
        new = {"User": {"version": 2, "fields": {}, "relationships": {
            "profile": {"type": "ManyToMany", "target": "Profile", "kind": "relationship"},
        }}}

        changes = diff_schemas(old, new)
        rel_changes = [c for c in changes if c.change_type == ChangeType.RELATIONSHIP_CHANGED]
        assert len(rel_changes) == 1
        assert rel_changes[0].safe is False

    def test_diff_version_changed(self):
        """Test that version changes are detected."""
        old = {"Product": {"version": 1, "fields": {}, "relationships": {}}}
        new = {"Product": {"version": 3, "fields": {}, "relationships": {}}}

        changes = diff_schemas(old, new)
        version_changes = [c for c in changes if c.change_type == ChangeType.VERSION_CHANGED]
        assert len(version_changes) == 1
        assert version_changes[0].old_value == 1
        assert version_changes[0].new_value == 3

    # ── Upgrade compatibility enforcement ──────────────────────────────

    def test_check_upgrade_first_deploy(self):
        """Test that first deployment (no stored schema) always succeeds."""

        class Product(Entity):
            name = String()

        db = Database.get_instance()
        changes = db.check_upgrade_compatibility()
        assert changes == []

        stored = db.load("_system", "_schema")
        assert stored is not None
        assert "Product" in stored

    def test_check_upgrade_compatible_change(self):
        """Test that adding a field with default passes."""

        class Product(Entity):
            __version__ = 1
            name = String()

        db = Database.get_instance()
        db.check_upgrade_compatibility()

        db.clear_registry()

        class Product(Entity):
            __version__ = 2
            name = String()
            price = Float(default=0.0)

        db.register_entity_type(Product)
        changes = db.check_upgrade_compatibility()

        field_changes = [c for c in changes if c.field == "price"]
        assert len(field_changes) == 1
        assert field_changes[0].safe is True

    def test_check_upgrade_breaking_without_migrate_raises(self):
        """Test that breaking change without migrate() raises."""

        class Product(Entity):
            __version__ = 1
            age = String()

        db = Database.get_instance()
        db.check_upgrade_compatibility()

        db.clear_registry()

        class Product(Entity):
            __version__ = 2
            age = Integer()

        db.register_entity_type(Product)

        raised = False
        try:
            db.check_upgrade_compatibility()
        except SchemaIncompatibleError as e:
            raised = True
            assert "age" in str(e)
            assert "breaking" in str(e).lower() or "Type changed" in str(e)
        assert raised, "Expected SchemaIncompatibleError"

    def test_check_upgrade_breaking_with_migrate_passes(self):
        """Test that breaking change with migrate() passes."""

        class Product(Entity):
            __version__ = 1
            age = String()

        db = Database.get_instance()
        db.check_upgrade_compatibility()

        db.clear_registry()

        class Product(Entity):
            __version__ = 2
            age = Integer()

            @classmethod
            def migrate(cls, obj, from_version, to_version):
                if from_version == 1:
                    obj["age"] = int(obj["age"]) if obj.get("age") else 0
                return obj

        db.register_entity_type(Product)
        changes = db.check_upgrade_compatibility()

        type_changes = [c for c in changes if c.change_type == ChangeType.TYPE_CHANGED]
        assert len(type_changes) == 1
        assert type_changes[0].safe is False

    def test_check_upgrade_no_raise_mode(self):
        """Test raise_on_error=False returns changes without raising."""

        class Product(Entity):
            __version__ = 1
            age = String()

        db = Database.get_instance()
        db.check_upgrade_compatibility()

        db.clear_registry()

        class Product(Entity):
            __version__ = 2
            age = Integer()

        db.register_entity_type(Product)
        changes = db.check_upgrade_compatibility(raise_on_error=False)
        assert any(c.change_type == ChangeType.TYPE_CHANGED for c in changes)

    def test_schema_hash_stored(self):
        """Test that schema hash is persisted after check."""

        class Product(Entity):
            name = String()

        db = Database.get_instance()
        db.check_upgrade_compatibility()

        stored_hash = db.get_schema_hash()
        assert stored_hash is not None
        assert len(stored_hash) == 64

    def test_save_schema_updates_hash(self):
        """Test that save_schema updates the stored hash."""

        class Product(Entity):
            name = String()

        db = Database.get_instance()
        db.save_schema()

        hash1 = db.get_schema_hash()
        assert hash1 is not None

    # ── Auto-migration for safe changes ────────────────────────────────

    def test_auto_migrate_new_field_with_default(self):
        """Test that new fields with defaults are auto-injected on load."""

        class Product(Entity):
            __version__ = 1
            name = String()

        product = Product(name="Widget")
        product_id = product._id

        Database.get_instance().clear_registry()

        class Product(Entity):
            __version__ = 2
            name = String()
            price = Float(default=9.99)

        Database.get_instance().register_entity_type(Product)

        loaded = Product.load(product_id)

        assert loaded is not None
        assert loaded.name == "Widget"
        assert loaded.price == 9.99

    def test_auto_migrate_does_not_override_migrate(self):
        """Test that auto-migration runs before migrate(), and migrate() can override."""

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
                if from_version == 1:
                    obj["price"] = 42.0
                return obj

        Database.get_instance().register_entity_type(Product)

        loaded = Product.load(product_id)

        assert loaded is not None
        assert loaded.price == 42.0  # migrate() overrode the default

    def test_auto_migrate_boolean_default(self):
        """Test auto-migration with Boolean default."""

        class Feature(Entity):
            __version__ = 1
            name = String()

        feature = Feature(name="Dark Mode")
        feature_id = feature._id

        Database.get_instance().clear_registry()

        class Feature(Entity):
            __version__ = 2
            name = String()
            enabled = Boolean(default=True)

        Database.get_instance().register_entity_type(Feature)

        loaded = Feature.load(feature_id)

        assert loaded is not None
        assert loaded.name == "Dark Mode"
        assert loaded.enabled is True

    def test_auto_migrate_multiple_new_fields(self):
        """Test auto-migration with multiple new fields."""

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
            active = Boolean(default=True)

        Database.get_instance().register_entity_type(Product)

        loaded = Product.load(product_id)

        assert loaded is not None
        assert loaded.name == "Widget"
        assert loaded.price == 0.0
        assert loaded.category == "general"
        assert loaded.active is True

    # ── _has_custom_migrate helper ─────────────────────────────────────

    def test_has_custom_migrate_false(self):
        """Test _has_custom_migrate returns False for default migrate."""

        class Product(Entity):
            name = String()

        assert _has_custom_migrate(Product) is False

    def test_has_custom_migrate_true(self):
        """Test _has_custom_migrate returns True for overridden migrate."""

        class Product(Entity):
            name = String()

            @classmethod
            def migrate(cls, obj, from_version, to_version):
                return obj

        assert _has_custom_migrate(Product) is True


def run(test_name: str = None, test_var: str = None):
    tester = Tester(TestSchema)
    return tester.run_tests()


if __name__ == "__main__":
    exit(run())
