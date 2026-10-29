"""The identity map keeps entities only while held, and never past the
message that loaded them (issue #23)."""

import gc
import weakref

from tester import Tester

import ic_python_db.db_engine as engine
from ic_python_db import Database, Entity, String


class IdNote(Entity):
    text = String()


def _weakrefs_work():
    """False in a canister, where weakref is a stub holding strong references."""

    class Probe:
        pass

    probe = Probe()
    ref = weakref.ref(probe)
    del probe
    gc.collect()
    return ref() is None


class TestIdentity:
    def setUp(self):
        Database.get_instance().clear()

    def test_same_instance_while_held(self):
        note = IdNote(text="a")
        assert IdNote.load(note._id) is note

    def test_dropped_once_nobody_holds_it(self):
        if not _weakrefs_work():
            return  # canister: covered per message, below
        note_id = IdNote(text="a")._id
        gc.collect()
        assert Database.get_instance().get_entity("IdNote", note_id) is None
        assert IdNote.load(note_id).text == "a"  # reloaded from storage

    def test_registry_does_not_grow_with_entities_touched(self):
        if not _weakrefs_work():
            return
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

    def test_emptied_when_a_new_message_starts(self):
        clock = [1]
        original = engine._message_time
        engine._message_time = lambda: clock[0]
        try:
            note = IdNote(text="a")
            assert IdNote.load(note._id) is note  # same message, same instance
            clock[0] = 2  # the next message
            again = IdNote.load(note._id)
            assert again is not note and again.text == "a"
            assert len(Database.get_instance()._entity_registry) == 1
        finally:
            engine._message_time = original


def run(test_name: str = None, test_var: str = None):
    tester = Tester(TestIdentity)
    return tester.run_tests()


if __name__ == "__main__":
    exit(run())
