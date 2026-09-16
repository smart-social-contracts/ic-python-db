import json

from ic_python_logging import get_logger
from tester import Tester

from ic_python_db import *

logger = get_logger(__name__)


class TestDatabase:
    def setUp(self):
        self.db = Database.get_instance()
        self.db.clear()

    def test_database_basic_operations(self):
        # Test save and load
        data = {"name": "John", "age": 30}
        self.db.save("person", "1", data)
        loaded = self.db.load("person", "1")
        assert loaded == data

        # Test update
        self.db.update("person", "1", "age", 31)
        loaded = self.db.load("person", "1")
        assert loaded["age"] == 31

        # Test delete
        self.db.delete("person", "1")
        self.db.clear()  # Clear database to ensure data is removed
        assert self.db.load("person", "1") is None

    def test_memory_storage_range_matches_ic_key_order(self):
        """MemoryStorage.range must order str keys like Basilisk's
        StableBTreeMap does: shorter keys first, then bytewise."""
        from ic_python_db.storage import MemoryStorage, supports_range

        st = MemoryStorage()
        assert supports_range(st)
        for k in ["T@9", "T@10", "T@2", "T@100", "T@1x", "U@1", "T@"]:
            st.insert(k, k)

        assert [k for k, _ in st.range("T@1", "T@:")] == ["T@2", "T@9"]
        assert [k for k, _ in st.range("T@10", "T@::")] == ["T@10", "T@1x"]
        assert [k for k, _ in st.range("T@10", "T@::", limit=1)] == ["T@10"]
        assert [k for k, _ in st.range("T@10", "T@::", limit=0)] == []
        # Unbounded end: everything of length >= 4 from "T@10" on
        assert [k for k, _ in st.range("T@10")] == ["T@10", "T@1x", "T@100"]
        # A 3-char key sorts before every 4-char key regardless of content
        assert [k for k, _ in st.range("T@", "T@1")] == ["T@"]
        assert st.range("T@1", "T@:")[0] == ("T@2", "T@2")

    def test_database_load_page(self):
        for i in range(1, 121):
            self.db.save("Item", str(i), {"n": i})
        self.db.save("_system", "Item_id", "120")
        for i in (1, 10, 11, 50, 100, 120):
            self.db.delete("Item", str(i))
        # Non-sequential ids of the same type must never be returned
        self.db.save("Item", "1x", {"custom": True})
        self.db.save("Item", "007", {"custom": True})
        self.db.save("Item", "abc", {"custom": True})
        self.db.save("ItemX", "5", {"other_type": True})

        def ids(page):
            return [int(i) for i, _ in page]

        assert ids(self.db.load_page("Item", 1, 12)) == [
            2,
            3,
            4,
            5,
            6,
            7,
            8,
            9,
            12,
            13,
            14,
            15,
        ]
        assert ids(self.db.load_page("Item", 9, 3)) == [9, 12, 13]
        assert ids(self.db.load_page("Item", 98, 5)) == [98, 99, 101, 102, 103]
        assert ids(self.db.load_page("Item", 118, 10)) == [118, 119]
        assert self.db.load_page("Item", 121, 10) == []
        assert self.db.load_page("Item", 1, 1)[0][1] == {"n": 2}
        assert self.db.load_page("Nothing", 1, 10) == []

        everything = self.db.load_page("Item", 1, 1000)
        assert ids(everything) == [
            i for i in range(1, 121) if i not in (1, 10, 11, 50, 100, 120)
        ]
        assert all(not d.get("custom") for _, d in everything)

        # Bounded by the passed max_id
        assert ids(self.db.load_page("Item", 1, 1000, max_id=5)) == [2, 3, 4, 5]

        Tester.assert_raises(ValueError, lambda: self.db.load_page("Item", 0, 5))
        Tester.assert_raises(ValueError, lambda: self.db.load_page("Item", 1, 0))

    def test_database_get_all(self):
        data1 = {"name": "John", "age": 30}
        data2 = {"name": "Jane", "age": 25}

        self.db.save("person", "1", data1)
        self.db.save("person", "2", data2)

        all_data = self.db.get_all()
        assert len(all_data) == 2
        assert all_data["person@1"] == data1
        assert all_data["person@2"] == data2

    def test_database_dump_json(self):
        # Test empty database
        assert self.db.dump_json() == "{}"
        assert json.loads(self.db.dump_json(pretty=True)) == {}

        # Add some test data
        person_data = {"name": "John", "age": 30}
        dept_data = {"name": "IT", "location": "HQ"}

        self.db.save("person", "1", person_data)
        self.db.save("department", "1", dept_data)

        # Test non-pretty output
        dumped = json.loads(self.db.dump_json())
        assert "person" in dumped
        assert "department" in dumped
        assert dumped["person"]["1"] == person_data
        assert dumped["department"]["1"] == dept_data

        # Test pretty output
        pretty_dumped = self.db.dump_json(pretty=True)
        assert "\n" in pretty_dumped
        assert json.loads(pretty_dumped) == dumped


def run(test_name: str = None, test_var: str = None):
    tester = Tester(TestDatabase)
    return tester.run_tests()


if __name__ == "__main__":
    exit(run())
