"""Scaling cases: how every public operation may grow with the data.

Shared by the local suite (tests/scaling, storage-operation counts) and the
canister suite (tests/entrypoint_scaling.py, IC instruction counts).

Each case builds a world of size ``n`` and runs one operation in it. Its kind
says how the operation's cost may grow with ``n``:

- BOUNDED: about the same at every ``n`` (O(1), or one page).
- GUARDED: may grow with ``n``, so past ``CAP`` it must raise TooLargeError
  (a clear error) instead of running into the IC instruction limit.
- ADMIN: whole-database operations, unbounded by design; a paged alternative
  must exist.

``known`` names the open issue that tracks a case's failure (see KNOWN). The
suites expect those cases to fail, and fail themselves when one starts
passing, so the marker is removed together with the fix.

Kept free of typing/dataclasses: this module also runs inside a canister.
"""

from ic_python_db import (
    Entity,
    Integer,
    ManyToMany,
    ManyToOne,
    OneToMany,
    OneToOne,
    String,
)

BOUNDED = "bounded"
GUARDED = "guarded"
ADMIN = "admin"

# Collections larger than this must be paged (GUARDED cases)
CAP = 1000

# Issue tracking each known failure: key -> (issue, one-line reason)
KNOWN = {
    "18": ("#18", "one JSON id list per index value is rewritten on every change"),
    "19": ("#19", "no paged alternative to whole-database operations"),
    "20": ("#20", "migrations write during reads"),
    "relations": ("#21", "relation getters load every member"),
    "instances": ("#22", "instances()/find() load the whole type"),
    "heap": ("#23", "Entity._context keeps every entity touched in the heap"),
    "sizes": ("#24", "no size check before writing a key or value"),
    "audit": ("#25", "audit log has no paged read"),
}

# Known failures only the local suite sees (their cost grows in bytes read,
# not in instructions)
LOCAL_ONLY_KNOWN = set()


# ── Model ───────────────────────────────────────────────────────────────────


class ScOwner(Entity):
    __alias__ = "name"
    name = String()
    kind = String(indexed=True)
    items = OneToMany("ScItem", "owner")
    tags = ManyToMany("ScTag", "owners")


class ScItem(Entity):
    title = String()
    color = String(indexed=True)
    owner = ManyToOne("ScOwner", "items")


class ScTag(Entity):
    __alias__ = "label"
    label = String()
    owners = ManyToMany("ScOwner", "tags")


class ScViewer(Entity):
    name = String()
    badges = ManyToMany("ScBadge", "viewers", unidirectional=True)


class ScBadge(Entity):
    label = String()


class ScPerson(Entity):
    name = String()
    card = OneToOne("ScCard", "person")


class ScCard(Entity):
    number = Integer()
    person = OneToOne("ScPerson", "card")


class ScVersioned(Entity):
    __version__ = 1
    title = String()
    rank = Integer()


MODEL = [ScOwner, ScItem, ScTag, ScViewer, ScBadge, ScPerson, ScCard, ScVersioned]


def cold():
    """Forget every in-memory entity, so the next read goes to storage.

    The runners call it between building a world and measuring an operation,
    so freeing the world's entities is not counted.
    """
    Entity.db().clear_registry()
    Entity._context.clear()


# ── Worlds ──────────────────────────────────────────────────────────────────


class World:
    """A dataset grown one member at a time, so a canister can build it in chunks.

    ``start()`` returns the world's state; ``add(state, i)`` adds member i.
    """

    def __init__(self, start, add):
        self.start = start
        self.add = add

    def build(self, n):
        state = self.start()
        for i in range(n):
            self.add(state, i)
        return state


