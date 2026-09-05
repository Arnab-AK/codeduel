import uuid

from pydantic import BaseModel, ConfigDict

from app.db.models import MatchStatus


class MatchDetail(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    player_one_id: uuid.UUID
    player_two_id: uuid.UUID
    problem_id: uuid.UUID
    status: MatchStatus
    winner_id: uuid.UUID | None
