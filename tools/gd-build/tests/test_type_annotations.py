"""Tests for the type cross-link + modern-union annotation patch.

These build genuine `griffe` annotation expressions and assert against real
great-docs render internals, so they need the (test-only) great-docs dependency.
"""

from __future__ import annotations

import types
from collections.abc import Callable, Iterator

import griffe
import pytest

from gd_build import patches as patches_mod

SOURCE = '''
from __future__ import annotations
import typing
from typing import Optional, Union, Literal


class CommandLineQuery: ...
class Command: ...


x_attr: Command
x_opt: Optional[Command]


def f(
    a: CommandLineQuery,
    b: Optional[Command],
    c: typing.Optional[Command],
    d: Union[Command, CommandLineQuery],
    e: typing.Union[Command, None],
    g: tuple[Command, ...],
    h: list[Optional[Command]],
    i: int,
    j: str | None,
    k: dict[str, Command],
    l: Literal["x", "y"],
    m2: Command | None,
    n: typing.Any,
    o: dict[str, Optional[list[Command]]],
) -> Optional[Command]: ...
'''

DOCUMENTED = {"m.Command", "m.CommandLineQuery"}


@pytest.fixture(scope="module")
def module() -> griffe.Module:
    return griffe.visit("m", filepath="m.py", code=SOURCE)


@pytest.fixture(scope="module")
def annotations(module: griffe.Module) -> dict[str, object]:
    fn = module["f"]
    anns = {p.name: p.annotation for p in fn.parameters if p.name != "self"}
    anns["return"] = fn.returns
    return anns


def _fmt(ann: object, mode: str) -> str:
    return patches_mod.format_annotation(ann, documented=DOCUMENTED, mode=mode)


@pytest.mark.parametrize(
    ("param", "annotation", "signature"),
    [
        ("a", "[](`~m.CommandLineQuery`)", "CommandLineQuery"),
        ("b", "[](`~m.Command`) | None", "Command | None"),
        ("c", "[](`~m.Command`) | None", "Command | None"),
        ("d", "[](`~m.Command`) | [](`~m.CommandLineQuery`)", "Command | CommandLineQuery"),
        ("e", "[](`~m.Command`) | None", "Command | None"),
        ("g", "tuple[[](`~m.Command`), ...]", "tuple[Command, ...]"),
        ("h", "list[[](`~m.Command`) | None]", "list[Command | None]"),
        ("i", "int", "int"),
        ("j", "str | None", "str | None"),
        ("k", "dict[str, [](`~m.Command`)]", "dict[str, Command]"),
        ("m2", "[](`~m.Command`) | None", "Command | None"),
        ("n", "Any", "Any"),
        ("o", "dict[str, list[[](`~m.Command`)] | None]", "dict[str, list[Command] | None]"),
        ("return", "[](`~m.Command`) | None", "Command | None"),
    ],
)
def test_format_annotation_modernize_and_linkify(
    annotations: dict[str, object], param: str, annotation: str, signature: str
) -> None:
    ann = annotations[param]
    assert _fmt(ann, "annotation") == annotation
    assert _fmt(ann, "signature") == signature


def test_builtins_and_externals_are_not_linkified(annotations: dict[str, object]) -> None:
    for param in ("i", "j", "k"):
        assert "](`~" not in _fmt(annotations[param], "signature")
    # str / int / dict / tuple / list are never wrapped as interlinks
    assert "](`~" not in _fmt(annotations["i"], "annotation")


def test_literal_values_pass_through_untouched(annotations: dict[str, object]) -> None:
    # Non-inventory Literal string values are rendered verbatim (signature keeps
    # the raw source quotes; no interlink is emitted in either mode).
    assert _fmt(annotations["l"], "signature") == "Literal['x', 'y']"
    assert "](`~" not in _fmt(annotations["l"], "annotation")


def test_annotation_mode_skips_links_for_undocumented(annotations: dict[str, object]) -> None:
    empty = patches_mod.format_annotation(
        annotations["b"], documented=set(), mode="annotation"
    )
    # With nothing documented, Optional still modernizes but the type stays plain.
    assert empty == "Command | None"


def test_initvar_is_unwrapped() -> None:
    mod = griffe.visit(
        "m",
        filepath="m.py",
        code="from dataclasses import InitVar\nclass Command: ...\nx: InitVar[Command]\n",
    )
    ann = mod["x"].annotation
    assert _fmt(ann, "annotation") == "[](`~m.Command`)"
    assert _fmt(ann, "signature") == "Command"


# ── patched-method integration ────────────────────────────────────────────────


def _restore_patch_targets() -> Callable[[], None]:
    """Snapshot the three rebind points so a test can fully undo apply_type_annotations."""
    from great_docs._apiref import collect
    from great_docs._apiref._render import doc as doc_mod
    from great_docs._apiref._render import mixin_call

    saved_bm = collect.build_manifest
    had_ra = "render_annotation" in doc_mod.RenderDoc.__dict__
    had_rsp = "render_signature_parameter" in mixin_call.RenderDocCallMixin.__dict__
    saved_documented = set(patches_mod.DOCUMENTED_NAMES)

    def restore() -> None:
        collect.build_manifest = saved_bm
        if not had_ra:
            del doc_mod.RenderDoc.render_annotation
        if not had_rsp:
            del mixin_call.RenderDocCallMixin.render_signature_parameter
        patches_mod.DOCUMENTED_NAMES.clear()
        patches_mod.DOCUMENTED_NAMES.update(saved_documented)

    return restore


