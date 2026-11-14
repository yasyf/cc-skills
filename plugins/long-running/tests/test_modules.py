import ast
from collections import defaultdict
from pathlib import Path

PLUGIN = Path(__file__).resolve().parents[1]


def test_no_module_binds_a_name_twice_so_none_replaces_another_as_watch_pys_second_passed_crashed_every_watch_on_1_0_1044_and_test_remotes_second_unread_failed_main() -> None:
    defined: dict[str, list[int]] = defaultdict(list)
    for path in (path for folder in ("lib", "hooks", "capt-hook", "tests") for path in (PLUGIN / folder).rglob("*.py")):
        for node in ast.parse(path.read_text()).body:
            match node:
                case ast.FunctionDef(name=name) | ast.AsyncFunctionDef(name=name) | ast.ClassDef(name=name):
                    names = [name]
                case ast.Assign(targets=targets):
                    names = [target.id for target in targets if isinstance(target, ast.Name)]
                case ast.AnnAssign(target=ast.Name(id=name), value=value) if value is not None:
                    names = [name]
                case _:
                    names = []
            for name in names:
                defined[f"{path.relative_to(PLUGIN)}:{name}"].append(node.lineno)
    assert {name: lines for name, lines in defined.items() if len(lines) > 1} == {}
