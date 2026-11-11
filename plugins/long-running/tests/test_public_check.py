import subprocess
from pathlib import Path

import pytest
from conftest import TEAM
from drive import public_check

LIVE = "https://github.com/yasyf/cc-skills/blob/3d8111f/pr-shots/plan.png?raw=true"


def git(repo: Path, *args: str) -> None:
    subprocess.run(["git", "-c", "user.email=me@example.com", "-c", "user.name=Me", *args], cwd=repo, check=True, capture_output=True)


@pytest.fixture
def public_repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, visibilities: dict) -> Path:
    git(tmp_path, "init", "-q")
    git(tmp_path, "remote", "add", "origin", "https://github.com/yasyf/demo.git")
    (tmp_path / "notes.md").write_text("Rowan wrote the first line.\n")
    git(tmp_path, "add", ".")
    git(tmp_path, "commit", "-q", "-m", "first")
    visibilities["yasyf/demo"] = "public"
    monkeypatch.chdir(tmp_path)
    return tmp_path


def test_a_roster_names_each_teammate_by_full_name_first_name_handle_and_email() -> None:
    roster = public_check.Roster.of(
        [
            *TEAM,
            {"name": "me", "real_name": "Me Myself", "email": "ME@example.com"},
            {"name": "qa_test", "real_name": "Quinn Tester", "email": "qa@example.com"},
            {"name": "pat", "real_name": "Pat Guest", "email": "pat+guest@example.com"},
            {"name": "shared1", "real_name": "Team Account 1", "email": "shared1@example.com"},
            {"name": "solo", "real_name": "Solo", "email": "solo@example.com"},
        ],
        "me@example.com",
    )
    text = "Rowan Vale paired with Juniper; ping @rowan or Juniper.Cole@example.com. The Mac build works, says Mac Ivers. Me, Quinn, Pat, Team and Solo too."
    assert roster.mentions(text) == {"Rowan Vale": 1, "Juniper": 1, "@rowan": 1, "Juniper.Cole@example.com": 1, "Mac Ivers": 1}
    assert roster.named(roster.mentions("Rowan and Rowan met Juniper Cole")) == "Rowan (Rowan Vale) 2 times, Juniper Cole"


def test_a_public_pr_body_is_refused_for_teammates_and_live_pictures_but_not_pictures_shown_as_code() -> None:
    assert public_check.body("yasyf/demo", "Shows a picture as `![caption](/abs.png)` and\n```md\n![x](/abs.png)\n```\n") == ""
    found = public_check.body("yasyf/demo", f"Rowan asked; Rowan Vale agreed.\n![Plan]({LIVE})\n")
    assert found.startswith(
        f"yasyf/demo is public, and its body shows pictures outside a demo or public path ({LIVE}) and names Rowan (Rowan Vale), Rowan Vale. "
    )


def test_a_commit_to_a_public_repository_is_refused_for_each_added_line_naming_a_teammate(public_repo: Path, capsys: pytest.CaptureFixture[str]) -> None:
    (public_repo / "notes.md").write_text("Rowan wrote the first line.\nJuniper Cole wrote the second.\n")
    (public_repo / "new.md").write_text("Ask @rowan.\nA line naming nobody.\n")
    git(public_repo, "add", "notes.md")
    git(public_repo, "add", "--intent-to-add", "new.md")
    assert public_check.main(["notes.md", "new.md"]) == 1
    assert capsys.readouterr().out.splitlines()[:3] == [
        "public-check: new.md:1 names @rowan (Rowan Vale)",
        "public-check: notes.md:2 names Juniper Cole",
        f"public-check: yasyf/demo is public. {public_check.RULE}",
    ]
    (public_repo / "notes.md").write_text("Rowan wrote the first line.\nThe reporter wrote the second.\n")
    (public_repo / "new.md").write_text("Ask the reporter.\n")
    assert public_check.main(["notes.md", "new.md"]) == 0


def test_a_private_repository_or_one_with_no_origin_takes_any_name(public_repo: Path, visibilities: dict) -> None:
    (public_repo / "notes.md").write_text("Rowan Vale and Juniper Cole.\n")
    visibilities["yasyf/demo"] = "private"
    assert public_check.main(["notes.md"]) == 0
    git(public_repo, "remote", "remove", "origin")
    assert public_check.main(["notes.md"]) == 0


def test_a_pr_body_for_a_public_repository_is_checked_whole(public_repo: Path, tmp_path_factory: pytest.TempPathFactory, capsys: pytest.CaptureFixture[str]) -> None:
    body = tmp_path_factory.mktemp("bodies") / "body.md"
    body.write_text("## Refresher\nJuniper's report started this.\n")
    assert public_check.main(["--body", str(body)]) == 1
    assert capsys.readouterr().out.startswith(f"public-check: {body}: yasyf/demo is public, and its body names Juniper (Juniper Cole). ")
    body.write_text("## Refresher\nThe reporter's report started this.\n")
    assert public_check.main(["--body", str(body)]) == 0
