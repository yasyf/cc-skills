from __future__ import annotations

import importlib.resources
import sys
from collections.abc import Callable

import pytest

from gd_build import cli

LOADER_HTML = (
    "<html><head>"
    "<script>(function(){var s=document.createElement('script');"
    "s.src='../../color-swatch.js';document.head.appendChild(s);})()</script>"
    "</head></html>"
)


def packaged_titles() -> str:
    return importlib.resources.files("gd_build").joinpath("titles.py").read_text()


@pytest.mark.parametrize(
    ("value", "code"),
    [
        pytest.param(None, 0, id="none-returns-0"),
        pytest.param(0, 0, id="zero-returns-0"),
        pytest.param(1, 1, id="nonzero-int-propagates"),
        pytest.param(2, 2, id="other-int-propagates"),
        pytest.param("boom", 1, id="string-code-is-failure"),
    ],
)
def test_exit_code(value: object, code: int) -> None:
    assert cli.exit_code(value) == code


@pytest.mark.parametrize(
    ("outcomes", "code"),
    [
        pytest.param({"a": True, "b": True}, 0, id="all-applied-exit-0"),
        pytest.param({"a": True, "b": False}, 3, id="one-skipped-exit-3"),
        pytest.param({"a": False}, 3, id="all-skipped-exit-3"),
    ],
)
def test_selftest_exit_codes(monkeypatch: pytest.MonkeyPatch, outcomes: dict[str, bool], code: int) -> None:
    monkeypatch.setattr(cli, "apply_patches", lambda: outcomes)
    monkeypatch.setattr(sys, "argv", ["gd-build", "selftest"])
    with pytest.raises(SystemExit) as exc:
        cli.main()
    assert exc.value.code == code


