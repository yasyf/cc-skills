from __future__ import annotations

import sys
import types
from collections.abc import Callable

import pytest

from gd_build import patches as patches_mod


def make_patch(probe: Callable[[], str | None], apply: Callable[[], None]) -> patches_mod.Patch:
    return patches_mod.Patch(
        name="fake",
        verified_window="",
        probe=probe,
        apply=apply,
        expected_savings="",
        upstream_ref="",
    )


def install_introspect(
    monkeypatch: pytest.MonkeyPatch, *, make_loader: bool = True, loader_param: bool = True
) -> types.ModuleType:
    introspect = types.ModuleType("great_docs._apiref.introspect")
    if make_loader:
        introspect.make_loader = lambda parser: f"loader:{parser}"
    if loader_param:

        def get_object(path: object, parser: object, loader: object = None) -> dict[str, object]:
            return {"path": path, "parser": parser, "loader": loader}
    else:

        def get_object(path: object, parser: object) -> dict[str, object]:  # type: ignore[misc]
            return {"path": path, "parser": parser}

    introspect.get_object = get_object
    apiref = types.ModuleType("great_docs._apiref")
    apiref.introspect = introspect
    great_docs = types.ModuleType("great_docs")
    great_docs._apiref = apiref
    monkeypatch.setitem(sys.modules, "great_docs", great_docs)
    monkeypatch.setitem(sys.modules, "great_docs._apiref", apiref)
    monkeypatch.setitem(sys.modules, "great_docs._apiref.introspect", introspect)
    return introspect


def install_git(
    monkeypatch: pytest.MonkeyPatch, *, has_gitinfo: bool = True, classmethod_fp: bool = True
) -> types.ModuleType:
    git = types.ModuleType("griffe._internal.git")
    if has_gitinfo:

        class GitInfo:
            calls: list[object] = []

            @classmethod
            def from_package(cls, package: object) -> str:
                cls.calls.append(package)
                return f"info:{package}"

        if not classmethod_fp:
            GitInfo.from_package = staticmethod(lambda package: f"info:{package}")
        git.GitInfo = GitInfo
    internal = types.ModuleType("griffe._internal")
    internal.git = git
    griffe = types.ModuleType("griffe")
    griffe._internal = internal
    monkeypatch.setitem(sys.modules, "griffe", griffe)
    monkeypatch.setitem(sys.modules, "griffe._internal", internal)
    monkeypatch.setitem(sys.modules, "griffe._internal.git", git)
    return git


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        pytest.param(None, [("shared-griffe-loader", True), ("griffe-gitinfo-cache", True)], id="unset-defaults-to-all"),
        pytest.param("all", [("shared-griffe-loader", True), ("griffe-gitinfo-cache", True)], id="all"),
        pytest.param("none", [], id="none-selects-nothing"),
        pytest.param("shared-griffe-loader", [("shared-griffe-loader", True)], id="single-csv"),
        pytest.param(
            "griffe-gitinfo-cache,shared-griffe-loader",
            [("griffe-gitinfo-cache", True), ("shared-griffe-loader", True)],
            id="csv-order-preserved",
        ),
        pytest.param("bogus", [("bogus", False)], id="unknown-name-maps-to-none"),
        pytest.param(
            "  shared-griffe-loader , bogus ",
            [("shared-griffe-loader", True), ("bogus", False)],
            id="whitespace-trimmed",
        ),
        pytest.param(
            "shared-griffe-loader,,griffe-gitinfo-cache",
            [("shared-griffe-loader", True), ("griffe-gitinfo-cache", True)],
            id="empty-parts-filtered",
        ),
    ],
)
def test_selected_patches_parsing(
    monkeypatch: pytest.MonkeyPatch, value: str | None, expected: list[tuple[str, bool]]
) -> None:
    if value is None:
        monkeypatch.delenv("GD_BUILD_PATCHES", raising=False)
    else:
        monkeypatch.setenv("GD_BUILD_PATCHES", value)
    assert [(name, patch is not None) for patch, name in patches_mod.selected_patches()] == expected


def test_skip_reason_unknown_patch() -> None:
    assert patches_mod.skip_reason(None) == "unknown patch name"


def test_skip_reason_probe_returns_reason_and_skips_apply() -> None:
    applied: list[str] = []
    reason = patches_mod.skip_reason(make_patch(lambda: "gate closed", lambda: applied.append("apply")))
    assert reason == "gate closed"
    assert applied == []


def test_skip_reason_probe_raises_is_isolated() -> None:
    def probe() -> str | None:
        raise RuntimeError("boom")

    assert patches_mod.skip_reason(make_patch(probe, lambda: None)) == "RuntimeError: boom"


def test_skip_reason_apply_raises_is_isolated() -> None:
    def apply() -> None:
        raise ValueError("nope")

    assert patches_mod.skip_reason(make_patch(lambda: None, apply)) == "ValueError: nope"


def test_skip_reason_success_returns_none_and_applies() -> None:
    applied: list[bool] = []
    reason = patches_mod.skip_reason(make_patch(lambda: None, lambda: applied.append(True)))
    assert reason is None
    assert applied == [True]


