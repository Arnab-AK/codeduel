"""
Runs entirely inside the sandbox container, as the unprivileged, network-less
`sandboxuser`. This is the ONLY code that ever executes a submitted program;
it treats /sandbox/solution.py as fully untrusted and assumes nothing about
what it might do.

Contract with the host side (execution/docker_runner.py):
  reads  /sandbox/testcases.json  ->  [{"id", "input", "expected_output"}, ...]
  reads  /sandbox/solution.py     ->  the submitted program
  writes /sandbox/result.json     ->  {"outcomes": [...], "infra_error": str|null}

Why a fresh subprocess per test case instead of importing solution.py once
and calling a function: importing would run top-level code exactly once and
share interpreter/module state across every test case in the submission —
one test case could leak state into (or crash) the next. A subprocess per
case costs a bit of startup time but gives each test case a completely
clean process, which is the same isolation guarantee we want at the
container level, just one level down.

Timeout handling: subprocess.run()'s own timeout only kills the immediate
child process, not any children *it* spawns — a fork bomb would survive
that. So the child is started in its own process group (start_new_session)
and, on timeout, we kill the whole group. This is still only the SECOND
layer of defense: the outer container-level timeout in docker_runner.py
(which kills the entire cgroup, full stop) is what actually guarantees
termination no matter what the submitted code does.
"""
import json
import os
import signal
import subprocess
import sys
import time

SANDBOX_DIR = "/sandbox"
SOLUTION_PATH = os.path.join(SANDBOX_DIR, "solution.py")
TESTCASES_PATH = os.path.join(SANDBOX_DIR, "testcases.json")
RESULT_PATH = os.path.join(SANDBOX_DIR, "result.json")

TIMEOUT_SECONDS = float(os.environ.get("CODEDUEL_TIMEOUT_SECONDS", "5"))
# Caps how much stdout we ever carry back out of the sandbox, so a
# `while True: print(...)` can't inflate result.json without bound.
MAX_OUTPUT_CHARS = 64 * 1024


def truncate(s: str) -> str:
    if len(s) > MAX_OUTPUT_CHARS:
        return s[:MAX_OUTPUT_CHARS] + "\n...[truncated]"
    return s


def run_one(test_case: dict) -> dict:
    start = time.monotonic()
    proc = subprocess.Popen(
        [sys.executable, SOLUTION_PATH],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        start_new_session=True,  # own process group -> killable as a whole
    )
    try:
        stdout, stderr = proc.communicate(input=test_case["input"], timeout=TIMEOUT_SECONDS)
        returncode = proc.returncode
    except subprocess.TimeoutExpired:
        try:
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
        except ProcessLookupError:
            pass  # process already gone between the timeout firing and here
        proc.communicate()  # reap the now-dead process, avoid a zombie
        return {
            "test_case_id": test_case["id"],
            "passed": False,
            "actual_output": None,
            "error": f"Time limit exceeded ({TIMEOUT_SECONDS}s)",
            "runtime_ms": TIMEOUT_SECONDS * 1000,
        }

    runtime_ms = (time.monotonic() - start) * 1000

    if returncode != 0:
        return {
            "test_case_id": test_case["id"],
            "passed": False,
            "actual_output": truncate(stdout),
            "error": truncate(stderr) or f"Process exited with code {returncode}",
            "runtime_ms": runtime_ms,
        }

    actual = stdout.strip()
    expected = test_case["expected_output"].strip()
    return {
        "test_case_id": test_case["id"],
        "passed": actual == expected,
        "actual_output": truncate(stdout),
        "error": None,
        "runtime_ms": runtime_ms,
    }


def main():
    try:
        with open(TESTCASES_PATH) as f:
            test_cases = json.load(f)
        outcomes = [run_one(tc) for tc in test_cases]
        result = {"outcomes": outcomes, "infra_error": None}
    except Exception as exc:
        # The harness itself failed (bad JSON, missing file, unexpected
        # crash) -- distinct from the submitted program failing, which is
        # always captured as a per-test-case outcome above, never here.
        result = {"outcomes": [], "infra_error": f"{type(exc).__name__}: {exc}"}

    with open(RESULT_PATH, "w") as f:
        json.dump(result, f)


if __name__ == "__main__":
    main()
