"""
The trust boundary of the whole system lives here.

Everything on the far side of `SandboxRunner.run()` is untrusted: it may try
to read the host filesystem, exhaust memory/CPU/process count, reach the
network, or simply hang forever. A concrete runner's entire job is making
sure none of that escapes containment, no matter what the submitted code
tries.

This is an abstract interface (not just calling DockerSandboxRunner directly)
so the judge and its tests depend on this contract rather than on Docker
specifically. Swapping in an nsjail-based runner later — faster cold starts,
finer-grained seccomp control, no container-image overhead — means writing
one new class that satisfies this interface, not touching judge.py.
"""
from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass(frozen=True)
class TestCaseInput:
    id: str  # str(uuid) — kept as a string end-to-end since it round-trips
    input: str  # through JSON into the container and back without fuss
    expected_output: str


@dataclass(frozen=True)
class TestCaseOutcome:
    test_case_id: str
    passed: bool
    actual_output: str | None
    error: str | None
    runtime_ms: float | None


@dataclass(frozen=True)
class SandboxRunResult:
    outcomes: list[TestCaseOutcome]
    # Set when the *sandbox itself* failed to produce a verdict — container
    # OOM-killed, harness crashed, container-level timeout hit — as opposed
    # to the submitted program legitimately failing a test. Kept distinct
    # from "wrong answer" / "runtime error" because those are the player's
    # fault; an infra_error is ours to investigate.
    infra_error: str | None = None


class SandboxRunner(ABC):
    @abstractmethod
    def run(
        self, code: str, test_cases: list[TestCaseInput], language: str
    ) -> SandboxRunResult:
        """Runs `code` against every test case in one throwaway sandbox and
        returns a verdict per case. Must never raise on account of the
        submitted code itself (crashes/timeouts get folded into per-case
        outcomes) — only truly infrastructural failure should surface as
        `SandboxRunResult.infra_error`."""
        raise NotImplementedError
