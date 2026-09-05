"""
Adversarial test suite for DockerSandboxRunner.

The whole point of the sandboxing work is a set of claims: "submitted code
can't reach the network", "a fork bomb can't take down the host", "an
infinite loop gets killed", etc. This file exists to prove each claim
against a real container rather than just trusting the flags in
docker_runner.py do what their comments say. Every test here submits code
built to violate exactly one boundary and asserts it was actually contained.

These tests are slower than typical unit tests (each one spins up a real
Docker container) and are intentionally not mocked -- mocking the sandbox
boundary in a test suite whose entire purpose is verifying that boundary
holds would defeat the point.
"""
from app.execution.docker_runner import DockerSandboxRunner
from app.execution.sandbox import TestCaseInput

runner = DockerSandboxRunner()


def _run(code: str, expected: str = ""):
    return runner.run(
        code=code,
        test_cases=[TestCaseInput(id="1", input="", expected_output=expected)],
        language="python",
    )


def test_correct_solution_is_accepted():
    result = _run("print('hello')", expected="hello")
    assert result.infra_error is None
    assert result.outcomes[0].passed is True


def test_wrong_output_is_wrong_answer_not_an_error():
    result = _run("print('nope')", expected="hello")
    assert result.infra_error is None
    outcome = result.outcomes[0]
    assert outcome.passed is False
    assert outcome.error is None  # ran fine, just produced the wrong output


def test_uncaught_exception_is_captured_not_propagated():
    result = _run("raise ValueError('boom')")
    outcome = result.outcomes[0]
    assert outcome.passed is False
    assert "boom" in outcome.error


def test_infinite_loop_is_killed_by_the_per_test_timeout():
    result = _run("while True:\n    pass\n")
    outcome = result.outcomes[0]
    assert outcome.passed is False
    assert "Time limit exceeded" in outcome.error


def test_fork_bomb_is_contained_by_pids_limit():
    # Spawns subprocesses as fast as possible. Without pids_limit this would
    # keep going until it exhausted the HOST's process table; with it, the
    # container hits its own cap almost immediately and Popen starts raising
    # OSError, which crashes the submitted program (not the harness, not the
    # host) well within the per-test timeout.
    code = (
        "import subprocess, sys\n"
        "while True:\n"
        "    subprocess.Popen([sys.executable, '-c', 'pass'])\n"
    )
    result = _run(code)
    assert result.infra_error is None
    assert result.outcomes[0].passed is False


def test_memory_bomb_is_contained_not_a_hang(monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "sandbox_memory_limit", "32m")
    code = (
        "data = []\n"
        "while True:\n"
        "    data.append(bytearray(10 * 1024 * 1024))\n"
    )
    result = _run(code)
    # Two legitimate outcomes depending on exactly which process the
    # cgroup's OOM killer picks: either the whole container gets OOM-killed
    # (infra_error), or just the offending subprocess dies and the harness
    # reports it as a failed outcome. Both are "contained". Neither is a
    # 30-second hang or a host running out of memory.
    if result.infra_error:
        assert "memory" in result.infra_error.lower() or "no result" in result.infra_error.lower()
    else:
        assert result.outcomes[0].passed is False


def test_network_access_is_blocked():
    code = (
        "import socket\n"
        "s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)\n"
        "s.settimeout(2)\n"
        "try:\n"
        "    s.connect(('8.8.8.8', 53))\n"
        "    print('CONNECTED')\n"
        "except OSError:\n"
        "    print('BLOCKED')\n"
    )
    result = _run(code, expected="BLOCKED")
    assert result.infra_error is None
    assert result.outcomes[0].actual_output.strip() == "BLOCKED"


def test_filesystem_write_outside_sandbox_is_blocked():
    code = (
        "try:\n"
        "    open('/etc/passwd', 'a').write('pwned')\n"
        "    print('WROTE')\n"
        "except PermissionError:\n"
        "    print('BLOCKED')\n"
    )
    result = _run(code, expected="BLOCKED")
    assert result.infra_error is None
    assert result.outcomes[0].actual_output.strip() == "BLOCKED"
