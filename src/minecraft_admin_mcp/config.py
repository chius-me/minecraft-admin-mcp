import os
from enum import StrEnum
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict, Field, SecretStr, ValidationError, field_validator

from .errors import ErrorCode, MinecraftAdminError


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class PermissionState(StrEnum):
    ALLOW = "allow"
    APPROVAL = "approval"
    DISABLED = "disabled"


class ServerConfig(StrictModel):
    id: str = Field(pattern=r"^[a-z0-9][a-z0-9_-]{0,63}$")
    name: str = Field(min_length=1, max_length=100)
    aliases: list[str] = Field(default_factory=list)
    environment: str = "production"
    risk_level: str = "high"


class MinecraftConfig(StrictModel):
    version: str
    loader: str
    data_directory: Path = Path("/minecraft")
    log_file: Path = Path("/minecraft/logs/latest.log")
    world_directories: list[Path] = Field(default_factory=list)


class RconConfig(StrictModel):
    host: str
    port: int = Field(default=25575, ge=1, le=65535)
    password_env: str = "MC_RCON_PASSWORD"  # noqa: S105 - environment variable name
    timeout_seconds: float = Field(default=10, gt=0, le=60)


class HttpConfig(StrictModel):
    host: str = "0.0.0.0"  # noqa: S104 - container server default
    port: int = Field(default=8101, ge=1, le=65535)
    path: str = "/mcp"
    token_env: str = "MC_MCP_TOKEN"  # noqa: S105 - environment variable name

    @field_validator("path")
    @classmethod
    def validate_path(cls, value: str) -> str:
        if not value.startswith("/") or "?" in value or "#" in value:
            raise ValueError("HTTP path must be an absolute path without query or fragment")
        return value.rstrip("/") or "/"


class AuditConfig(StrictModel):
    database: Path = Path("/var/lib/minecraft-admin-mcp/audit.db")


class RateLimitRule(StrictModel):
    calls: int = Field(gt=0)
    period_seconds: float = Field(gt=0)


DEFAULT_RATE_LIMITS = {
    "broadcast": RateLimitRule(calls=5, period_seconds=60),
    "kick_player": RateLimitRule(calls=10, period_seconds=60),
    "whitelist_write": RateLimitRule(calls=20, period_seconds=60),
}


V01_TOOLS = (
    "get_identity",
    "get_status",
    "list_players",
    "get_whitelist",
    "broadcast",
    "whitelist_add",
    "whitelist_remove",
    "kick_player",
    "save_world",
)


class AppConfig(StrictModel):
    server: ServerConfig
    minecraft: MinecraftConfig
    rcon: RconConfig
    http: HttpConfig = Field(default_factory=HttpConfig)
    audit: AuditConfig = Field(default_factory=AuditConfig)
    permissions: dict[str, PermissionState]
    rate_limits: dict[str, RateLimitRule] = Field(default_factory=lambda: dict(DEFAULT_RATE_LIMITS))
    rcon_password: SecretStr
    mcp_token: SecretStr

    @field_validator("permissions")
    @classmethod
    def validate_permissions(cls, value: dict[str, PermissionState]) -> dict[str, PermissionState]:
        unknown = set(value) - set(V01_TOOLS)
        if unknown:
            raise ValueError(f"unsupported permissions: {', '.join(sorted(unknown))}")
        return value


def load_config(path: Path | str | None = None) -> AppConfig:
    selected_path: Path | str = (
        path if path is not None else os.getenv("MC_ADMIN_CONFIG", "config/config.yaml")
    )
    config_path = Path(selected_path)
    try:
        raw: Any = yaml.safe_load(config_path.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            raise ValueError("configuration root must be a mapping")
        rcon_env = raw.get("rcon", {}).get("password_env", "MC_RCON_PASSWORD")
        token_env = raw.get("http", {}).get("token_env", "MC_MCP_TOKEN")
        rcon_password = os.getenv(rcon_env)
        mcp_token = os.getenv(token_env)
        if not rcon_password:
            raise ValueError(f"required secret environment variable {rcon_env} is not set")
        if not mcp_token:
            raise ValueError(f"required secret environment variable {token_env} is not set")
        if len(mcp_token) < 32:
            raise ValueError("MCP bearer token must contain at least 32 characters")
        return AppConfig.model_validate(
            {**raw, "rcon_password": rcon_password, "mcp_token": mcp_token}
        )
    except (OSError, ValueError, ValidationError, yaml.YAMLError) as exc:
        raise MinecraftAdminError(ErrorCode.CONFIG_INVALID, str(exc)) from None
