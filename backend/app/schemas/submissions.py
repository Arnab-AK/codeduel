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
    # True only for the one request that actually won a match-completion
    # race (see matches/completion.py) -- always False for a standalone
    # submission. Told to the winner directly in their own HTTP response
    # rather than relying on them having their own WebSocket message for
    # their own outcome; the opponent still learns via the WS "match_complete"
    # push (routes_ws.py).
    won_match: bool = False
    # The winner's new Elo rating, set only alongside won_match=True. See
    # matches/rating.py.
    new_rating: int | None = None
