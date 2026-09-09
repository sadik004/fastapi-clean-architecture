"""Pydantic schemas for real-time leaderboard service using Redis Sorted Sets."""

from pydantic import BaseModel, ConfigDict, Field


class ScoreUpdateRequest(BaseModel):
    """Payload for incrementing or decrementing a player's score on a leaderboard."""

    model_config = ConfigDict(extra="forbid")

    score_delta: float = Field(
        ...,
        description="Floating-point amount to increment (or decrement if negative) the player's score",
    )


class ScoreUpdateResponse(BaseModel):
    """Response returned upon successfully recording a player's score."""

    model_config = ConfigDict(extra="forbid")

    leaderboard_id: str = Field(..., description="Target leaderboard scope identifier")
    player_id: str = Field(..., description="Unique player identifier")
    new_score: float = Field(..., description="Updated accumulated score of the player")
    rank: int = Field(..., ge=1, description="Updated 1-indexed rank of the player (1 = highest score)")


class LeaderboardEntry(BaseModel):
    """A single ranked competitor entry in a leaderboard slice."""

    model_config = ConfigDict(extra="forbid")

    rank: int = Field(..., ge=1, description="1-indexed rank of the player (1 = highest score)")
    player_id: str = Field(..., description="Unique player identifier")
    score: float = Field(..., description="Current accumulated score of the player")


class PlayerStanding(BaseModel):
    """Detailed standing and performance percentile of an individual player."""

    model_config = ConfigDict(extra="forbid")

    leaderboard_id: str = Field(..., description="Target leaderboard scope identifier")
    player_id: str = Field(..., description="Unique player identifier")
    score: float = Field(..., description="Current accumulated score of the player")
    rank: int = Field(..., ge=1, description="1-indexed rank of the player (1 = highest score)")
    total_players: int = Field(..., ge=1, description="Total number of active competitors on the leaderboard")
    percentile: float = Field(
        ...,
        ge=0.0,
        le=100.0,
        description="Calculated competitive percentile (e.g. 99.0 means outperforming 99% of players)",
    )
