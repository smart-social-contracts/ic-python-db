"""Entity.page() and the instances()/find() guard (issue #22)."""

from tester import Tester

from ic_python_db import Database, Entity, String, TooLargeError, constants


class PgNote(Entity):
    text = String()


class TestPaging:
    def setUp(self):
        Database.get_instance().clear()
        Entity._context.clear()
        self._cap = constants.MAX_UNPAGED

    def tearDown(self):
        constants.MAX_UNPAGED = self._cap

    def test_page_walks_every_entity_once(self):
        notes = [PgNote(text=f"n{i}") for i in range(7)]
        seen, cursor = [], 1
        while cursor is not None:
            page, cursor = PgNote.page(cursor, 3)
            assert len(page) <= 3
            seen.extend(page)
        assert seen == notes

    def test_page_skips_deleted_ids(self):
        notes = [PgNote(text=f"n{i}") for i in range(5)]
        notes[0].delete()
        notes[3].delete()
        page, cursor = PgNote.page(1, 2)
        assert [n.text for n in page] == ["n1", "n2"]
        assert cursor == int(notes[4]._id)
        page, cursor = PgNote.page(cursor, 2)
        assert [n.text for n in page] == ["n4"] and cursor is None

    def test_page_of_empty_type(self):
        assert PgNote.page() == ([], None)

    def test_page_rejects_bad_arguments(self):
        Tester.assert_raises(ValueError, PgNote.page, 0, 10)
        Tester.assert_raises(ValueError, PgNote.page, 1, 0)

    def test_instances_and_find_raise_past_the_cap(self):
        constants.MAX_UNPAGED = 3
        for i in range(4):
            PgNote(text="same")
        Tester.assert_raises(TooLargeError, PgNote.instances)
        Tester.assert_raises(TooLargeError, PgNote.find, {"text": "same"})
        assert PgNote.count() == 4

    def test_instances_and_find_still_work_when_small(self):
        PgNote(text="a")
        PgNote(text="b")
        assert len(PgNote.instances()) == 2
        assert [n.text for n in PgNote.find({"text": "b"})] == ["b"]


def run(test_name: str = None, test_var: str = None):
    tester = Tester(TestPaging)
    return tester.run_tests()


if __name__ == "__main__":
    exit(run())
