"""The heads finder.py reads for desk-runner.py, and its exec of `lr grade finder`; the pass itself is tested in lr/internal/grading/grader."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import finder
import pytest

REPO = "Forge-AI/monorepo"
HEAD = "7414944e30bfec89dee45328414dbc1016e35afa"
OLD = "a269784e49009c5aa92b2341e08d6945f7ff8e95"
SCRIPTS = Path(finder.__file__).resolve().parent


def test_a_head_record_names_its_commit_field_else_its_first_full_sha_no_was_precedes():
    named = {"refs": {"prs": [f"{REPO}#43299"]}}
    assert finder.heads(named | {"text": f"HEAD #43299 {HEAD} (was {OLD})", "fields": {"commit": HEAD}}, REPO) == [("43299", HEAD)]
    assert finder.heads({"refs": {"prs": [43299]}, "text": f"#43299 HEAD (was {OLD}), now {HEAD}", "fields": {}}, REPO) == [("43299", HEAD)]
    assert finder.heads(named | {"text": "#43299 HEAD 7414944e30bf (was a269784e4900)"}, REPO) == [("43299", "7414944e30bf")]
    assert finder.heads(named | {"text": "#43299 HEAD restacked onto 4329900 (was a269784e4900)"}, REPO) == []
    assert finder.heads({"refs": {"prs": ["yasyf/cc-skills#836"]}, "text": f"dashboard-tap-fast {HEAD}"}, REPO) == []


@pytest.mark.parametrize(
    ("refs", "text", "expected"),
    [
        ({}, f"dar-p5-api-cred-503 {HEAD} #43832 (was 814e3cd): test-only review fix", [("43832", HEAD)]),
        ({"refs": {}}, f"finder pass wanted at {HEAD} pr:{REPO}#43923 after #43900 landed", [("43923", HEAD)]),
        ({}, f"{HEAD} yasyf/cc-skills#836 then #43929", [("43929", HEAD)]),
        ({"refs": {"prs": ["yasyf/cc-skills#836"]}}, f"{HEAD} #43929", []),
        ({}, f"HEAD {HEAD} restacked onto dev", []),
    ],
)
def test_a_head_record_without_pr_refs_takes_the_pr_its_text_names(refs, text, expected):
    assert finder.heads(refs | {"text": text}, REPO) == expected


def test_a_restack_posted_in_prose_names_each_prs_own_head():
    """Coordinator #304381: n-enumeration-12's 7:54 PM restack #304136 named nine heads in prose, every PR read dev's 1eb90d3 as its head, and #45638's pass at its old head ran on until 8:03 PM."""
    record = {
        "refs": {"prs": [45492, 45493, 45638, 46720]},
        "fields": {},
        "text": "7:54 PM HEAD n-enum chain restacked onto dev 1eb90d3: #45492 1dcb6e6, #45493 c63add9, #45638 d740b78, #46720 483a700. Clears desk-runner 301062 (#45493 conflicting with dev)",
    }
    assert finder.heads(record, REPO) == [("45492", "1dcb6e6"), ("45493", "c63add9"), ("45638", "d740b78"), ("46720", "483a700")]
    assert finder.heads({"refs": {"prs": [47525]}, "text": "9:02 PM HEAD #47525 bfd51e7 over 46f153f: removes the heap return"}, REPO) == [("47525", "bfd51e7")]


@pytest.mark.parametrize("tool", ["finder", "fidelity"])
def test_a_grader_script_execs_the_lr_verb_of_its_own_plugin(tmp_path, tool):
    scripts = tmp_path / "skills" / "long-running" / "scripts"
    scripts.mkdir(parents=True)
    copy = scripts / f"{tool}.py"
    copy.write_text((SCRIPTS / f"{tool}.py").read_text())
    lr = tmp_path / "bin" / "lr"
    lr.parent.mkdir()
    lr.write_text('#!/bin/sh\nprintf "%s\\n" "$@"\n')
    lr.chmod(0o755)

    done = subprocess.run([sys.executable, str(copy), "/jobs/one"], capture_output=True, text=True, env={**os.environ, "PYTHONPATH": str(SCRIPTS)}, check=True)

    assert done.stdout.splitlines() == ["grade", tool, "/jobs/one"]
