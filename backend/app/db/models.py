"""
Phase-1 schema: Problem, TestCase, Submission.

Design notes (why things look the way they do):

- Primary keys are server-generated UUIDs, not autoincrement ints. Problem and
  Submission ids are going to be exposed in URLs/API responses (e.g. a match
  invite link, a submission-result lookup); UUIDs avoid leaking sequential
  counts ("we've had 4,213 submissions") and avoid enumeration attacks on
  someone else's submission. The cost (16 bytes vs 4, non-sequential inserts)
  is irrelevant at this scale.

- TestCase.is_hidden exists because a duel platform (like a real judge) wants
  to run more test cases than it shows the player — otherwise players just
  hardcode outputs for the visible cases. The API layer (schemas/submissions.py)
  is responsible for redacting hidden test cases' input/expected/actual output
  before it ever reaches a response; the DB stores full detail for every case
  because we (the server) need it to grade correctly and to debug.

- Submission has no user_id yet. Auth is explicitly a later phase (6) — adding
  a dangling FK to a users table that doesn't exist yet would be worse than
  just adding the column when auth actually lands.

- Submission.results is JSONB holding the full per-test-case verdict array.
  This is a deliberate denormalization: we don't need to query "all
  submissions where test case #3 failed" across the corpus, so a normalized
  SubmissionTestCaseResult table would add a join for no read benefit. If a
  reporting/analytics use case ever needs to query into individual test
  results, that's the trigger to normalize it.
"""
import enum
import uuid
from datetime import datetime

from sqlalchemy import Enum as SAEnum
from sqlalchemy import ForeignKey, Text, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base


class Difficulty(str, enum.Enum):
    EASY = "easy"
    MEDIUM = "medium"
    HARD = "hard"


class SubmissionStatus(str, enum.Enum):
    PENDING = "pending"          # row created, execution not started yet
    RUNNING = "running"          # sandbox execution in flight
    ACCEPTED = "accepted"        # all test cases passed
    WRONG_ANSWER = "wrong_answer"  # ran fine, output didn't match on >=1 case
    RUNTIME_ERROR = "runtime_error"  # the submitted program crashed/raised
    TIMEOUT = "timeout"          # exceeded the per-test-case time limit
    INTERNAL_ERROR = "internal_error"  # our harness/sandbox failed, not the user's code


class Problem(Base):
    __tablename__ = "problems"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    slug: Mapped[str] = mapped_column(unique=True, index=True)
    title: Mapped[str]
    description: Mapped[str] = mapped_column(Text)
    difficulty: Mapped[Difficulty] = mapped_column(SAEnum(Difficulty, name="difficulty"))
    # Reserved for future languages beyond Python; execution/languages.py maps
    # this value to a runner image + invocation. Kept as free text (not FK)
    # since it's a small closed set the app code owns, not user data.
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())

    test_cases: Mapped[list["TestCase"]] = relationship(
        back_populates="problem", cascade="all, delete-orphan", order_by="TestCase.order_index"
    )


class TestCase(Base):
    __tablename__ = "test_cases"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    problem_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("problems.id", ondelete="CASCADE")
    )
    # Raw stdin fed to the submitted program, and the exact stdout expected
    # back (compared after stripping trailing whitespace). Chosen over a
    # function-call model (e.g. calling a `solve(a, b)` the user defines) so
    # the judge never has to import/introspect user code — it just runs their
    # whole program as a subprocess and diffs text. This also makes adding
    # a second language later (C++, JS) a matter of "compile/run this file",
    # not "figure out how to call into arbitrary user-defined signatures".
    input: Mapped[str] = mapped_column(Text)
    expected_output: Mapped[str] = mapped_column(Text)
    is_hidden: Mapped[bool] = mapped_column(default=False)
    order_index: Mapped[int] = mapped_column(default=0)

    problem: Mapped["Problem"] = relationship(back_populates="test_cases")


class Submission(Base):
    __tablename__ = "submissions"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    problem_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("problems.id", ondelete="CASCADE")
    )
    # Both nullable: a submission can stand alone (phase 1's original
    # use case -- practicing a problem outside any duel) or belong to a
    # live match (phase 3+). match_id has a real FK since Match already
    # exists; player_id doesn't, same reasoning as Match's own player
    # columns -- there's no users table yet for it to reference.
    match_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("matches.id", ondelete="CASCADE"), nullable=True
    )
    player_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    code: Mapped[str] = mapped_column(Text)
    language: Mapped[str] = mapped_column(default="python")
    status: Mapped[SubmissionStatus] = mapped_column(
        SAEnum(SubmissionStatus, name="submission_status"), default=SubmissionStatus.PENDING
    )
    passed_count: Mapped[int] = mapped_column(default=0)
    total_count: Mapped[int] = mapped_column(default=0)
    # Full per-test-case detail: [{id, passed, actual_output, error, runtime_ms}, ...]
    # See the module docstring for why this is JSONB rather than a child table.
    results: Mapped[list | None] = mapped_column(JSONB, nullable=True)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
    completed_at: Mapped[datetime | None] = mapped_column(nullable=True)


class MatchStatus(str, enum.Enum):
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    ABANDONED = "abandoned"  # a player disconnected/never submitted -- phase 3+ territory


class Match(Base):
    __tablename__ = "matches"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    # No FK to a users table, same reasoning as Submission having no
    # user_id: there is no users table yet (auth is phase 6). These are
    # "player ids" issued by the matchmaking queue itself (see
    # matchmaking/queue.py) -- opaque UUIDs today, destined to become real
    # authenticated user ids once phase 6 lands, without this schema or the
    # pairing logic needing to change.
    player_one_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    player_two_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    problem_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("problems.id")
    )
    status: Mapped[MatchStatus] = mapped_column(
        SAEnum(MatchStatus, name="match_status"), default=MatchStatus.IN_PROGRESS
    )
    # Set exactly once, by the single atomic UPDATE in
    # matches/completion.py that also flips status to COMPLETED -- see that
    # module for why this is safe under two players submitting an accepted
    # solution near-simultaneously. No FK, same reasoning as the player
    # columns above.
    winner_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
    completed_at: Mapped[datetime | None] = mapped_column(nullable=True)
