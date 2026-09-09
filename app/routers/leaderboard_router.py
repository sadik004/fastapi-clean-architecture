"""Router exposing real-time leaderboard endpoints backed by Redis Sorted Sets (ZSET)."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Path, Query, status

from app.core.dependencies import get_leaderboard_service
from app.core.exceptions import PlayerNotFoundException
from app.schemas.leaderboard import (
    LeaderboardEntry,
    PlayerStanding,
    ScoreUpdateRequest,
    ScoreUpdateResponse,
)
from app.services.leaderboard_service import LeaderboardService

router = APIRouter(prefix="/leaderboard", tags=["Leaderboard"])


@router.post(
    "/{leaderboard_id}/scores/{player_id}",
    response_model=ScoreUpdateResponse,
    status_code=status.HTTP_200_OK,
    summary="Record or increment a player's score",
    description=(
        "Atomically increments a player's score on the specified leaderboard scope in O(log N) time. "
        "Returns the player's updated score and human-readable 1-indexed rank."
    ),
)
async def update_player_score(
    leaderboard_id: Annotated[str, Path(..., min_length=1, description="Leaderboard scope identifier")],
    player_id: Annotated[str, Path(..., min_length=1, description="Unique player identifier")],
    payload: ScoreUpdateRequest,
    service: Annotated[LeaderboardService, Depends(get_leaderboard_service)],
) -> ScoreUpdateResponse:
    """Increment player score in O(log N) time and return updated score and rank."""
    new_score, rank = await service.record_score(
        leaderboard_id=leaderboard_id,
        player_id=player_id,
        score_delta=payload.score_delta,
    )
    return ScoreUpdateResponse(
        leaderboard_id=leaderboard_id,
        player_id=player_id,
        new_score=new_score,
        rank=rank,
    )


@router.get(
    "/{leaderboard_id}/top",
    response_model=list[LeaderboardEntry],
    status_code=status.HTTP_200_OK,
    summary="Retrieve top-ranked competitors",
    description=(
        "Returns a paginated slice of top players ordered from highest to lowest score in O(log N + M) time. "
        "Enforces strict limit and offset validation bounds to prevent unbounded memory consumption."
    ),
)
async def get_top_leaderboard(
    leaderboard_id: Annotated[str, Path(..., min_length=1, description="Leaderboard scope identifier")],
    service: Annotated[LeaderboardService, Depends(get_leaderboard_service)],
    limit: Annotated[int, Query(ge=1, le=100, description="Maximum number of competitors to return")] = 10,
    offset: Annotated[int, Query(ge=0, description="0-based starting offset")] = 0,
) -> list[LeaderboardEntry]:
    """Retrieve top-ranked competitors with 1-indexed ranks."""
    return await service.get_top_players(
        leaderboard_id=leaderboard_id,
        limit=limit,
        offset=offset,
    )


@router.get(
    "/{leaderboard_id}/standing/{player_id}",
    response_model=PlayerStanding,
    status_code=status.HTTP_200_OK,
    summary="Get individual player standing and percentile",
    description=(
        "Retrieves a player's score, 1-indexed rank, total competitors, and calculated competitive percentile. "
        "Raises HTTP 404 (PlayerNotFoundException) if the player has no recorded score on the leaderboard."
    ),
)
async def get_player_standing(
    leaderboard_id: Annotated[str, Path(..., min_length=1, description="Leaderboard scope identifier")],
    player_id: Annotated[str, Path(..., min_length=1, description="Unique player identifier")],
    service: Annotated[LeaderboardService, Depends(get_leaderboard_service)],
) -> PlayerStanding:
    """Retrieve player's rank, score, total competitors, and percentile."""
    standing = await service.get_player_standing(
        leaderboard_id=leaderboard_id,
        player_id=player_id,
    )
    if standing is None:
        raise PlayerNotFoundException(player_id=player_id, leaderboard_id=leaderboard_id)
    return standing
