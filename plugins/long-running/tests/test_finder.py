"""The head records and asks finder.py reads for desk-runner.py, and its exec of `lr grade finder`; the pass itself is tested in lr/internal/grading/grader."""

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
NEWER = "5be7c0de5be7c0de5be7c0de5be7c0de5be7c0de"
MOVED = "8a6c4e2f0b9d7a5c3e1f8b6d4a2c0e9f7b5d3a1c"
SCRIPTS = Path(finder.__file__).resolve().parent


@pytest.mark.parametrize(
    ("kind", "to", "text", "prs"),
    [
        ("ask", ["finder-desk", "p8b-step10"], "finder-desk: finder: whole on #44114 over its own commits", ["44114"]),
        ("ask", ["finder-desk"], "Finder: whole #44114, please", ["44114"]),
        ("ask", ["finder-desk"], "finder: whole on #46075 decdc34, #46155 58c7d42, #45799 5066a43, #46075 decdc34", ["46075", "46155", "45799"]),
        ("ask", ["finder-desk"], "finder-desk: run #44114 at 7d13a98 again", []),
        ("ask", ["finder-desk"], "finder-desk: one whole-diff pass on #44114", []),
        ("decide", ["finder-desk"], "re #182756: the carried major on #44764 8b4019d is settled", []),
        ("decide", ["finder-desk"], "finder: whole #44114", []),
        ("defect", ["finder-desk", "fidelity-pipeline"], "re #288330 Grader defect: the finder reads a re-timed test as added under e20e623 on #47267, #47274 and #47281. finder-desk: regrade #47267 and #47274 at their current heads with git diff -w; fidelity-pipeline: regrade #47275", ["47267", "47274"]),
        ("decide", ["finder-desk"], "finder-desk: re-grade #44114 at 7d13a98", ["44114"]),
        ("ask", ["p8b-step10"], "finder: whole #44114", []),
        ("ask", ["finder-desk"], f"finder: whole pr:{REPO}#77 after yasyf/cc-skills#9940", ["77"]),
        ("ask", ["finder-desk"], "finder: whole yasyf/cc-skills#9940", []),
    ],
)
def test_an_ask_saying_finder_whole_or_a_root_regrade_order_requests_a_full_pass(kind, to, text, prs):
    assert finder.requested({"kind": kind, "lane": "root", "to": to, "text": text, "refs": {}}, REPO) == prs


def test_a_regrade_order_from_a_lane_other_than_root_requests_nothing():
    assert finder.requested({"kind": "decide", "lane": "speed-ts-1", "to": ["finder-desk"], "text": "finder-desk: regrade #47267", "refs": {}}, REPO) == []


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


def test_since_names_the_full_head_the_newest_finder_record_at_another_head_read():
    older = {"seq": 10, "lane": "p8a-step9", "head": "cfe6544", "text": "finder: ok cfe6544"}
    newest = {"seq": 12, "lane": "p8a-step9-2", "head": "a269784", "text": "finder: major a269784 1 open: walk.go:9"}
    current = {"seq": 14, "lane": "p8a-step9", "head": HEAD[:7], "text": f"finder: ok {HEAD[:7]}"}
    records = [{"kind": "head", "text": f"HEAD #43299 {OLD} (was cfe6544f8d03)", "fields": {}}]
    assert finder.since([older, newest, current], records, HEAD, REPO) == (OLD, {"seq": 12, "lane": "p8a-step9-2", "head": "a269784", "text": "finder: major a269784 1 open: walk.go:9"})
    assert finder.since([newest], [], HEAD, REPO)[0] == ""
    assert finder.since([current], records, HEAD, REPO) == ("", None)


def test_a_restack_posted_in_prose_names_each_prs_own_head():
    """Root #304381: n-enumeration-12's 7:54 PM restack #304136 named nine heads in prose, every PR read dev's 1eb90d3 as its head, and #45638's pass at its old head ran on until 8:03 PM."""
    record = {
        "refs": {"prs": [45492, 45493, 45638, 46720]},
        "fields": {},
        "text": "7:54 PM HEAD n-enum chain restacked onto dev 1eb90d3: #45492 1dcb6e6, #45493 c63add9, #45638 d740b78, #46720 483a700. Clears desk-runner 301062 (#45493 conflicting with dev)",
    }
    assert finder.heads(record, REPO) == [("45492", "1dcb6e6"), ("45493", "c63add9"), ("45638", "d740b78"), ("46720", "483a700")]
    assert finder.heads({"refs": {"prs": [47525]}, "text": "9:02 PM HEAD #47525 bfd51e7 over 46f153f: removes the heap return"}, REPO) == [("47525", "bfd51e7")]


def test_a_multi_head_record_names_each_prs_own_head_and_the_head_it_replaced():
    record = {
        "refs": {"prs": [f"{REPO}#43299", 43300, "yasyf/cc-skills#836"]},
        "fields": {"attrs": {f"head:{REPO}#43299": f"{OLD}..{HEAD} restack", "head:43300": NEWER, "head:yasyf/cc-skills#836": MOVED, "actor": "lane"}},
        "text": f"HEAD 3 PRs: #43299 {HEAD[:7]}, #43300 {NEWER[:7]}, #836 {MOVED[:7]}: restack onto dev",
    }
    assert finder.heads(record, REPO) == [("43299", HEAD), ("43300", NEWER)]
    assert (finder.previous(record, REPO, "43299"), finder.previous(record, REPO, "43300")) == (OLD, "")


def test_previous_reads_the_head_a_head_record_says_it_replaced():
    assert finder.previous({"text": f"HEAD #43299 {HEAD} (was {OLD}): restack onto #42751"}, REPO, "43299") == OLD
    assert finder.previous({"text": f"HEAD #43299 {HEAD}"}, REPO, "43299") == ""
    assert finder.previous(None, REPO, "43299") == ""


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
