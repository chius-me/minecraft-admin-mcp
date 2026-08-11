import os
from enum import StrEnum
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    SecretStr,
    ValidationError,
    field_validator,
    model_validator,
)

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

    @model_validator(mode="after")
    def validate_fixed_paths(self) -> "MinecraftConfig":
        root = self.data_directory.resolve()
        if not self.data_directory.is_absolute():
            raise ValueError("minecraft data_directory must be absolute")
        configured_paths = [self.log_file, *self.world_directories]
        for path in configured_paths:
            if not path.is_absolute() or not path.resolve().is_relative_to(root):
                raise ValueError("Minecraft paths must be absolute and inside data_directory")
        return self


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


class BackupConfig(StrictModel):
    directory: Path = Path("/backups")
    compression: Literal["zstd"] = "zstd"
    retention_count: int = Field(default=7, ge=1, le=1000)
    timeout_seconds: float = Field(default=600, gt=0, le=86400)

    @field_validator("directory")
    @classmethod
    def validate_directory(cls, value: Path) -> Path:
        if not value.is_absolute():
            raise ValueError("backup directory must be absolute")
        return value


class LogsConfig(StrictModel):
    default_lines: int = Field(default=100, ge=1, le=200)
    maximum_lines: int = Field(default=500, ge=1, le=5000)
    maximum_characters: int = Field(default=30000, ge=1000, le=1_000_000)
    redact_ip_addresses: bool = True

    @model_validator(mode="after")
    def validate_line_limits(self) -> "LogsConfig":
        if self.default_lines > self.maximum_lines:
            raise ValueError("logs default_lines cannot exceed maximum_lines")
        return self


class RateLimitRule(StrictModel):
    calls: int = Field(gt=0)
    period_seconds: float = Field(gt=0)


DEFAULT_RATE_LIMITS = {
    "broadcast": RateLimitRule(calls=5, period_seconds=60),
    "kick_player": RateLimitRule(calls=10, period_seconds=60),
    "whitelist_write": RateLimitRule(calls=20, period_seconds=60),
    "create_backup": RateLimitRule(calls=1, period_seconds=300),
    "ban_player": RateLimitRule(calls=5, period_seconds=60),
    "restart": RateLimitRule(calls=1, period_seconds=300),
    "restore_backup": RateLimitRule(calls=1, period_seconds=600),
    "request_high_risk": RateLimitRule(calls=10, period_seconds=60),
}

# Permission keys accepted in YAML (includes high-risk ops introduced in V0.3).
SUPPORTED_PERMISSIONS = (
    "get_identity",
    "get_status",
    "list_players",
    "get_whitelist",
    "broadcast",
    "whitelist_add",
    "whitelist_remove",
    "kick_player",
    "save_world",
    "get_metrics",
    "get_recent_events",
    "get_recent_errors",
    "create_backup",
    "list_backups",
    "restart",
    "ban_player",
    "restore_backup",
)

# Backward-compatible alias used by older imports/tests.
SUPPORTED_TOOLS = SUPPORTED_PERMISSIONS


class ApprovalConfig(StrictModel):
    """V0.3 approval queue settings. Admin token is never stored in YAML."""

    database: Path | None = None
    default_ttl_seconds: float = Field(default=3600, gt=0, le=86400)
    admin_token_env: str = "MC_ADMIN_TOKEN"  # noqa: S105 - environment variable name


class AppConfig(StrictModel):
    server: ServerConfig
    minecraft: MinecraftConfig
    rcon: RconConfig
    http: HttpConfig = Field(default_factory=HttpConfig)
    backup: BackupConfig = Field(default_factory=BackupConfig)
    audit: AuditConfig = Field(default_factory=AuditConfig)
    logs: LogsConfig = Field(default_factory=LogsConfig)
    approval: ApprovalConfig = Field(default_factory=ApprovalConfig)
    permissions: dict[str, PermissionState]
    rate_limits: dict[str, RateLimitRule] = Field(default_factory=lambda: dict(DEFAULT_RATE_LIMITS))
    rcon_password: SecretStr
    mcp_token: SecretStr
    admin_token: SecretStr | None = None

    @field_validator("permissions")
    @classmethod
    def validate_permissions(cls, value: dict[str, PermissionState]) -> dict[str, PermissionState]:
        unknown = set(value) - set(SUPPORTED_PERMISSIONS)
        if unknown:
            raise ValueError(f"unsupported permissions: {', '.join(sorted(unknown))}")
        return value

    def approval_database(self) -> Path:
        if self.approval.database is not None:
            return self.approval.database
        return self.audit.database.parent / "approvals.db"


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
        approval_section = raw.get("approval")
        approval_raw: dict[str, Any] = (
            approval_section if isinstance(approval_section, dict) else {}
        )
        admin_token_env = str(approval_raw.get("admin_token_env", "MC_ADMIN_TOKEN"))
        rcon_password = os.getenv(rcon_env)
        mcp_token = os.getenv(token_env)
        admin_token = os.getenv(admin_token_env)
        if not rcon_password:
            raise ValueError(f"required secret environment variable {rcon_env} is not set")
        if not mcp_token:
            raise ValueError(f"required secret environment variable {token_env} is not set")
        if len(mcp_token) < 32:
            raise ValueError("MCP bearer token must contain at least 32 characters")
        if admin_token is not None and len(admin_token) < 32:
            raise ValueError("admin approval token must contain at least 32 characters")
        return AppConfig.model_validate(
            {
                **raw,
                "rcon_password": rcon_password,
                "mcp_token": mcp_token,
                "admin_token": admin_token,
            }
        )
    except (OSError, ValueError, ValidationError, yaml.YAMLError) as exc:
        raise MinecraftAdminError(ErrorCode.CONFIG_INVALID, str(exc)) from None
