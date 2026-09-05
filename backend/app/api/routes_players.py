import uuid

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import PlayerRating
from app.db.session import get_db
from app.schemas.players import PlayerRatingPublic

router = APIRouter(prefix="/players", tags=["players"])


@router.get("/{player_id}/rating", response_model=PlayerRatingPublic)
async def get_player_rating(player_id: uuid.UUID, db: AsyncSession = Depends(get_db)):
    row = await db.get(PlayerRating, player_id)
    if row is None:
        # No PlayerRating row is created until a player's first match
        # actually completes (see matches/rating.py) -- a player who has
        # never finished one is, correctly, at the default rating rather
        # than a 404: everyone starts here, it's just not persisted until
        # it changes.
        return PlayerRatingPublic(player_id=player_id)
    return PlayerRatingPublic(
        player_id=row.player_id, rating=row.rating, matches_played=row.matches_played
    )
