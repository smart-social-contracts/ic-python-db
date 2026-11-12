"""Operation cost against the number of registered entity types.

An operation works on its own type's data, whatever other types the
application defines (a realm registers 93). The scaling suite counts storage
operations, which a loop over the registered classes does not make, so these
tests count the Python lines an operation runs.
"""

import sys

from harness import fresh_db

from ic_python_db import Entity, ManyToMany, ManyToOne, String

EXTRA_TYPES = 100
# Reading 100 classes costs thousands of lines; this only absorbs noise
LINES_SLACK = 20


def _lines_run(fn):
    """How many Python lines fn runs, counting those of everything it calls."""
    count = 0

    def trace(frame, event, arg):
        nonlocal count
        if event == "line":
            count += 1
        return trace

    previous = sys.gettrace()
    sys.settrace(trace)
    try:
        fn()
    finally:
        sys.settrace(previous)
    return count


def _register_unrelated_types(n):
    """n classes with every kind of attribute, none of them pointing at CostBadge."""
    for i in range(n):
        type(
            f"CostExtra{i}",
            (Entity,),
            {
                "name": String(),
                "others": ManyToMany(
                    "CostOther", f"extra_owners{i}", unidirectional=True
                ),
                "pals": ManyToMany("CostOther", f"extra_pals{i}"),
                "parent": ManyToOne("CostOther", f"extras{i}"),
            },
        )


def test_delete_cost_does_not_grow_with_the_registered_types():
    fresh_db()

    class CostBadge(Entity):
        name = String()

    class CostOther(Entity):
        name = String()

    class CostHolder(Entity):
        badges = ManyToMany("CostBadge", "holders", unidirectional=True)

    def delete_lines():
        """Lines run by deleting a badge that has a counter to delete."""
        badge = CostBadge(name="gold")
        CostHolder().badges.add(badge)
        return _lines_run(badge.delete)

    delete_lines()  # whatever the first delete builds
    few = delete_lines()
    _register_unrelated_types(EXTRA_TYPES)
    delete_lines()  # the registry changed, so this one may build again
    many = delete_lines()  # a later delete must not

    assert many <= few + LINES_SLACK, (
        f"delete runs {many} lines with {EXTRA_TYPES} more types registered, "
        f"{few} without"
    )
