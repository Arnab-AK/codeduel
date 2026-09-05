import uuid

from pydantic import BaseModel

from app.matches.rating import DEFAULT_RATING


class PlayerRatingPublic(BaseModel):
    player_id: uuid.UUID
    rating: int = DEFAULT_RATING
    matches_played: int = 0
