from minecraft_admin_mcp.adapters.rcon import RconAdapter
from minecraft_admin_mcp.config import RconConfig


def test_fixed_commands_and_parsing() -> None:
    calls: list[tuple[str, ...]] = []

    def runner(command: str, *arguments: str) -> str:
        calls.append((command, *arguments))
        if command == "list":
            return "There are 2 of a max of 20 players online: Steve, Alex"
        if command == "whitelist" and arguments == ("list",):
            return "There are 2 whitelisted players: Steve, Alex"
        return "OK"

    adapter = RconAdapter(RconConfig(host="minecraft"), "secret", runner)
    assert adapter.list_players().players == ["Steve", "Alex"]
    assert adapter.get_whitelist() == ["Steve", "Alex"]
    adapter.broadcast("hello")
    adapter.whitelist_add("Steve")
    adapter.whitelist_remove("Steve")
    adapter.kick_player("Steve", "reason")
    adapter.save_all_flush()
    assert calls == [
        ("list",),
        ("whitelist", "list"),
        ("say", "hello"),
        ("whitelist", "add", "Steve"),
        ("whitelist", "remove", "Steve"),
        ("kick", "Steve", "reason"),
        ("save-all", "flush"),
    ]
    assert not hasattr(adapter, "execute")
    assert not hasattr(adapter, "run")
    assert not hasattr(adapter, "send_raw")


def test_whitelist_parses_vanilla_player_parentheses() -> None:
    adapter = RconAdapter(
        RconConfig(host="minecraft"),
        "secret",
        lambda command, *arguments: "There are 1 whitelisted player(s): Notch",
    )
    assert adapter.get_whitelist() == ["Notch"]


def test_failed_command_response_is_not_reported_as_success() -> None:
    adapter = RconAdapter(
        RconConfig(host="minecraft"),
        "secret",
        lambda command, *arguments: "That player does not exist",
    )
    assert adapter.whitelist_add("UnknownPlayer").success is False


def test_status_reports_unreachable_without_guessing_metrics() -> None:
    def unreachable(command: str, *arguments: str) -> str:
        raise TimeoutError

    adapter = RconAdapter(RconConfig(host="minecraft"), "secret", unreachable)
    status = adapter.get_status()
    assert status.minecraft_reachable is False
    assert status.rcon_reachable is False
    assert status.online_players is None
