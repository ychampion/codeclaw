import pytest

from codeclaw.tui.commands import CommandRegistry
from codeclaw.tui.types import CommandResult, SlashCommand


def _noop_handler(_ctx, _args):
    return CommandResult(ok=True, message="ok")


def test_registry_register_parse_and_alias():
    registry = CommandRegistry()
    registry.register(
        SlashCommand(
            name="help",
            aliases=("h", "?"),
            help_text="show help",
            usage="/help",
            handler=_noop_handler,
        )
    )

    parsed = registry.parse("/help")
    assert parsed is not None
    assert parsed.command_name == "help"
    assert parsed.args == []

    parsed_alias = registry.parse("/h arg1")
    assert parsed_alias is not None
    assert parsed_alias.command_name == "h"
    assert parsed_alias.args == ["arg1"]

    command = registry.get(parsed_alias.command_name)
    assert command is not None
    assert command.name == "help"


def test_registry_completion_for_slash_commands():
    registry = CommandRegistry()
    registry.register(SlashCommand(name="help", aliases=(), help_text="h", handler=_noop_handler))
    registry.register(SlashCommand(name="hello", aliases=(), help_text="h", handler=_noop_handler))
    registry.register(SlashCommand(name="watch", aliases=(), help_text="w", handler=_noop_handler))

    assert registry.completions("/he") == ["/hello", "/help"]
    assert registry.completions("he") == []
    assert registry.completions("/watch on") == []


def test_registry_parse_errors():
    registry = CommandRegistry()
    with pytest.raises(ValueError):
        registry.parse("/")

    with pytest.raises(ValueError):
        registry.parse('/help "unterminated')

