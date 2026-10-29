"""Canister side of the scaling suite, driven by tests/entrypoint_scaling.py.

A case's world is built over several update calls (start, then add in
chunks), so a world too large to build in one message still gets measured,
and its build cost never mixes with the operation's. The world's state stays
in the heap between calls.
"""

import gc

import scaling_cases as sc
from tester import Tester

from ic_python_db import Database, Entity
from ic_python_db._cdk import ic

# Returned by measure() instead of an instruction count
ERROR = -1
TOO_LARGE = -2

_current = {}


class TestScaling:
    def start(self, name: str):
        """Empty the database and start the world of case ``name``."""
        Database.get_instance().clear()
        Entity._context.clear()
        case = sc.BY_NAME[name]
        _current["case"] = case
        _current["world"] = case.world.start()
        return 0

    def add(self, span: str):
        """Add members ``lo`` to ``hi`` (exclusive) to the world; span is "lo:hi"."""
        lo, hi = (int(x) for x in span.split(":"))
        case, world = _current["case"], _current["world"]
        for i in range(lo, hi):
            case.world.add(world, i)
        return 0

    def registry_size(self, _unused: str = None):
        """Entities in the identity map as seen by a new message."""
        return len(Database.get_instance()._registry())

    def measure(self, _unused: str = None):
        """Instructions the case's operation uses, or ERROR / TOO_LARGE."""
        case, world = _current["case"], _current["world"]
        sc.cold()
        gc.collect()
        before = ic.performance_counter(0)
        try:
            case.op(world)
        except Exception as e:
            ic.print(f"SCALE_ERROR:{case.name}:{type(e).__name__}:{e}")
            return TOO_LARGE if type(e).__name__ == "TooLargeError" else ERROR
        return ic.performance_counter(0) - before


def run(test_name: str = None, test_var: str = None):
    tester = Tester(TestScaling)
    return tester.run_test(test_name, test_var)
