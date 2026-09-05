import uuid

from pydantic import BaseModel

from app.db.models import SubmissionStatus


class SubmissionCreate(BaseModel):
    problem_id: uuid.UUID
    code: str
    language: str = "python"
    # Both optional together: omit both for a standalone practice submission
    # (phase 1's original use case), or provide both to attach this
    # submission to a live duel -- which is what makes the judge publish a
    # live progress update to the match's WebSocket room. See
    # routes_submissions.py for the "provide both or neither" validation.
    match_id: uuid.UUID | None = None
    player_id: uuid.UUID | None = None


class TestCaseResultPublic(BaseModel):
    """
    Per-test-case verdict returned to the client.

    For hidden test cases, input/expected_output/actual_output are always
    None regardless of what's in the DB — this is the one place that
    redaction has to happen correctly, since it's the boundary between
    "data the server needs to grade" and "data an opponent (or the judge
    UI) is allowed to see". See execution/judge.py `to_public_results`.
    """
    test_case_id: uuid.UUID
    is_hidden: bool
    passed: bool
    input: str | None
    expected_output: str | None
    actual_output: str | None
    error: str | None
    runtime_ms: float | None


class SubmissionResult(BaseModel):
    id: uuid.UUID
    problem_id: uuid.UUID
    status: SubmissionStatus
    passed_count: int
    total_count: int
    results: list[TestCaseResultPublic]
