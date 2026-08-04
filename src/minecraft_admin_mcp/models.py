from datetime import UTC, datetime

from pydantic import BaseModel, Field


class Identity(BaseModel):
    server_id: str
    name: str
    aliases: list[str]
    environment: str
    risk_level: str
    minecraft_version: str
    loader: str


class PlayerList(BaseModel):
    online: int = Field(ge=0)
    maximum: int | None = Field(default=None, ge=0)
    players: list[str]


class ServerStatus(BaseModel):
    minecraft_reachable: bool
    rcon_reachable: bool
    online_players: int | None = None
    maximum_players: int | None = None
    players: list[str] = Field(default_factory=list)
    uptime_seconds: int | None = None
    checked_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class ActionResult(BaseModel):
    success: bool = True
    message: str
