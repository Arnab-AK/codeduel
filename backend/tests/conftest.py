import docker
import pytest

from app.config import settings


@pytest.fixture(autouse=True)
def fast_sandbox_limits(monkeypatch):
    """Shrinks timeouts/limits for the whole test run so adversarial tests
    (infinite loops, fork bombs) resolve in ~2s instead of the production
    default -- these are read from `settings` at call-time in
    docker_runner.py, so monkeypatching the settings object is enough."""
    monkeypatch.setattr(settings, "sandbox_timeout_seconds", 2)
    monkeypatch.setattr(settings, "sandbox_pids_limit", 32)
    monkeypatch.setattr(settings, "sandbox_memory_limit", "128m")


@pytest.fixture(autouse=True)
def no_leftover_sandbox_containers():
    """
    Runs after every single test in this suite (not just the adversarial
    ones) and asserts DockerSandboxRunner didn't leak a container. This is
    the automated version of "trust but verify" for the
    `finally: container.remove(force=True)` guarantee in docker_runner.py --
    if any test (happy path, timeout, OOM, fork bomb) leaves a container
    behind, this fails immediately with that test's name attached, rather
    than someone noticing stray containers accumulating in `docker ps -a`
    days later.
    """
    yield
    client = docker.from_env()
    leftover = client.containers.list(
        all=True, filters={"ancestor": "codeduel-python-runner:latest"}
    )
    assert leftover == [], (
        "DockerSandboxRunner leaked a container -- every run() must remove "
        f"its container regardless of outcome. Leftover: {[c.id for c in leftover]}"
    )
