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


class ServerMetrics(BaseModel):
    cpu_percent: float | None = None
    memory_used_bytes: int | None = None
    memory_limit_bytes: int | None = None
    data_volume_free_bytes: int | None = None
    tps: float | None = None
    mspt: float | None = None
    checked_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class LogEvent(BaseModel):
    event_type: str
    timestamp: str | None = None
    player: str | None = None
    message: str
    trusted: bool = False
    source: str


class LogError(BaseModel):
    timestamp: str | None = None
    category: str
    message: str
    trusted: bool = False
    source: str = "server_log"


class BackupInfo(BaseModel):
    backup_id: str
    created_at: datetime
    size_bytes: int = Field(ge=0)
    sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    reason: str


class ApprovalRequestInfo(BaseModel):
    request_id: str
    operation: str
    status: str
    created_at: datetime
    expires_at: datetime
    arguments: dict[str, object] = Field(default_factory=dict)
    decision_note: str | None = None
    result_message: str | None = None
    error_code: str | None = None
