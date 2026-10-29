"""Canister scaling suite: the cases in src/scaling_cases.py, in IC instructions.

For each case, builds its world at N_SMALL and N_LARGE in the test canister
and compares the instructions its operation used (ic.performance_counter):

- BOUNDED: the N_LARGE cost may exceed the N_SMALL cost by at most
  FACTOR x + SLACK instructions.
- GUARDED: works at N_SMALL and raises TooLargeError at N_LARGE.

Cases marked ``known`` must fail (their issue is open); one that passes fails
the run, so its marker is removed with the fix. Exit code 0 = all as expected.
"""

import os
import re
import subprocess
import sys

sys.path.append(os.path.join(os.path.dirname(os.path.abspath(__file__)), "src"))

import scaling_cases as sc  # noqa: E402

N_SMALL = 50
N_LARGE = sc.CAP + 500
FACTOR, SLACK = 1.5, 5_000_000
TIMEOUT = 600
CHUNK = 250  # world members added per call

GREEN, RED, YELLOW, RESET = "\033[92m", "\033[91m", "\033[93m", "\033[0m"


def call(method, arg):
    """(int result, error) of one run_test call; error is '' on success."""
    cmd = f'dfx canister call test run_test \'("scaling", "{method}", "{arg}")\''
    try:
        proc = subprocess.run(
            cmd, shell=True, text=True, capture_output=True, timeout=TIMEOUT
        )
    except subprocess.TimeoutExpired:
        return None, "timeout"
    if proc.returncode != 0:
        # The call trapped, e.g. on the instruction or a size limit
        output = proc.stdout + proc.stderr
        reason = re.search(r"(Canister \S+: [^\n]*?(trapped|exceeded)[^\n]*)", output)
        return None, "trap: " + (reason.group(1)[:160] if reason else output[-160:])
    value = re.search(r"\((-?[\d_]+)\s*:\s*int\)", proc.stdout)
    if not value:
        return None, "no result: " + proc.stdout[-160:]
    return int(value.group(1).replace("_", "")), ""


def measure(name, n):
    """(instructions, error) for one case at size n; error is '' on success."""
    _, error = call("start", name)
    for lo in range(0, n, CHUNK):
        if error:
            break
        _, error = call("add", f"{lo}:{min(n, lo + CHUNK)}")
    if error:
        return None, f"building the world: {error}"
    result, error = call("measure", "")
    if error:
        return None, error
    if result == -2:
        return None, "TooLargeError"
    if result < 0:
        return None, "error (see the replica log for SCALE_ERROR)"
    return result, ""


def check(case):
    """(passed, detail) for one case."""
    small, small_err = measure(case.name, N_SMALL)
    if small_err:
        return False, f"n={N_SMALL}: {small_err}"
    large, large_err = measure(case.name, N_LARGE)
    if case.kind == sc.GUARDED:
        if large_err == "TooLargeError":
            return True, f"{small:,} at n={N_SMALL}; TooLargeError at n={N_LARGE}"
        return False, f"n={N_LARGE}: expected TooLargeError, got {large_err or large}"
    if large_err:
        return False, f"n={N_LARGE}: {large_err}"
    detail = f"{small:,} -> {large:,} instructions"
    return large <= small * FACTOR + SLACK, detail


def check_heap():
    """Entities loaded by earlier calls must not stay in the heap (#23)."""
    _, error = call("start", "load")
    for lo in range(0, N_LARGE, CHUNK):
        if error:
            break
        _, error = call("add", f"{lo}:{min(N_LARGE, lo + CHUNK)}")
    size, error = (None, error) if error else call("registry_size", "")
    passed = not error and size <= CHUNK
    color = GREEN if passed else RED
    detail = error or f"{size} entities still held after {N_LARGE} were created"
    print(
        f"{color}{'ok' if passed else 'FAIL':34}{RESET} {'heap_between_calls':24} {detail}"
    )
    return passed


def main():
    cases = [c for c in sc.CASES if c.kind in (sc.BOUNDED, sc.GUARDED)]
    if len(sys.argv) > 1:
        cases = [c for c in cases if c.name in sys.argv[1:]]
    unexpected = 0
    for case in cases:
        passed, detail = check(case)
        known = None if case.name in sc.LOCAL_ONLY_KNOWN else case.known
        if known:
            issue = sc.KNOWN[known][0]
            if passed:
                status, color = f"XPASS ({issue}: remove the marker)", RED
                unexpected += 1
            else:
                status, color = f"xfail ({issue})", YELLOW
        elif passed:
            status, color = "ok", GREEN
        else:
            status, color = "FAIL", RED
            unexpected += 1
        print(f"{color}{status:34}{RESET} {case.name:24} {detail}", flush=True)
    if len(sys.argv) == 1 or "heap_between_calls" in sys.argv[1:]:
        unexpected += 0 if check_heap() else 1
    print(f"\n{unexpected} unexpected result(s)")
    return 1 if unexpected else 0


if __name__ == "__main__":
    sys.exit(main())
