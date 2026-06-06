from __future__ import annotations

from click.testing import CliRunner

from {{PACKAGE}}.cli import main


def test_help_exits_cleanly() -> None:
    result = CliRunner().invoke(main, ["--help"])
    assert result.exit_code == 0
    assert result.output.startswith("Usage: main")


def test_hello_greets() -> None:
    result = CliRunner().invoke(main, ["hello"])
    assert result.exit_code == 0
    assert result.output == "Hello from {{DIST_NAME}}!\n"
