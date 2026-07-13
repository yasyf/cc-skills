# {{PROJECT_NAME}} Python Style Guide

The concrete style rules for the Python code under `{{SECONDARY_CODE_ROOT}}/`. Target
Python 3.13+. The repo's primary language follows the root `STYLEGUIDE.md`; this file
governs the Python.

## Core Principles

1. **Functional over imperative.** Compose, chain, and return. Skip intermediate
   variables when a pipeline reads well, and reach for the walrus (`:=`) and
   comprehensions instead of loops.
2. **Match for dispatch.** Pattern matching for type dispatch, destructuring, and
   multi-factor decisions. Use `if/elif` only for meaningful boolean flags.
3. **Type everything.** `from __future__ import annotations` in every module.
   Never widen a typed slot to `Any` to quiet the checker.
4. **Fail fast, fail loud.** No defensive coding: no fallbacks, shims, or
   backwards-compat layers, and no guards against impossible states. No sentinel
   values, no silent defaults. If unused, delete it. Crash on the unexpected.
5. **Make invalid states unrepresentable.** `NewType` for branded primitives,
   frozen dataclasses for immutable data, required fields over optionals.
6. **Minimal changes.** Stay within scope. Make the test pass, then stop. Improve
   only the code you touch.
7. **Match surrounding code.** Follow this guide first, then the file you're in,
   then the module. If surrounding code violates this guide, fix it.
8. **Flat over nested.** Early returns and flat control flow. Nesting deeper than
   three levels is a smell.
9. **Async-native I/O.** Anything that touches I/O is `async def`, backed by a
   library with a native async API (e.g. `aiosqlite`) — never a blocking call
   wrapped in `asyncio.to_thread`. See § Async.

## Functional Style

Avoid intermediate variables. Chain operations or return directly.

```python
# Good
def expand_tool_names(name: str) -> set[str]:
    return (base := set(name.split("|"))) | {
        alias for n in base for alias in (TOOL_ALIASES.get(n), TOOL_ALIASES_REVERSE.get(n)) if alias
    }

# Bad
def expand_tool_names(name):
    base = set(name.split("|"))
    aliases = set()
    for n in base:
        ...
    return base | aliases
```

Use the walrus operator to bind a value once and reuse it inside an expression.

```python
# Good
if (match := WHEEL_CHECKSUM.search(body)):
    return match.group(1)

# Good — walrus in a comprehension, single pass
return [result for item in items if (result := process(item)) is not None]
```

Prefer the dict union operator over unpacking.

```python
config = defaults | user_config | overrides   # not {**defaults, **user_config, ...}
```

Use comprehensions instead of imperative accumulation.

```python
# Good
return [item.transform() for item in items if item.is_valid()]

# Bad
result = []
for item in items:
    if item.is_valid():
        result.append(item.transform())
return result
```

## Type Annotations

Always annotate. Use future annotations and guard expensive or cycle-prone imports
with `TYPE_CHECKING`. Under PEP 563 annotations stay strings, so they need no quotes.

```python
from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .models import Record

def process(self, record: Record) -> bool: ...
```

Lazy imports that break cycles or defer heavy modules go at the top of the function
body, before any logic, and never inside an `if`, `for`, or `try`.

```python
# Good
def model_version() -> str:
    from .state import RESOURCES

    return RESOURCES.lookup()

# Bad — import buried in a branch
def model_version() -> str:
    if cached:
        from .state import RESOURCES
        ...
```

Don't widen to `Any` to quiet the type checker. Use the real type, narrow with
`isinstance`, or split the model. Trivial complaints such as `cached_property`
shadowing `property` or descriptor-protocol nuances are noise; ignore them instead
of reaching for `# type: ignore`. Wanting `hasattr` on a typed object means the type
is wrong. Fix it or define a `Protocol`.

## Pattern Matching

Use `match` for type dispatch, destructuring, and decisions that turn on several
factors at once.

```python
match decision:
    case Keep():
        return msg
    case Compress(rate=rate):
        return msg.filter(lambda c: c.type != "text").append(compress(text, rate))
    case Summarize(content=content):
        return msg.append(content)
```

For multi-factor decisions, name the state with a `NamedTuple` so each `case` maps
one-to-one onto a requirement.

```python
match Status(is_fresh, scores.get(id(tc))):
    case Status(score=None):           return tc
    case Status(score=s) if s >= floor: return tc
    case Status(is_fresh=True):        return tc.demote()
    case Status(is_fresh=False):       return tc.exclude()
```