def test_emit_patched(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    monkeypatch.delenv("GITHUB_ACTIONS", raising=False)
    patches_mod.emit("shared-griffe-loader", None)
    out = capsys.readouterr()
    assert out.err == "PATCHED: shared-griffe-loader\n"
    assert out.out == ""


def test_emit_unpatched_without_github_actions(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.delenv("GITHUB_ACTIONS", raising=False)
    patches_mod.emit("griffe-gitinfo-cache", "griffe.GitInfo missing")
    out = capsys.readouterr()
    assert out.err == "UNPATCHED: griffe-gitinfo-cache — running STOCK (griffe.GitInfo missing)\n"
    assert out.out == ""


def test_emit_unpatched_under_github_actions(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    patches_mod.emit("griffe-gitinfo-cache", "griffe.GitInfo missing")
    out = capsys.readouterr()
    line = "UNPATCHED: griffe-gitinfo-cache — running STOCK (griffe.GitInfo missing)"
    assert out.err == f"{line}\n"
    assert out.out == f"::warning::{line}\n"


def test_probe_shared_loader_gate_open(monkeypatch: pytest.MonkeyPatch) -> None:
    install_introspect(monkeypatch)
    monkeypatch.setattr(patches_mod, "version", lambda dist: "0.15.0")
    assert patches_mod.probe_shared_loader() is None


def test_probe_shared_loader_version_below_window(monkeypatch: pytest.MonkeyPatch) -> None:
    install_introspect(monkeypatch)
    monkeypatch.setattr(patches_mod, "version", lambda dist: "0.14.1")
    assert patches_mod.probe_shared_loader() == "great-docs 0.14.1 is outside [0.15, 0.16)"


def test_probe_shared_loader_missing_make_loader(monkeypatch: pytest.MonkeyPatch) -> None:
    install_introspect(monkeypatch, make_loader=False)
    monkeypatch.setattr(patches_mod, "version", lambda dist: "0.15.2")
    assert patches_mod.probe_shared_loader() == "introspect.make_loader missing (upstream fix absent)"


def test_probe_shared_loader_no_loader_param(monkeypatch: pytest.MonkeyPatch) -> None:
    install_introspect(monkeypatch, loader_param=False)
    monkeypatch.setattr(patches_mod, "version", lambda dist: "0.15.0")
    assert patches_mod.probe_shared_loader() == "introspect.get_object has no loader parameter"


def test_apply_shared_loader_shares_loader_per_parser(monkeypatch: pytest.MonkeyPatch) -> None:
    built: list[object] = []
    introspect = install_introspect(monkeypatch)
    introspect.make_loader = lambda parser: built.append(parser) or f"loader:{parser}"
    monkeypatch.setattr(patches_mod, "version", lambda dist: "0.15.0")
    patches_mod.apply_shared_loader()
    first = introspect.get_object("a.py", "python")
    second = introspect.get_object("b.py", "python")
    assert first["loader"] == "loader:python"
    assert second["loader"] == "loader:python"
    assert built == ["python"]


def test_probe_gitinfo_cache_gate_open(monkeypatch: pytest.MonkeyPatch) -> None:
    install_git(monkeypatch)
    assert patches_mod.probe_gitinfo_cache() is None


def test_probe_gitinfo_cache_missing_gitinfo(monkeypatch: pytest.MonkeyPatch) -> None:
    install_git(monkeypatch, has_gitinfo=False)
    assert patches_mod.probe_gitinfo_cache() == "griffe.GitInfo missing"


def test_probe_gitinfo_cache_not_classmethod(monkeypatch: pytest.MonkeyPatch) -> None:
    install_git(monkeypatch, classmethod_fp=False)
    assert (
        patches_mod.probe_gitinfo_cache()
        == "griffe.GitInfo.from_package is not a classmethod (layout changed)"
    )


def test_apply_gitinfo_cache_memoizes_per_package(monkeypatch: pytest.MonkeyPatch) -> None:
    git = install_git(monkeypatch)
    patches_mod.apply_gitinfo_cache()
    assert git.GitInfo.from_package("pkgA") == "info:pkgA"
    assert git.GitInfo.from_package("pkgA") == "info:pkgA"
    assert git.GitInfo.from_package("pkgB") == "info:pkgB"
    assert git.GitInfo.calls == ["pkgA", "pkgB"]


def test_apply_patches_none_selects_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GD_BUILD_PATCHES", "none")
    assert patches_mod.apply_patches() == {}


def test_apply_patches_reports_outcomes(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.delenv("GITHUB_ACTIONS", raising=False)
    monkeypatch.setenv("GD_BUILD_PATCHES", "griffe-gitinfo-cache,bogus")
    install_git(monkeypatch)
    outcomes = patches_mod.apply_patches()
    assert outcomes == {"griffe-gitinfo-cache": True, "bogus": False}
    err = capsys.readouterr().err
    assert "PATCHED: griffe-gitinfo-cache\n" in err
    assert "UNPATCHED: bogus — running STOCK (unknown patch name)\n" in err