@pytest.fixture
def applied() -> Iterator[None]:
    restore = _restore_patch_targets()
    patches_mod.DOCUMENTED_NAMES.clear()
    patches_mod.DOCUMENTED_NAMES.update(DOCUMENTED)
    patches_mod.apply_type_annotations()
    try:
        yield
    finally:
        restore()


def test_build_manifest_wrapper_captures_documented_names() -> None:
    from great_docs._apiref import collect
    from great_docs._apiref.inventory import InventoryItem

    restore = _restore_patch_targets()
    manifest = types.SimpleNamespace(
        pages=[],
        items=[
            InventoryItem(obj=None, name="pkg.Command", uri="reference/Command.html"),
            InventoryItem(obj=None, name="", uri="reference/blank.html"),
        ],
    )
    collect.build_manifest = lambda *a, **k: manifest
    try:
        patches_mod.DOCUMENTED_NAMES.clear()
        patches_mod.apply_type_annotations()
        out = collect.build_manifest(["sections"], dir="reference")
        assert out is manifest
        assert patches_mod.DOCUMENTED_NAMES == {"pkg.Command"}
    finally:
        restore()


def test_render_annotation_patch_linkifies_attribute(
    applied: None, module: griffe.Module
) -> None:
    from great_docs._apiref._render import doc as doc_mod

    fake_self = types.SimpleNamespace(obj=module["x_attr"])
    out = doc_mod.RenderDoc.render_annotation(fake_self)
    assert out == "[](`~m.Command`)"


def test_render_annotation_patch_modernizes_optional_attribute(
    applied: None, module: griffe.Module
) -> None:
    from great_docs._apiref._render import doc as doc_mod

    fake_self = types.SimpleNamespace(obj=module["x_opt"])
    out = doc_mod.RenderDoc.render_annotation(fake_self)
    assert out == "[](`~m.Command`) | None"


def test_render_annotation_patch_falls_back_on_error(
    applied: None, module: griffe.Module, monkeypatch: pytest.MonkeyPatch
) -> None:
    from great_docs._apiref._render import doc as doc_mod

    def boom(*args: object, **kwargs: object) -> str:
        raise RuntimeError("boom")

    monkeypatch.setattr(patches_mod, "format_annotation", boom)
    fake_self = types.SimpleNamespace(obj=module["x_attr"])
    out = doc_mod.RenderDoc.render_annotation(fake_self)
    # Stock render: plain type text, never an interlink.
    assert "](`~" not in out
    assert "Command" in out


def test_render_annotation_patch_rejects_non_attribute(applied: None) -> None:
    from great_docs._apiref._render import doc as doc_mod

    fake_self = types.SimpleNamespace(obj="not-an-attribute")
    with pytest.raises(TypeError):
        doc_mod.RenderDoc.render_annotation(fake_self)


def _param(module: griffe.Module, name: str) -> object:
    return next(p for p in module["f"].parameters if p.name == name)


def test_render_signature_parameter_modernizes_without_links(
    applied: None, module: griffe.Module
) -> None:
    from great_docs._apiref._render import mixin_call

    fake_self = types.SimpleNamespace(show_signature_annotation=True)
    out = mixin_call.RenderDocCallMixin.render_signature_parameter(
        fake_self, _param(module, "b")
    )
    assert out == "b: Command | None"
    assert "](`~" not in out


def test_render_signature_parameter_leaves_unannotated_alone(
    applied: None, module: griffe.Module
) -> None:
    from great_docs._apiref._render import mixin_call

    mod = griffe.visit("m", filepath="m.py", code="def g(a): ...\n")
    param = next(p for p in mod["g"].parameters if p.name == "a")
    fake_self = types.SimpleNamespace(show_signature_annotation=True)
    out = mixin_call.RenderDocCallMixin.render_signature_parameter(fake_self, param)
    assert out == "a"


def test_render_signature_parameter_falls_back_on_error(
    applied: None, module: griffe.Module, monkeypatch: pytest.MonkeyPatch
) -> None:
    from great_docs._apiref._render import mixin_call

    def boom(*args: object, **kwargs: object) -> str:
        raise RuntimeError("boom")

    monkeypatch.setattr(patches_mod, "format_annotation", boom)
    fake_self = types.SimpleNamespace(show_signature_annotation=True)
    out = mixin_call.RenderDocCallMixin.render_signature_parameter(
        fake_self, _param(module, "b")
    )
    # Stock signature keeps the original Optional[...] source text.
    assert out == "b: Optional[Command]"


# ── probe / self-retire ───────────────────────────────────────────────────────


def test_probe_type_annotations_gate_open() -> None:
    assert patches_mod.probe_type_annotations() is None


def test_probe_type_annotations_version_below_window(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(patches_mod, "version", lambda dist: "0.14.9")
    assert (
        patches_mod.probe_type_annotations()
        == "great-docs 0.14.9 is outside [0.15, 0.16)"
    )


def test_probe_type_annotations_render_shape_changed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from great_docs._apiref._render import doc as doc_mod

    def stub_render_annotation(self: object, annotation: object = None) -> str:
        return "unrecognized"

    monkeypatch.setattr(
        doc_mod.RenderDoc, "render_annotation", stub_render_annotation, raising=True
    )
    assert (
        patches_mod.probe_type_annotations()
        == "RenderDoc.render_annotation shape changed"
    )


def test_probe_type_annotations_interlink_contract_changed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from great_docs._apiref.pandoc import inlines

    class BrokenInterLink:
        def __init__(self, *args: object, **kwargs: object) -> None: ...

        def __str__(self) -> str:
            return "not-an-interlink"

    monkeypatch.setattr(inlines, "InterLink", BrokenInterLink)
    assert (
        patches_mod.probe_type_annotations()
        == "InterLink interlink markup shape changed"
    )