Use `if/elif` when the branches turn on meaningful boolean flags with their own
names. Don't build a tuple just to pattern-match on it.

## Functions & Methods

Options and flags go keyword-only, after `*`.

```python
def run(self, jobs: Sequence[Job], *, timeout: int = 30, strict: bool = True) -> Result: ...
```

Use `@overload` when the return type depends on the argument shape.

```python
@overload
def __getitem__(self, index: int) -> Task: ...
@overload
def __getitem__(self, index: slice) -> tuple[Task, ...]: ...
def __getitem__(self, index: int | slice) -> Task | tuple[Task, ...]:
    return self.tasks[index]
```

Mutable defaults are forbidden in function signatures too: take `list[T] | None = None`
and normalize with `items = items or []` at the top of the body.

Access typed attributes directly instead of routing through helpers that may return
None; a helper that can fail forces every caller into a guard.

## API Design

Accept what callers naturally have. If callers must extract or transform data before
calling, take the parent object and extract internally.

Keep parameters minimal. No speculative flags; add a parameter when there is a
demonstrated need, not just in case.

Types reflect user concepts, not implementation internals. A public signature built
from internal metadata types leaks the implementation; expose the objects callers
think in.

## Async

I/O is async from day 1. Anything that hits the network, filesystem, or a database is
an `async def`, and the library doing it has a native async API. Reach for the
async-native driver instead of wrapping a blocking one in `asyncio.to_thread` — the
wrapper leaks a sync boundary into every caller and caps throughput at the thread
pool. Keep `to_thread` / `run_in_executor` for libraries with no async equivalent.
Run concurrent work through `anyio` `TaskGroup`s rather than juggling bare
`asyncio.gather`.

## Error Handling

Keep `try` blocks minimal. Only the line that can throw belongs inside.

```python
# Good
try:
    response = await client.fetch(url)
except HTTPError:
    return None
data = response.json()
return transform(data)
```

No broad `except Exception` that swallows everything. Use dedicated exception
classes. Read required configuration with `os.environ["KEY"]` so a missing key raises
at startup. No sentinel return values; raise, or return a typed result.

## Code Organization

Module order runs imports, constants, type aliases, helpers, classes, then functions.
Module-level `UPPER_SNAKE_CASE` constants sit immediately after imports, before any
class or function.

Within a class body, all assignments come before any methods. That covers constants,
`ClassVar`s, and dataclass fields.

```python
@dataclass(frozen=True, slots=True)
class JobSpec:
    name: str
    steps: tuple[Step, ...] = ()
    retries: int = 0

    def matches(self, job: Job) -> bool: ...
```

No leading underscores on classes, constants, or module-level helpers. Reserve a
leading underscore for a private instance attribute.

`__init__.py` exposes only the public API surface, re-exported with plain regular
imports. No redundant `as` aliases, no `__all__`: name a symbol here to make it
public, omit it to keep it internal. F401 stays active in every other module, so
unused imports outside `__init__.py` are still deleted.

Frozen dataclasses for immutable and config data. Every mutable default needs a
factory such as `field(default_factory=list)`; a bare `[]` or `{}` is a bug.

## Comments & Docstrings

Code documents itself through names, types, and organization. No comments except
TODOs, non-obvious workarounds, or disabled code.

Docstrings are the one exception, scoped by surface. Public API surfaces and
user-facing classes carry Google-style docstrings, so they earn their place. Internal
helpers get none, and a docstring that restates the signature is clutter to delete.

```python
# Good — public class, documented
@dataclass(frozen=True, slots=True)
class Matcher:
    """Matches a record against a regex pattern.

    Example:
        >>> Matcher("user_.*").matches(record)
    """

    pattern: str

# Good — internal helper, no docstring
def version_key(dirname: str) -> tuple[int, ...]:
    return tuple(int(part) for part in dirname.split("."))
```

## Testing

Tests live beside the code as `test_*.py`; run them with `uv run pytest`. Write strict
assertions against specific expected values; a test that can't fail uncovers nothing.
Mock the boundaries your code talks to, such as the network, filesystem, and clock,
and leave the function under test real. A database (or any stateful service) is **not**
a mock boundary: when a test needs one, start a real ephemeral instance with
`testcontainers` rather than mocking the driver or using an in-memory fake.
Parameterize repeated test bodies, giving each case a descriptive `id` and its own
expected values.
