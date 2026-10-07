from __future__ import annotations

import importlib
import importlib.util
import inspect
import re
import sys
import types
import typing
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from livedash import payloads

PAYLOADS = {cls.__name__: cls for cls in (payloads.Table, payloads.Matrix, payloads.Checklist, payloads.Tiles, payloads.Series, payloads.Percentiles, payloads.Graph, payloads.Feed, payloads.Kv, payloads.Markdown, payloads.Svg)}
BUILTIN_PACKAGE = "livedash.components"
RELEASE_PACKAGE = "livedash.components.release"
PACK_PREFIX = "livedash_pack_"
LOCAL_PACKAGE = "livedash_local"
CADENCES = {"15s": 15, "30s": 30, "1m": 60, "2m": 120, "5m": 300, "15m": 900, "manual": None}
TIMEOUT = re.compile(r"^(\d+)(s|m)$")
COMPONENT_ID = re.compile(r"^[a-z][a-z0-9-]*$")
SCALARS = (int, float, str, bool, Path)
MISSING = inspect.Parameter.empty

REGISTRY: dict[str, Spec] = {}
LOAD_ERRORS: dict[str, str] = {}


class BindError(ValueError):
    pass


@dataclass(frozen=True)
class Param:
    name: str
    annotation: object
    default: object


@dataclass(frozen=True)
class Spec:
    id: str
    title: str
    every: str
    timeout: float
    fn: Callable
    payload: type
    params: dict[str, Param]
    actions: dict[str, Callable] = field(default_factory=dict)
    doc: str = ""
    source: str = ""


def seconds(text: str) -> float:
    if not (match := TIMEOUT.match(text)):
        raise BindError(f"timeout must look like 30s or 2m, not {text!r}")
    return int(match[1]) * (60 if match[2] == "m" else 1)


def namespace(module: str) -> str:
    if module.startswith(RELEASE_PACKAGE):
        return "release."
    if module.startswith(BUILTIN_PACKAGE):
        return ""
    if module.startswith(LOCAL_PACKAGE):
        return "local."
    if module.startswith(PACK_PREFIX):
        return module.removeprefix(PACK_PREFIX).split(".", 1)[0] + "."
    raise BindError(f"{module} is not a built-in, pack or local component module")


def allowed(annotation) -> bool:
    if annotation in SCALARS:
        return True
    origin, args = typing.get_origin(annotation), typing.get_args(annotation)
    if origin in (types.UnionType, typing.Union):
        rest = [arg for arg in args if arg is not type(None)]
        return set(rest) == {int, str} or (len(rest) == 1 < len(args) and allowed(rest[0]))
    if origin is list:
        return len(args) == 1 and (args[0] in (int, str) or set(typing.get_args(args[0])) == {int, str})
    if origin is dict:
        return args == (str, str)
    return False


def component(id: str, title: str, every: str = "2m", timeout: str = "30s", actions: dict[str, Callable] | None = None):
    """Register `fn(ctx, *, param=default) -> Payload` as a dashboard component under its pack's namespace."""
    if not COMPONENT_ID.match(id):
        raise BindError(f"component id {id!r} must be lowercase words joined by hyphens")
    if every not in CADENCES:
        raise BindError(f"component {id} has every={every!r}; use one of {', '.join(CADENCES)}")

    def register(fn: Callable) -> Callable:
        hints = typing.get_type_hints(fn)
        payload = hints.get("return")
        if payload not in PAYLOADS.values():
            raise BindError(f"component {id} must annotate its return as one of {', '.join(PAYLOADS)}, not {payload!r}")
        signature = inspect.signature(fn)
        first, *rest = signature.parameters.values()
        if first.kind is not inspect.Parameter.POSITIONAL_OR_KEYWORD:
            raise BindError(f"component {id} must take the context as its first positional parameter")
        params = {}
        for param in rest:
            if param.kind is not inspect.Parameter.KEYWORD_ONLY:
                raise BindError(f"component {id} parameter {param.name} must be keyword-only, after `*`")
            if not allowed(hints.get(param.name)):
                raise BindError(f"component {id} parameter {param.name} has type {hints.get(param.name)!r}; use int, float, str, bool, Path, list[int|str], dict[str, str], or one of those | None")
            params[param.name] = Param(param.name, hints[param.name], param.default)
        qualified = namespace(fn.__module__) + id
        if qualified in REGISTRY and REGISTRY[qualified].fn.__module__ != fn.__module__:
            raise BindError(f"component {qualified} is registered twice: {REGISTRY[qualified].fn.__module__} and {fn.__module__}")
        spec = Spec(qualified, title, every, seconds(timeout), fn, payload, params, dict(actions or {}), inspect.getdoc(fn) or "", fn.__module__)
        REGISTRY[qualified] = spec
        fn.__livedash__ = spec
        return fn

    return register


