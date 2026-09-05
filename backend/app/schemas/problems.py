import uuid

from pydantic import BaseModel, ConfigDict

from app.db.models import Difficulty


class TestCaseExample(BaseModel):
    """Only ever built from non-hidden test cases — see ProblemDetail below."""
    model_config = ConfigDict(from_attributes=True)

    input: str
    expected_output: str


class ProblemSummary(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    slug: str
    title: str
    difficulty: Difficulty


class ProblemDetail(ProblemSummary):
    description: str
    # Deliberately excludes hidden test cases: this schema is what a player
    # sees, and showing them would let players special-case the grader
    # instead of solving the general problem.
    examples: list[TestCaseExample]
