import ast
from pathlib import Path

DRIVE_CHECKS = Path(__file__).resolve().parents[1] / "capt-hook" / "hooks" / "drive_checks.py"


def test_the_say_judges_take_capt_hooks_default_20_second_deadline() -> None:
    judges = {
        node.targets[0].id: node.value
        for node in ast.parse(DRIVE_CHECKS.read_text()).body
        if isinstance(node, ast.Assign) and isinstance(node.targets[0], ast.Name) and node.targets[0].id in {"JUDGE", "HANDLER_JUDGE"}
    }
    assert set(judges) == {"JUDGE", "HANDLER_JUDGE"}
    assert all(isinstance(call, ast.Call) and "deadline" not in {keyword.arg for keyword in call.keywords} for call in judges.values())
