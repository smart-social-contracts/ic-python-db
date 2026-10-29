"""Relation getters return a lazy RelationView (issue #21)."""

from tester import Tester

from ic_python_db import (
    Database,
    Entity,
    ManyToMany,
    ManyToOne,
    OneToMany,
    RelationView,
    String,
    TooLargeError,
    constants,
)


class RvSection(Entity):
    name = String()
    stands = OneToMany("RvStand", "section")


class RvStand(Entity):
    name = String()
    section = ManyToOne("RvSection", "stands")


class RvUser(Entity):
    name = String()
    groups = ManyToMany("RvGroup", "members")


class RvGroup(Entity):
    name = String()
    members = ManyToMany("RvUser", "groups")


class TestRelationView:
    def setUp(self):
        Database.get_instance().clear()
        Entity._context.clear()
        self._cap = constants.MAX_UNPAGED

    def tearDown(self):
        constants.MAX_UNPAGED = self._cap

    def _section(self, n):
        section = RvSection(name="Deployments")
        stands = [RvStand(name=f"s{i}", section=section) for i in range(n)]
        return section, stands

    def test_getter_returns_a_view(self):
        section, _ = self._section(2)
        assert isinstance(section.stands, RelationView)

    def test_bool_len_and_contains(self):
        section, stands = self._section(3)
        empty = RvSection(name="Empty")
        assert section.stands and not empty.stands
        assert len(section.stands) == 3 and len(empty.stands) == 0
        assert stands[1] in section.stands
        assert stands[1] not in empty.stands
        assert RvUser(name="u") not in section.stands  # wrong type

    def test_still_behaves_like_a_list_when_small(self):
        section, stands = self._section(3)
        assert [s.name for s in section.stands] == ["s0", "s1", "s2"]
        assert section.stands[0] == stands[0]
        assert section.stands == stands
        assert list(section.stands) == stands

    def test_page_walks_every_member_once(self):
        section, stands = self._section(7)
        seen, cursor = [], 1
        while cursor is not None:
            page, cursor = section.stands.page(cursor, 3)
            assert len(page) <= 3
            seen.extend(page)
        assert seen == stands

    def test_page_skips_deleted_members(self):
        section, stands = self._section(5)
        stands[1].delete()
        page, cursor = section.stands.page(1, 10)
        assert [s.name for s in page] == ["s0", "s2", "s3", "s4"]
        assert cursor is None

    def test_iterating_past_the_cap_raises(self):
        constants.MAX_UNPAGED = 3
        section, _ = self._section(4)
        Tester.assert_raises(TooLargeError, list, section.stands)
        # The cheap questions keep working
        assert len(section.stands) == 4
        assert len(section.stands.page(1, 2)[0]) == 2

    def test_many_to_many_add_discard(self):
        user, group = RvUser(name="u"), RvGroup(name="g")
        user.groups.add(group)
        assert group in user.groups and user in group.members
        group.members.discard(user)
        assert not user.groups and not group.members

    def test_many_to_many_set_past_the_cap_raises(self):
        constants.MAX_UNPAGED = 2
        group = RvGroup(name="big")
        for i in range(3):
            RvUser(name=f"u{i}").groups.add(group)
        Tester.assert_raises(TooLargeError, setattr, group, "members", [])
        assert len(group.members) == 3

    def test_delete_past_the_cap_raises_before_changing_anything(self):
        constants.MAX_UNPAGED = 2
        group = RvGroup(name="big")
        users = [RvUser(name=f"u{i}") for i in range(3)]
        for user in users:
            user.groups.add(group)
        Tester.assert_raises(TooLargeError, group.delete)
        assert RvGroup.load(group._id) is not None
        assert all(group in user.groups for user in users)

    def test_one_to_many_view_has_no_add(self):
        section, _ = self._section(1)
        Tester.assert_raises(TypeError, section.stands.add, RvStand(name="x"))


def run(test_name: str = None, test_var: str = None):
    tester = Tester(TestRelationView)
    return tester.run_tests()


if __name__ == "__main__":
    exit(run())
