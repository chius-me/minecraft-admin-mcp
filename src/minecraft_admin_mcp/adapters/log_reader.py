import re
import unicodedata
from pathlib import Path

from ..audit import redact_ip_addresses
from ..config import LogsConfig
from ..errors import ErrorCode, MinecraftAdminError
from ..models import LogError, LogEvent

TIMESTAMP_PATTERN = re.compile(r"^\[(?P<timestamp>\d{2}:\d{2}:\d{2})\]")
PLAYER = r"[A-Za-z0-9_]{3,16}"
EVENT_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("player_join", re.compile(rf"(?P<player>{PLAYER}) joined the game$")),
    ("player_leave", re.compile(rf"(?P<player>{PLAYER}) left the game$")),
    (
        "player_kick",
        re.compile(rf"(?P<player>{PLAYER}) lost connection: (?P<detail>.+)$"),
    ),
    (
        "whitelist_add",
        re.compile(rf"Added (?P<player>{PLAYER}) to the whitelist$", re.IGNORECASE),
    ),
    (
        "whitelist_remove",
        re.compile(rf"Removed (?P<player>{PLAYER}) from the whitelist$", re.IGNORECASE),
    ),
)
CHAT_PATTERN = re.compile(rf"<(?P<player>{PLAYER})> (?P<detail>.*)$")
DEATH_PATTERN = re.compile(
    rf"(?P<player>{PLAYER}) (?P<detail>(?:was |died|fell |blew up|burned |drowned|"
    r"experienced kinetic energy|hit the ground|starved|suffocated|withered|walked into).*)$",
    re.IGNORECASE,
)


def _sanitize(value: str, *, redact_ips: bool) -> str:
    sanitized = "".join(
        character if not unicodedata.category(character).startswith("C") else "�"
        for character in value
    )
    return redact_ip_addresses(sanitized) if redact_ips else sanitized


class LogReader:
    def __init__(self, log_file: Path, config: LogsConfig) -> None:
        self._log_file = log_file
        self._config = config

    def _read_lines(self) -> list[str]:
        try:
            with self._log_file.open("rb") as log:
                log.seek(0, 2)
                size = log.tell()
                read_size = min(size, self._config.maximum_characters * 4)
                log.seek(-read_size, 2)
                text = log.read(read_size).decode("utf-8", errors="replace")
        except OSError:
            raise MinecraftAdminError(
                ErrorCode.LOG_UNAVAILABLE, "configured log is unavailable"
            ) from None
        text = text[-self._config.maximum_characters :]
        return text.splitlines()[-self._config.maximum_lines :]

    @staticmethod
    def _timestamp(line: str) -> str | None:
        match = TIMESTAMP_PATTERN.match(line)
        return match.group("timestamp") if match else None

    def get_recent_events(self, limit: int) -> list[LogEvent]:
        events: list[LogEvent] = []
        for raw_line in self._read_lines():
            line = _sanitize(raw_line, redact_ips=self._config.redact_ip_addresses)
            timestamp = self._timestamp(line)
            chat = CHAT_PATTERN.search(line)
            if chat:
                events.append(
                    LogEvent(
                        event_type="player_chat",
                        timestamp=timestamp,
                        player=chat.group("player"),
                        message=chat.group("detail"),
                        source="player_chat",
                    )
                )
                continue
            matched = False
            for event_type, pattern in EVENT_PATTERNS:
                match = pattern.search(line)
                if match:
                    events.append(
                        LogEvent(
                            event_type=event_type,
                            timestamp=timestamp,
                            player=match.groupdict().get("player"),
                            message=match.groupdict().get("detail") or match.group(0),
                            source="server_log",
                        )
                    )
                    matched = True
                    break
            if matched:
                continue
            death = DEATH_PATTERN.search(line)
            if death:
                events.append(
                    LogEvent(
                        event_type="player_death",
                        timestamp=timestamp,
                        player=death.group("player"),
                        message=death.group(0),
                        source="server_log",
                    )
                )
            elif "Done (" in line and "For help" in line:
                events.append(
                    LogEvent(
                        event_type="server_start",
                        timestamp=timestamp,
                        message="server startup completed",
                        source="server_log",
                    )
                )
            elif "Stopping server" in line:
                events.append(
                    LogEvent(
                        event_type="server_stop",
                        timestamp=timestamp,
                        message="server stopping",
                        source="server_log",
                    )
                )
        return events[-limit:]

    def get_recent_errors(self, limit: int) -> list[LogError]:
        errors: list[LogError] = []
        categories = (
            ("fatal", ("fatal",)),
            ("watchdog", ("watchdog",)),
            ("crash", ("crash",)),
            ("exception", ("exception", "caused by:")),
            (
                "mod_load_failure",
                ("failed to load mod", "mod resolution encountered", "incompatible mod"),
            ),
            (
                "rcon_failure",
                (
                    "rcon failed",
                    "rcon failure",
                    "rcon listener stopped",
                    "rcon authentication failed",
                    "rcon connection failed",
                ),
            ),
            ("error", ("/error]", "[error]", " error ")),
        )
        for raw_line in self._read_lines():
            line = _sanitize(raw_line, redact_ips=self._config.redact_ip_addresses)
            if CHAT_PATTERN.search(line):
                continue
            folded = line.casefold()
            category = next(
                (
                    name
                    for name, markers in categories
                    if any(marker in folded for marker in markers)
                ),
                None,
            )
            if category:
                errors.append(
                    LogError(
                        timestamp=self._timestamp(line),
                        category=category,
                        message=line,
                    )
                )
        return errors[-limit:]
