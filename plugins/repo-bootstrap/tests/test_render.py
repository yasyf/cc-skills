"""Pure rendering: conditional sections, variable substitution, leftover scans."""

from __future__ import annotations

from bootstrap import render


def test_block_section_kept_when_enabled():
    text = "a\n{{#FEATURE_DOCS}}\ndocs line\n{{/FEATURE_DOCS}}\nb\n"
    assert render.render_sections(text, frozenset({"FEATURE_DOCS"})) == "a\ndocs line\nb\n"


def test_block_section_dropped_when_disabled():
    text = "a\n{{#FEATURE_DOCS}}\ndocs line\n{{/FEATURE_DOCS}}\nb\n"
    assert render.render_sections(text, frozenset()) == "a\nb\n"


def test_inverted_block_kept_when_disabled():
    text = "a\n{{^FEATURE_PYPI}}\nno pypi\n{{/FEATURE_PYPI}}\nb\n"
    assert render.render_sections(text, frozenset()) == "a\nno pypi\nb\n"
    assert render.render_sections(text, frozenset({"FEATURE_PYPI"})) == "a\nb\n"


def test_inline_two_branch():
    text = "pip install {{#FEATURE_PYPI}}demo{{/FEATURE_PYPI}}{{^FEATURE_PYPI}}./local{{/FEATURE_PYPI}}"
    assert render.render_sections(text, frozenset({"FEATURE_PYPI"})) == "pip install demo"
    assert render.render_sections(text, frozenset()) == "pip install ./local"


def test_nested_sections_resolve_to_fixpoint():
    text = (
        "{{#FEATURE_DOCS}}\n"
        "outer\n"
        "{{#FEATURE_PYPI}}\n"
        "inner\n"
        "{{/FEATURE_PYPI}}\n"
        "{{/FEATURE_DOCS}}\n"
    )
    assert render.render_sections(text, frozenset({"FEATURE_DOCS", "FEATURE_PYPI"})) == "outer\ninner\n"
    assert render.render_sections(text, frozenset({"FEATURE_DOCS"})) == "outer\n"
    assert render.render_sections(text, frozenset()) == ""


def test_substitute_vars():
    assert render.substitute_vars("{{A}}-{{B}}", {"A": "x", "B": "y"}) == "x-y"


def test_render_composes_sections_then_vars():
    text = "{{#FEATURE_DOCS}}docs for {{PROJECT_NAME}}{{/FEATURE_DOCS}}"
    assert render.render(text, {"PROJECT_NAME": "demo"}, frozenset({"FEATURE_DOCS"})) == "docs for demo"


def test_leftover_scanners():
    assert render.find_unrendered_sections("ok {{#X}} body") == ["{{#X}}"]
    assert render.find_unrendered_placeholders("hi {{NAME}} {{AGE}}") == ["{{AGE}}", "{{NAME}}"]
    assert render.find_unrendered_placeholders("clean text") == []