def test_selftest_none_selects_nothing_exits_0(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GD_BUILD_PATCHES", "none")
    monkeypatch.setattr(sys, "argv", ["gd-build", "selftest"])
    with pytest.raises(SystemExit) as exc:
        cli.main()
    assert exc.value.code == 0


def test_selftest_unknown_name_exits_3(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("GD_BUILD_PATCHES", "bogus")
    monkeypatch.delenv("GITHUB_ACTIONS", raising=False)
    monkeypatch.setattr(sys, "argv", ["gd-build", "selftest"])
    with pytest.raises(SystemExit) as exc:
        cli.main()
    assert exc.value.code == 3
    assert "UNPATCHED: bogus — running STOCK (unknown patch name)" in capsys.readouterr().err


def test_build_materializes_titles(
    tmp_path, monkeypatch: pytest.MonkeyPatch, install_great_docs_cli: Callable[[Callable[[], object]], None]
) -> None:
    install_great_docs_cli(lambda: None)
    monkeypatch.setenv("GD_BUILD_PATCHES", "none")
    monkeypatch.chdir(tmp_path)
    (tmp_path / "great-docs/_site").mkdir(parents=True)
    monkeypatch.setattr(sys, "argv", ["gd-build", "build"])
    with pytest.raises(SystemExit) as exc:
        cli.main()
    assert exc.value.code == 0
    dest = tmp_path / "docs/scripts/.gd-build/native_reference_titles.py"
    assert dest.is_file()
    assert dest.read_text() == packaged_titles()


def test_build_overwrites_stale_titles(
    tmp_path, monkeypatch: pytest.MonkeyPatch, install_great_docs_cli: Callable[[Callable[[], object]], None]
) -> None:
    install_great_docs_cli(lambda: None)
    monkeypatch.setenv("GD_BUILD_PATCHES", "none")
    monkeypatch.chdir(tmp_path)
    (tmp_path / "great-docs/_site").mkdir(parents=True)
    dest = tmp_path / "docs/scripts/.gd-build/native_reference_titles.py"
    dest.parent.mkdir(parents=True)
    dest.write_text("# stale content from a previous run\n")
    monkeypatch.setattr(sys, "argv", ["gd-build", "build"])
    with pytest.raises(SystemExit):
        cli.main()
    assert dest.read_text() == packaged_titles()


def test_build_delegates_argv(
    tmp_path, monkeypatch: pytest.MonkeyPatch, install_great_docs_cli: Callable[[Callable[[], object]], None]
) -> None:
    recorded: dict[str, list[str]] = {}

    def fake_main() -> None:
        recorded["argv"] = list(sys.argv)

    install_great_docs_cli(fake_main)
    monkeypatch.setenv("GD_BUILD_PATCHES", "none")
    monkeypatch.chdir(tmp_path)
    (tmp_path / "great-docs/_site").mkdir(parents=True)
    monkeypatch.setattr(sys, "argv", ["gd-build", "build", "--to", "gh-pages"])
    with pytest.raises(SystemExit) as exc:
        cli.main()
    assert exc.value.code == 0
    assert recorded["argv"] == ["great-docs", "build", "--to", "gh-pages"]


def test_build_success_fixes_swatch(
    tmp_path, monkeypatch: pytest.MonkeyPatch, install_great_docs_cli: Callable[[Callable[[], object]], None]
) -> None:
    install_great_docs_cli(lambda: None)
    monkeypatch.setenv("GD_BUILD_PATCHES", "none")
    monkeypatch.chdir(tmp_path)
    site = tmp_path / "great-docs/_site"
    site.mkdir(parents=True)
    (site / "index.html").write_text(LOADER_HTML)
    monkeypatch.setattr(sys, "argv", ["gd-build", "build"])
    with pytest.raises(SystemExit) as exc:
        cli.main()
    assert exc.value.code == 0
    rendered = (site / "index.html").read_text()
    assert rendered == '<html><head><script src="color-swatch.js"></script></head></html>'


def test_build_failure_skips_swatch_and_propagates(
    tmp_path, monkeypatch: pytest.MonkeyPatch, install_great_docs_cli: Callable[[Callable[[], object]], None]
) -> None:
    def fake_main() -> None:
        raise SystemExit(1)

    install_great_docs_cli(fake_main)
    monkeypatch.setenv("GD_BUILD_PATCHES", "none")
    monkeypatch.chdir(tmp_path)
    site = tmp_path / "great-docs/_site"
    site.mkdir(parents=True)
    (site / "index.html").write_text(LOADER_HTML)
    monkeypatch.setattr(sys, "argv", ["gd-build", "build"])
    with pytest.raises(SystemExit) as exc:
        cli.main()
    assert exc.value.code == 1
    assert (site / "index.html").read_text() == LOADER_HTML
    assert (tmp_path / "docs/scripts/.gd-build/native_reference_titles.py").is_file()


def test_build_failure_string_code_prints_message(
    tmp_path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
    install_great_docs_cli: Callable[[Callable[[], object]], None],
) -> None:
    def fake_main() -> None:
        raise SystemExit("ERROR: Configured reference item(s) not found in `pkg`: `Gone`")

    install_great_docs_cli(fake_main)
    monkeypatch.setenv("GD_BUILD_PATCHES", "none")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(sys, "argv", ["gd-build", "build"])
    with pytest.raises(SystemExit) as exc:
        cli.main()
    assert exc.value.code == 1
    assert "ERROR: Configured reference item(s) not found in `pkg`: `Gone`" in capsys.readouterr().err


@pytest.mark.parametrize(
    "argv",
    [
        pytest.param(["gd-build"], id="no-subcommand"),
        pytest.param(["gd-build", "frobnicate"], id="unknown-subcommand"),
        pytest.param(["gd-build", "selftest", "extra"], id="selftest-with-args"),
    ],
)
def test_usage_exit_2(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], argv: list[str]
) -> None:
    monkeypatch.setattr(sys, "argv", argv)
    with pytest.raises(SystemExit) as exc:
        cli.main()
    assert exc.value.code == 2
    assert "usage:" in capsys.readouterr().err
