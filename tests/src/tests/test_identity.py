"""The identity map is weak: entities live only while held (issue #23)."""

import gc

from tester import Tester

from ic_python_db import Database, Entity, String


class IdNote(Entity):
    text = String()


class TestIdentity:
    def setUp(self):
        Database.get_instance().clear()

    def test_same_instance_while_held(self):
        note = IdNote(text="a")
        assert IdNote.load(note._id) is note

    def test_dropped_once_nobody_holds_it(self):
        note_id = IdNote(text="a")._id
        gc.collect()
        assert Database.get_instance().get_entity("IdNote", note_id) is None
        assert IdNote.load(note_id).text == "a"  # reloaded from storage

    def test_registry_does_not_grow_with_entities_touched(self):
        ids = [IdNote(text=str(i))._id for i in range(200)]
        for note_id in ids:
            IdNote.load(note_id)
        gc.collect()
        assert len(Database.get_instance()._entity_registry) == 0

    def test_changes_are_saved_before_the_instance_goes(self):
        note = IdNote(text="a")
        note.text = "b"
        note_id = note._id
        del note
        gc.collect()
        assert IdNote.load(note_id).text == "b"


def run(test_name: str = None, test_var: str = None):
    tester = Tester(TestIdentity)
    return tester.run_tests()


if __name__ == "__main__":
    exit(run())
