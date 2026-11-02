"""Writes patterns.json: every module-level compiled pattern the Go landing desk ports, keyed ``<file>:<NAME>``."""

from __future__ import annotations

import ast
import importlib
import importlib.util
import json
import re
import sys
from pathlib import Path
from types import ModuleType

SCRIPTS = Path(__file__).resolve().parents[2] / "skills/long-running/scripts"
SOURCES = ("desk-runner.py", "merge_queue.py", "long_running/cci.py", "long_running/grades.py")
GOLDEN = Path(__file__).resolve().parent / "patterns.json"


def load(source: str) -> ModuleType:
    if source == "desk-runner.py":
        spec = importlib.util.spec_from_file_location("desk_runner", SCRIPTS / source)
        module = importlib.util.module_from_spec(spec)
        sys.modules["desk_runner"] = module
        spec.loader.exec_module(module)
        return module
    return importlib.import_module(source.removesuffix(".py").replace("/", "."))


def assigned(tree: ast.Module) -> set[str]:
    names: set[str] = set()
    pending: list[ast.AST] = list(tree.body)
    while pending:
        node = pending.pop()
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda)):
            continue
        if isinstance(node, ast.Assign):
            names |= {target.id for target in node.targets for target in ast.walk(target) if isinstance(target, ast.Name)}
        elif isinstance(node, (ast.AnnAssign, ast.AugAssign)) and isinstance(node.target, ast.Name):
            names.add(node.target.id)
        pending.extend(ast.iter_child_nodes(node))
    return names


def held(name: str, value: object) -> list[tuple[str, re.Pattern[str]]]:
    if isinstance(value, re.Pattern):
        return [(name, value)]
    if isinstance(value, dict):
        return [(f"{name}[{key}]", item) for key, item in value.items() if isinstance(item, re.Pattern)]
    if isinstance(value, (tuple, list)):
        return [(f"{name}[{index}]", item) for index, item in enumerate(value) if isinstance(item, re.Pattern)]
    if isinstance(value, (frozenset, set)):
        members = sorted((item for item in value if isinstance(item, re.Pattern)), key=lambda item: (item.pattern, item.flags))
        return [(f"{name}[{index}]", item) for index, item in enumerate(members)]
    return []


def patterns() -> dict[str, dict[str, object]]:
    sys.path.insert(0, str(SCRIPTS))
    found: dict[str, dict[str, object]] = {}
    for source in SOURCES:
        module = load(source)
        defined = assigned(ast.parse((SCRIPTS / source).read_text()))
        for name, value in vars(module).items():
            if name not in defined:
                continue
            for key, pattern in held(name, value):
                if not isinstance(pattern.pattern, str):
                    raise TypeError(f"{source}:{key} is a bytes pattern")
                found[f"{source}:{key}"] = {"pattern": pattern.pattern, "flags": pattern.flags}
    return found


def main() -> None:
    GOLDEN.write_text(json.dumps(patterns(), indent=1, ensure_ascii=False, sort_keys=True) + "\n")


if __name__ == "__main__":
    main()