def _items_add(w, i):
    """Loose items, half of them red."""
    ScItem(title=f"i{i}", color="red" if i % 2 else "blue")
    w["mid"] = str(i // 2 + 1)


def _owner_start():
    return {
        "owner": ScOwner(name="big", kind="k"),
        "other": ScOwner(name="other", kind="k"),
        "item_id": None,
    }


def _owner_add(w, i):
    """Items of one owner, all red."""
    item = ScItem(title=f"i{i}", color="red", owner=w["owner"])
    w["item_id"] = w["item_id"] or item._id


def _tag_start():
    return {
        "tag": ScTag(label="hot"),
        "first": None,
        "new": ScOwner(name="new", kind="k"),
    }


def _tag_add(w, i):
    """Owners sharing one tag."""
    owner = ScOwner(name=f"o{i}", kind="k")
    owner.tags.add(w["tag"])
    w["first"] = w["first"] or owner


def _badge_start():
    return {"badge": ScBadge(label="b"), "new": ScViewer(name="new")}


def _badge_add(w, i):
    """Viewers holding one badge (unidirectional)."""
    ScViewer(name=f"v{i}").badges.add(w["badge"])


def _pairs_add(w, i):
    """People, each holding their own card."""
    ScPerson(name=f"p{i}", card=ScCard(number=i))
    w["mid"] = str(i // 2 + 1)


world_items = World(lambda: {"mid": "1"}, _items_add)
world_owner = World(_owner_start, _owner_add)
world_tag = World(_tag_start, _tag_add)
world_badge = World(_badge_start, _badge_add)
world_pairs = World(lambda: {"mid": "1"}, _pairs_add)


# ── Operations ──────────────────────────────────────────────────────────────


def _item(w):
    return ScItem.load(w["item_id"])


def _owner(w):
    return ScOwner.load(w["owner"]._id)


def _tag(w):
    return ScTag.load(w["tag"]._id)


def op_create_child(w):
    ScItem(title="new", color="red", owner=w["owner"])


def op_load(w):
    assert ScItem.load(w["mid"]) is not None


def op_alias(w):
    assert ScOwner["big"] is not None


def op_count(w):
    ScItem.count()


def op_max_id(w):
    ScItem.max_id()


def op_load_some(w):
    assert len(ScItem.load_some(1, 50)) == 50


def op_page(w):
    entities, _ = ScItem.page(1, 50)
    assert len(entities) == 50


def op_find_by(w):
    entities, _ = ScItem.find_by("color", "red", count=20)
    assert len(entities) == 20


def op_count_by(w):
    ScItem.count_by("color", "red")


def op_rebuild_index(w):
    ScItem.rebuild_field_index("color", from_id=1, batch=50)


def op_parent_get(w):
    assert _item(w).owner is not None


def op_reparent(w):
    _item(w).owner = w["other"]


def op_update_field(w):
    _item(w).title = "renamed"


def op_update_indexed(w):
    _item(w).color = "blue"


def op_delete_child(w):
    _item(w).delete()


def op_delete_parent(w):
    _owner(w).delete()


def op_has_children(w):
    assert bool(_owner(w).items)


def op_count_children(w):
    len(_owner(w).items)


def op_contains_child(w):
    item = _item(w)
    assert item in item.owner.items


def op_page_children(w):
    children, _ = _owner(w).items.page(1, 50)
    assert len(children) == 50


def op_m2m_add_small_side(w):
    w["new"].tags.add(w["tag"])


def op_m2m_add_big_side(w):
    _tag(w).owners.add(w["new"])


def op_m2m_discard_big_side(w):
    _tag(w).owners.discard(w["first"])


def op_m2m_contains(w):
    assert w["first"] in _tag(w).owners


def op_m2m_small_side_get(w):
    assert len(w["first"].tags) == 1


def op_uni_add(w):
    w["new"].badges.add(w["badge"])


def op_uni_count(w):
    w["badge"].reverse_count("viewers")


def op_uni_delete_target(w):
    w["badge"].delete()


def op_one_to_one_get(w):
    person = ScPerson.load(w["mid"])
    assert person.card is not None and person.card.person == person


def op_one_to_one_set(w):
    ScPerson.load(w["mid"]).card = ScCard(number=-1)


def op_instances(w):
    ScItem.instances()


def op_find(w):
    ScItem.find({"color": "red"})


def op_iterate_children(w):
    list(_owner(w).items)


def op_index_children(w):
    _owner(w).items[0]


def op_m2m_list_big_side(w):
    list(_tag(w).owners)


def op_m2m_set_big_side(w):
    _tag(w).owners = [w["new"]]


def op_serialize_big_m2m(w):
    _tag(w).serialize()


def op_delete_big_m2m(w):
    _tag(w).delete()


# ── Cases ───────────────────────────────────────────────────────────────────


class Case:
    """One operation, the world it runs in, and how its cost may grow."""

    def __init__(
        self, name, kind, world, op, covers, known=None, readonly=False, alt=None
    ):
        self.name = name
        self.kind = kind
        self.world = world
        self.op = op
        self.covers = covers
        self.known = known
        self.readonly = readonly
        self.alt = alt  # ADMIN only: the paged alternative, "Class.method"


CASES = [
    # Entities of one type
    Case("create_child", BOUNDED, world_owner, op_create_child,
         ["Entity.new", "Database.save", "Database.reverse_index_add",
          "Database.field_index_add"]),
    Case("load", BOUNDED, world_items, op_load,
         ["Entity.load", "Database.load"], readonly=True),
    Case("alias", BOUNDED, world_owner, op_alias,
         ["Entity.__class_getitem__"], readonly=True),
    Case("count", BOUNDED, world_items, op_count, ["Entity.count"], readonly=True),
    Case("max_id", BOUNDED, world_items, op_max_id,
         ["Entity.max_id", "Database.max_id"], readonly=True),
    Case("load_some", BOUNDED, world_items, op_load_some,
         ["Entity.load_some", "Database.load_page"], readonly=True),
    Case("page", BOUNDED, world_items, op_page, ["Entity.page"],
         readonly=True),
    Case("find_by", BOUNDED, world_items, op_find_by,
         ["Entity.find_by", "Database.field_index_page"], readonly=True),
    Case("count_by", BOUNDED, world_items, op_count_by,
         ["Entity.count_by", "Database.field_index_count"], readonly=True),
    Case("rebuild_index_batch", BOUNDED, world_items, op_rebuild_index,
         ["Entity.rebuild_field_index"]),
    Case("update_field", BOUNDED, world_owner, op_update_field, ["Entity._save"]),
    Case("update_indexed", BOUNDED, world_owner, op_update_indexed,
         ["Database.field_index_remove"]),
    Case("delete_child", BOUNDED, world_owner, op_delete_child,
         ["Entity.delete", "Database.delete"]),
    # One-to-many
    Case("parent_get", BOUNDED, world_owner, op_parent_get,
         ["ManyToOne.__get__"], readonly=True),
    Case("reparent", BOUNDED, world_owner, op_reparent,
         ["ManyToOne.__set__", "Database.reverse_index_remove"]),
    Case("delete_parent", BOUNDED, world_owner, op_delete_parent,
         ["Database.reverse_index_delete"]),
    Case("has_children", BOUNDED, world_owner, op_has_children,
         ["RelationView.__bool__"], readonly=True),
    Case("count_children", BOUNDED, world_owner, op_count_children,
         ["RelationView.__len__", "Database.reverse_index_count"],
         readonly=True),
    Case("contains_child", BOUNDED, world_owner, op_contains_child,
         ["RelationView.__contains__", "Database.reverse_index_contains"],
         readonly=True),
    Case("page_children", BOUNDED, world_owner, op_page_children,
         ["RelationView.page", "Database.reverse_index_page"],
         readonly=True),
    Case("iterate_children", GUARDED, world_owner, op_iterate_children,
         ["OneToMany.__get__", "RelationView.__iter__", "Database.reverse_index_get"],
         readonly=True),
    Case("index_children", GUARDED, world_owner, op_index_children,
         ["RelationView.__getitem__"], readonly=True),
    # Many-to-many
    Case("m2m_small_side_get", BOUNDED, world_tag, op_m2m_small_side_get,
         ["ManyToMany.__get__"], readonly=True),
    Case("m2m_add_small_side", BOUNDED, world_tag, op_m2m_add_small_side,
         ["RelationView.add"]),
    Case("m2m_add_big_side", BOUNDED, world_tag, op_m2m_add_big_side,
         ["RelationView.add"]),
    Case("m2m_discard_big_side", BOUNDED, world_tag, op_m2m_discard_big_side,
         ["RelationView.discard", "RelationView.remove"]),
    Case("m2m_contains", BOUNDED, world_tag, op_m2m_contains,
         ["RelationView.__contains__"], readonly=True),
    Case("m2m_list_big_side", GUARDED, world_tag, op_m2m_list_big_side,
         ["RelationView.__iter__"], readonly=True),
    Case("m2m_set_big_side", GUARDED, world_tag, op_m2m_set_big_side,
         ["ManyToMany.__set__"]),
    Case("serialize_big_m2m", GUARDED, world_tag, op_serialize_big_m2m,
         ["Entity.serialize"], readonly=True),
    Case("delete_big_m2m", GUARDED, world_tag, op_delete_big_m2m,
         ["Entity.delete"]),
    # Unidirectional many-to-many
    Case("uni_add", BOUNDED, world_badge, op_uni_add,
         ["Database.reverse_count_add"]),
    Case("uni_count", BOUNDED, world_badge, op_uni_count,
         ["Entity.reverse_count", "Database.reverse_count_get"], readonly=True),
    Case("uni_delete_target", BOUNDED, world_badge, op_uni_delete_target,
         ["Database.reverse_count_delete"]),
    # One-to-one
    Case("one_to_one_get", BOUNDED, world_pairs, op_one_to_one_get,
         ["OneToOne.__get__"], readonly=True),
    Case("one_to_one_set", BOUNDED, world_pairs, op_one_to_one_set,
         ["OneToOne.__set__"]),
    # Whole type
    Case("instances", GUARDED, world_items, op_instances,
         ["Entity.instances"], readonly=True),
    Case("find", GUARDED, world_items, op_find,
         ["Entity.find"], readonly=True),
    # Whole database
    Case("dump_json", ADMIN, None, None, ["Database.dump_json"],
         known="19", alt="Database.dump_page"),
    Case("raw_dump_json", ADMIN, None, None,
         ["Database.raw_dump_json", "Database.get_all"],
         known="19", alt="Database.raw_dump_page"),
    Case("clear", ADMIN, None, None, ["Database.clear"],
         known="19", alt="Database.clear_page"),
    Case("get_audit", ADMIN, None, None, ["Database.get_audit"],
         known="audit", alt="Database.get_audit_page"),
]  # fmt: skip

BY_NAME = {case.name: case for case in CASES}


def run_case(name, n):
    """Build the world for ``name`` at size ``n`` and return (world, op)."""
    case = BY_NAME[name]
    return case.world(n), case.op
