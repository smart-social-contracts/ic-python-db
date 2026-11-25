"""The identity map keeps entities only while held, and never past the
message that loaded them (issue #23)."""

import gc
import weakref

from tester import Tester

import ic_python_db.db_engine as engine
from ic_python_db import Database, Entity, StaleEntityError, String


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

    # An instance held across a message boundary (issue #47).
    # clear_registry() is what a new message does to the identity map.

    def test_a_held_instance_cannot_write_back_over_another_messages_change(self):
        class IdOrder(Entity):
            status = String(default="open")
            paid = String(default="no")

        db = Database.get_instance()
        IdOrder()
        held = IdOrder.load("1")  # message 1 loads it, then awaits a call
        db.clear_registry()
        IdOrder.load("1").paid = "yes"  # message 2 records a payment
        db.clear_registry()  # message 1 resumes with the instance it held
        Tester.assert_raises(StaleEntityError, setattr, held, "status", "shipped")
        assert held.status == "open"
        stored = db.load("IdOrder", "1")
        assert (stored["status"], stored["paid"]) == ("open", "yes")

        fresh = IdOrder.load("1")  # what the error says to do
        fresh.status = "shipped"
        stored = db.load("IdOrder", "1")
        assert (stored["status"], stored["paid"]) == ("shipped", "yes")

    def test_a_held_instance_of_an_unchanged_row_writes_normally(self):
        """Nothing changed the row: the write goes through, and the instance
        is the one a load returns again."""

        class IdCart(Entity):
            items = String(default="")

        db = Database.get_instance()
        held = IdCart(items="a")
        db.clear_registry()
        held.items = "a,b"
        assert db.load("IdCart", held._id)["items"] == "a,b"
        assert IdCart.load(held._id) is held
        held.items = "a,b,c"
        assert db.load("IdCart", held._id)["items"] == "a,b,c"

    def test_a_held_instance_cannot_bring_back_a_deleted_row(self):
        class IdTicket(Entity):
            note = String(default="")

        db = Database.get_instance()
        held = IdTicket(note="a")
        db.clear_registry()
        IdTicket.load(held._id).delete()
        db.clear_registry()
        Tester.assert_raises(StaleEntityError, setattr, held, "note", "b")
        Tester.assert_raises(StaleEntityError, held._save)
        Tester.assert_raises(StaleEntityError, held.delete)
        assert db.load("IdTicket", held._id) is None
        assert IdTicket.count() == 0

    def test_after_a_held_instance_writes_a_fresh_one_of_the_same_row_is_stale(
        self,
    ):
        """The held instance passed its check and wrote: an instance loaded in
        this message before that holds the old row, and must not write it."""

        class IdBox(Entity):
            a = String(default="")
            b = String(default="")

        db = Database.get_instance()
        held = IdBox()
        db.clear_registry()
        fresh = IdBox.load(held._id)
        held.a = "by held"
        Tester.assert_raises(StaleEntityError, setattr, fresh, "b", "by fresh")
        stored = db.load("IdBox", held._id)
        assert (stored["a"], stored["b"]) == ("by held", "")


def run(test_name: str = None, test_var: str = None):
    tester = Tester(TestIdentity)
    return tester.run_tests()


if __name__ == "__main__":
    exit(run())
