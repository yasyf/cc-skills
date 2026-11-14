import ast
from collections import defaultdict
from pathlib import Path

PLUGIN = Path(__file__).resolve().parents[1]


def test_no_module_defines_a_function_or_class_twice_so_none_replaces_another_as_watch_pys_second_passed_crashed_every_watch_on_1_0_1044() -> None:
    defined: dict[str, list[int]] = defaultdict(list)
    for path in (path for folder in ("lib", "hooks", "capt-hook", "tests") for path in (PLUGIN / folder).rglob("*.py")):
        for node in ast.parse(path.read_text()).body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                defined[f"{path.relative_to(PLUGIN)}:{node.name}"].append(node.lineno)
    assert {name: lines for name, lines in defined.items() if len(lines) > 1} == {}
