import os
import sys

# The harness lives here; the shared cases live in the canister's source tree.
# Appended, not prepended: tests/src/ic_python_db is an empty Docker mount point.
sys.path.insert(0, os.path.dirname(__file__))
sys.path.append(os.path.join(os.path.dirname(__file__), "..", "src"))


def pytest_terminal_summary(terminalreporter):
    from harness import COSTS, N_LARGE, N_SMALL

    if not COSTS:
        return
    write = terminalreporter.write_line
    write("")
    write(f"Bounded operation costs (storage ops / bytes), n={N_SMALL} vs n={N_LARGE}")
    for name, (small, large) in sorted(COSTS.items()):
        write(
            f"  {name:24} {small.ops:>6} / {small.bytes:>8}   "
            f"{large.ops:>6} / {large.bytes:>8}"
        )
