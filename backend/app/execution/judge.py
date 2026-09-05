"""
Orchestrates grading one Submission: loads its problem's test cases, runs the
code through a SandboxRunner, maps the raw sandbox result onto the
Submission's status/results, and leaves it ready to persist.

Concurrency: sandbox execution is bounded by a process-wide semaphore
(SANDBOX_MAX_CONCURRENT). Each run is a real Docker container consuming real
host CPU/memory — without a bound, a burst of submissions (or, from phase 4
on, two duel players submitting within the same second) could spin up
unbounded containers and take the host down. The semaphore turns "grade this
submission" into a queue of at most N concurrent; everything past that
blocks until a slot frees up, rather than piling more load onto the host.

docker-py's calls are synchronous (blocking HTTP to the daemon under the
hood), so the actual run happens via asyncio.to_thread rather than being
awaited directly — otherwise one sandbox run would block the entire event
loop for its whole duration, stalling every other request the process is
handling.
"""
import asyncio

from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db.models import Submission, SubmissionStatus, TestCase
from app.execution.docker_runner import DockerSandboxRunner
from app.execution.sandbox import SandboxRunner, TestCaseInput

_sandbox_semaphore = asyncio.Semaphore(settings.sandbox_max_concurrent)

# Failures worse than a plain wrong answer, ranked so that when a submission
# fails several test cases for different reasons, the aggregate status
# reflects the most severe one deterministically (not just "whichever test
# case happened to run last").
_STATUS_SEVERITY = {
    SubmissionStatus.WRONG_ANSWER: 1,
    SubmissionStatus.RUNTIME_ERROR: 2,
    SubmissionStatus.TIMEOUT: 2,
}


class Judge:
    def __init__(self, runner: SandboxRunner | None = None):
        self._runner = runner or DockerSandboxRunner()

    async def grade(
        self, db: AsyncSession, submission: Submission, test_cases: list[TestCase]
    ) -> Submission:
        submission.status = SubmissionStatus.RUNNING
        await db.flush()

        inputs = [
            TestCaseInput(id=str(tc.id), input=tc.input, expected_output=tc.expected_output)
            for tc in test_cases
        ]

        async with _sandbox_semaphore:
            result = await asyncio.to_thread(
                self._runner.run, submission.code, inputs, submission.language
            )

        if result.infra_error:
            # Ours to fix, not the player's fault -- kept distinct from
            # RUNTIME_ERROR so a dashboard could alert on this separately
            # from "someone's solution has a bug".
            submission.status = SubmissionStatus.INTERNAL_ERROR
            submission.results = [{"infra_error": result.infra_error}]
            submission.passed_count = 0
            submission.total_count = len(test_cases)
            return submission

        by_id = {str(tc.id): tc for tc in test_cases}
        results = []
        passed_count = 0
        overall_status = SubmissionStatus.ACCEPTED
        worst_severity = 0

        for outcome in result.outcomes:
            tc = by_id[outcome.test_case_id]
            if outcome.passed:
                passed_count += 1
            else:
                if outcome.error and "Time limit exceeded" in outcome.error:
                    candidate = SubmissionStatus.TIMEOUT
                elif outcome.error:
                    candidate = SubmissionStatus.RUNTIME_ERROR
                else:
                    candidate = SubmissionStatus.WRONG_ANSWER
                if _STATUS_SEVERITY[candidate] > worst_severity:
                    worst_severity = _STATUS_SEVERITY[candidate]
                    overall_status = candidate

            results.append(
                {
                    "test_case_id": outcome.test_case_id,
                    "is_hidden": tc.is_hidden,
                    "passed": outcome.passed,
                    # Stored unredacted -- the DB needs the full picture to
                    # be useful for debugging a grading dispute. Redaction
                    # of hidden test cases happens at the API boundary, see
                    # `to_public_results` below.
                    "input": tc.input,
                    "expected_output": tc.expected_output,
                    "actual_output": outcome.actual_output,
                    "error": outcome.error,
                    "runtime_ms": outcome.runtime_ms,
                }
            )

        submission.status = overall_status
        submission.passed_count = passed_count
        submission.total_count = len(test_cases)
        submission.results = results
        return submission


def to_public_results(raw_results: list[dict]) -> list[dict]:
    """Strips hidden test cases' input/expected/actual output before a
    result ever reaches an HTTP response. This is the one place that
    redaction has to be correct -- everything upstream of it (the DB,
    Judge.grade) intentionally keeps the full detail."""
    public = []
    for r in raw_results:
        if r.get("is_hidden"):
            public.append({**r, "input": None, "expected_output": None, "actual_output": None})
        else:
            public.append(r)
    return public