def coerce(name: str, value, annotation, base: Path):
    origin, args = typing.get_origin(annotation), typing.get_args(annotation)
    if origin in (types.UnionType, typing.Union):
        if value is None and type(None) in args:
            return None
        options = [arg for arg in args if arg is not type(None)]
        if set(options) == {int, str}:
            if isinstance(value, (int, str)) and not isinstance(value, bool):
                return value
            raise BindError(f"{name} must be an int or a string, not {value!r}")
        (option,) = options
        return coerce(name, value, option, base)
    if annotation is Path:
        if not isinstance(value, str):
            raise BindError(f"{name} must be a path string, not {value!r}")
        path = Path(value).expanduser()
        return path if path.is_absolute() else base / path
    if annotation is float and isinstance(value, int) and not isinstance(value, bool):
        return float(value)
    if annotation in SCALARS:
        if not isinstance(value, annotation) or (annotation is not bool and isinstance(value, bool)):
            raise BindError(f"{name} must be {annotation.__name__}, not {value!r}")
        return value
    if origin is list:
        if not isinstance(value, list):
            raise BindError(f"{name} must be a list, not {value!r}")
        return [coerce(f"{name}[{index}]", item, args[0], base) for index, item in enumerate(value)]
    if not isinstance(value, dict) or not all(isinstance(key, str) and isinstance(item, (str, int, float)) and not isinstance(item, bool) for key, item in value.items()):
        raise BindError(f"{name} must be a mapping of strings, not {value!r}")
    return {key: str(item) for key, item in value.items()}


def bind(spec: Spec, given: dict, facts: dict, base: Path) -> dict:
    unknown = sorted(set(given) - set(spec.params))
    if unknown:
        raise BindError(f"{spec.id} takes no parameter {', '.join(unknown)}; it takes {', '.join(spec.params) or 'none'}")
    bound = {}
    for name, param in spec.params.items():
        if name in given:
            bound[name] = coerce(name, given[name], param.annotation, base)
        elif name in facts and facts[name] is not None:
            bound[name] = coerce(name, facts[name], param.annotation, base)
        elif param.default is not MISSING:
            bound[name] = param.default
        else:
            raise BindError(f"{spec.id} needs {name}: set it under `with:` or add it to context.json")
    return bound


def builtins() -> None:
    package = Path(__file__).parent / "components"
    for path in sorted([*package.glob("*.py"), *(package / "release").glob("*.py")]):
        if path.stem != "__init__":
            relative = path.relative_to(package).with_suffix("")
            importlib.import_module(".".join([BUILTIN_PACKAGE, *relative.parts]))


def package(name: str, directory: Path) -> None:
    spec = importlib.util.spec_from_loader(name, loader=None, is_package=True)
    module = importlib.util.module_from_spec(spec)
    module.__path__ = [str(directory)]
    sys.modules[name] = module


def forget(prefix: str) -> None:
    for qualified in [qualified for qualified, spec in REGISTRY.items() if spec.source == prefix or spec.source.startswith(prefix + ".")]:
        del REGISTRY[qualified]
    for name in [name for name in sys.modules if name == prefix or name.startswith(prefix + ".")]:
        del sys.modules[name]
    for name in [name for name in LOAD_ERRORS if name.startswith(prefix)]:
        del LOAD_ERRORS[name]


def load_directory(prefix: str, directory: Path) -> None:
    forget(prefix)
    if not directory.is_dir():
        return
    package(prefix, directory)
    for path in sorted(directory.glob("*.py")):
        if path.stem.startswith("_"):
            continue
        name = f"{prefix}.{path.stem}"
        if name in sys.modules:
            continue
        spec = importlib.util.spec_from_file_location(name, path)
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        try:
            spec.loader.exec_module(module)
        except Exception as failure:
            forget(name)
            LOAD_ERRORS[name] = f"{path.name} failed to load: {type(failure).__name__}: {failure}"


def load_packs(packs: dict[str, str]) -> None:
    for name, directory in sorted(packs.items()):
        load_directory(PACK_PREFIX + name, Path(directory))


def load_local(directory: Path) -> None:
    load_directory(LOCAL_PACKAGE, directory / "components")


def missing(use: str) -> str:
    failed = [error for name, error in LOAD_ERRORS.items() if namespace(name) == use.split(".", 1)[0] + "."]
    if failed:
        return "; ".join(failed)
    return f"unknown component {use!r}; `live-dashboard catalog` lists the built-ins"
