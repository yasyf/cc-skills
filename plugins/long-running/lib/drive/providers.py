"""What one repository adds to every drive working in it, through the provider classes its .claude/long-running.toml names under [providers]: how it releases, stages, proves a build on a Mac box, reads its CI builds, grants elevated access and guards its teams' data, and the text each adds to the skill's pages. A role the table leaves out runs the base class, which does none of it."""

import json
import re
import shlex
import subprocess
import tomllib
import types
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, NamedTuple

from drive.state import DriveError

REPO_RULES = ".claude/long-running.toml"
COMMAND_SECONDS = 600
ROLES = ("repo", "release", "staging", "mac_box", "build", "access", "data")


class ProviderError(DriveError):
    pass


class Place(NamedTuple):
    stage: str | None
    prod: bool
    teams: tuple[str, ...]


class Queue(NamedTuple):
    """The merge queue a repository lands through: the script, relative to its checkout, the lander runs to enqueue and dequeue a stack; each command that enqueues, as its words, the flags taking a value and the verbs that only read; and why a drive session may land a PR no other way, its `{key}` the PR."""

    script: str
    commands: tuple[tuple[tuple[str, ...], frozenset[str], tuple[str, ...]], ...]
    reason: str


class LaneTool(NamedTuple):
    """A tool a lane runs from its own tree that the drive points at its checkout, which holds the default branch's head: the words that run it, the pattern each such word matches in a command, its path in a checkout, and the source paths whose change in a lane means the lane tests its own copy."""

    words: tuple[str, ...]
    word: re.Pattern
    path: str
    sources: tuple[str, ...]


class Provider:
    """One role's provider for the repository cloned at clone, its commands run in checkout, the clone's tree the drive keeps at its default branch's head."""

    def __init__(self, clone: Path, checkout: Path, config: dict) -> None:
        self.clone = clone
        self.checkout = checkout
        self.config = config

    def page(self, name: str) -> str:
        """What this provider adds to the skill page named name, under its `## In this repository` heading."""
        return ""

    def run(self, template: str, stdin: str | None = None, timeout: int = COMMAND_SECONDS, **fields: str) -> str:
        """The output of the shell command template with each `{field}` filled, quoted, run in the checkout; a ProviderError when it exits non-zero."""
        command = template.format_map({key: shlex.quote(value) for key, value in fields.items()})
        try:
            done = subprocess.run(command, shell=True, cwd=self.checkout, input=stdin, capture_output=True, text=True, timeout=timeout, check=False)
        except subprocess.TimeoutExpired:
            raise ProviderError(f"`{command}` ran past {timeout} s") from None
        if done.returncode:
            said = done.stderr.strip().splitlines()
            raise ProviderError(f"`{command}` exited {done.returncode}: {said[-1] if said else 'no output'}")
        return done.stdout

    def json(self, template: str, stdin: str | None = None, timeout: int = COMMAND_SECONDS, **fields: str) -> Any:
        return json.loads(self.run(template, stdin=stdin, timeout=timeout, **fields))


class RepoProvider(Provider):
    """The repository's identity, its landing queue and the tools its lanes run."""

    name: str | None = None
    guarded_owner: str | None = None
    review_ask: str | None = None
    fde_scan: str | None = None
    page_owner: str | None = None

    def queue(self) -> Queue | None:
        """The merge queue a PR to this repository lands through; None when PRs land through GitHub alone."""
        return None

    def lane_tool(self) -> LaneTool | None:
        """The tool a lane runs from its tree that the drive points at its checkout; None when lanes run each tool as their own tree holds it."""
        return None

    def ci_config(self, path: str) -> bool:
        return False

    def checkout_tools(self, path: str) -> bool:
        return False

    def dangerous_paths(self, paths: list[str]) -> list[str]:
        return []


class ReleaseProvider(Provider):
    """How the repository releases: its targets, a release's record, the freezes standing, what a PR reaches, what runs where, and how a cut names its holder."""

    whole: str | None = None
    rehearsal_envs: frozenset[str] = frozenset()

    def prebuild(self, commit: str) -> None:
        return None

    def targets(self) -> list[str]:
        return []

    def status(self, release: str) -> dict | None:
        return None

    def show(self, release: str) -> str:
        return ""

    def freezes(self) -> list[dict]:
        return []

    def holder(self, holder: str) -> str | None:
        """The flag naming holder, `<drive>/<job>`, on a freeze and each staging command of a cut; None when releases name no holder."""
        return None

    def join(self, freeze: str, holder: str) -> str | None:
        """The command that joins holder to the freeze a cut's starter ran; None when a rider needs none."""
        return None

    def components(self, pr: str, paths: list[str], head: str | None) -> dict:
        """What the PR numbered pr, changing paths, reaches: its `components`, `touched`, `dev`, `releases` and `staged` lists, as cli.release_reach reads them."""
        return {"components": [], "touched": [], "dev": [], "releases": [], "staged": [], "tested": True}

    def live(self) -> list[dict]:
        """Each cluster of every service and the commit it serves, one row each with its `target`, `env`, `cluster` and `commit`."""
        return []

    def place(self, env: str) -> Place:
        return Place(None, True, ())

    def start(self, words: list[str]) -> dict[str, tuple[str, ...]] | None:
        """What a release start or hotfix the shell words run ships and from where, each flag's values by name, `targets`, `env`, `without`, `only`, `exclude`, `from`, `order`, `hotfix` and the rest, as the release tool reads them; None for any other command."""
        return None

    def parts(self) -> dict[str, tuple[str, ...]]:
        """Each release target's components."""
        return {}

    def freeze(self, components: list[str], why: str, until: str) -> str:
        """Freeze releases of components until the stamp until, for why; the freeze's id."""
        raise ProviderError("this repository sets no release freeze")

    def unfreeze(self, freeze: str) -> str:
        """The command that lifts the freeze with id freeze."""
        return ""


class StagingProvider(Provider):
    """Where the repository stages a change before it lands, and the steps a staging pass takes there."""

    env: str | None = None

    def claim_steps(self, env: str, job: str) -> str:
        return ""

    def unread(self, error: str) -> str:
        return ""

    def exception_steps(self) -> str:
        """The steps a staging test takes for components staging has no copy of."""
        return ""


class MacBoxProvider(Provider):
    """The repository's Mac boxes: a staging pass proving a desktop build on one, and taking each down with the job it is named after."""

    builds: frozenset[str] = frozenset()
    signed_in_step: str | None = None

    def down(self, box: str, holder: str) -> int:
        return 0

    def live_boxes(self) -> list[str] | None:
        """The name of each live box; None when the repository keeps none."""
        return None

    def hand_over(self, box: str, holder: str, prior: str) -> str | None:
        return None

    def affected(self, components: list[str]) -> bool:
        return False

    def live_commit(self) -> str:
        raise ProviderError("this repository proves no build on a Mac box")

    def case_steps(self, tested: str, live: str) -> str:
        return ""

    def rebuild_steps(self, tested: str) -> str:
        """The steps that run the cases of the PRs tested again on a box built at their own head, for PRs changing what a box builds."""
        return ""


class BuildProvider(Provider):
    """The repository's CI builds, as links, reads, logs and webhook events."""

    build_url: re.Pattern | None = None
    receiver_path: str | None = None
    graders: str = "CI"

    def builds(self, links: list[str], fields: str) -> dict[str, dict | None]:
        """Each build by its link, as one read of the repository's CI answers its GraphQL fields, None for a build it answers none for."""
        return {}

    def webhook(self, headers: Any, body: bytes, key: bytes) -> list[str] | None:
        """The build links a CI webhook delivery names once its signature checks against key, [] for one naming no build; None when the delivery is no CI event of this repository's, so the receiver reads it as GitHub's; a ProviderError naming the event when its signature fails."""
        return None

    def log_tip(self, job_url: str) -> str:
        """Where a failed CI job's own output is read, for the guard refusing a rerun past it."""
        return ""


class AccessProvider(Provider):
    """How a drive takes elevated access to one of the repository's stacks."""

    stack_example: str = ""

    @contextmanager
    def grant(self, stack: str, why: str, started: Any) -> Iterator[Any]:
        raise ProviderError("this repository grants no elevated access")
        yield


class DataProvider(Provider):
    """What holds a team's data in this repository, which a guard refuses any session reading or writing where it runs."""

    team_area: re.Pattern | None = None
    namespaces: frozenset[str] = frozenset()

    def buckets(self) -> set[str] | None:
        """Each bucket or bucket folder, as `<bucket>/` or `<bucket>/<prefix>/`, the repository's default branch lists as holding a team's data; None when its tree holds no such list, which refuses every bucket but its outputs()."""
        return set()

    def outputs(self) -> set[str]:
        """The buckets the repository's own code names as its build, release and infra output, which pass while buckets() is None."""
        return set()

    def refusal(self, kind: str, **fields: str) -> str:
        """Why the guard refuses a call reaching a team's data where it runs: kind `team` for `{location}` holding it, `unlisted` for `{location}` from `{folder}`, a tree holding no inventory, `namespace` for `{step}` reaching it `{where}`; `rule` carries the drive's own rule."""
        return f"refused: this reaches a team's data where the job runs, and {fields['rule']}"


BASES: dict[str, type[Provider]] = {"repo": RepoProvider, "release": ReleaseProvider, "staging": StagingProvider, "mac_box": MacBoxProvider, "build": BuildProvider, "access": AccessProvider, "data": DataProvider}


@dataclass(frozen=True)
class Providers:
    sha: str | None
    config: dict = field(default_factory=dict)
    repo: RepoProvider = field(default_factory=lambda: RepoProvider(Path(), Path(), {}))
    release: ReleaseProvider = field(default_factory=lambda: ReleaseProvider(Path(), Path(), {}))
    staging: StagingProvider = field(default_factory=lambda: StagingProvider(Path(), Path(), {}))
    mac_box: MacBoxProvider = field(default_factory=lambda: MacBoxProvider(Path(), Path(), {}))
    build: BuildProvider = field(default_factory=lambda: BuildProvider(Path(), Path(), {}))
    access: AccessProvider = field(default_factory=lambda: AccessProvider(Path(), Path(), {}))
    data: DataProvider = field(default_factory=lambda: DataProvider(Path(), Path(), {}))

    def pages(self, name: str) -> list[str]:
        return [text for role in ROLES if (text := getattr(self, role).page(name).strip())]


LOADED: dict[tuple[str, str], Providers] = {}


def git(clone: Path, *argv: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *argv], cwd=clone, capture_output=True, text=True, check=False)


def default_head(clone: Path) -> str:
    """The commit the clone's origin/HEAD, its default branch, names."""
    found = git(clone, "rev-parse", "--verify", "refs/remotes/origin/HEAD^{commit}")
    if found.returncode:
        raise ProviderError(f"{clone} has no origin/HEAD to read its providers from: {found.stderr.strip()}")
    return found.stdout.strip()


def blob(clone: Path, sha: str, path: str) -> str | None:
    shown = git(clone, "show", f"{sha}:{path}")
    return shown.stdout if shown.returncode == 0 else None


def at(clone: Path, checkout: Path | None = None) -> Providers:
    """The providers the .claude/long-running.toml at the default branch's head of the repository cloned at clone names, their module read from that same commit, never a working tree, so no branch changes the providers that judge it; each run commands in checkout, the clone when none is named. One load per process per clone and commit."""
    sha = default_head(clone)
    if (key := (str(clone), sha)) in LOADED:
        return LOADED[key]
    text = blob(clone, sha, REPO_RULES)
    config = tomllib.loads(text) if text else {}
    table = config.get("providers", {})
    if not table:
        LOADED[key] = Providers(sha, config)
        return LOADED[key]
    module = load(clone, sha, table["module"])
    built = {}
    for role, base in BASES.items():
        named = table.get(role)
        made = getattr(module, named, None) if named else base
        if not (isinstance(made, type) and issubclass(made, base)):
            raise ProviderError(f"{table['module']} at {sha[:12]} names {role} = {named!r}, no subclass of {base.__name__} it defines")
        built[role] = made(clone, checkout or clone, config)
    LOADED[key] = Providers(sha, config, **built)
    return LOADED[key]


def load(clone: Path, sha: str, path: str) -> types.ModuleType:
    """The module at path in the commit sha, executed under a name of its own; a ProviderError naming why when the file is missing or raises, a SystemExit included."""
    if (source := blob(clone, sha, path)) is None:
        raise ProviderError(f"[providers] module {path} is not in {sha[:12]}")
    module = types.ModuleType(f"drive_providers_{sha[:12]}")
    module.__file__ = str(clone / path)
    try:
        exec(compile(source, module.__file__, "exec"), module.__dict__)
    except (Exception, SystemExit) as error:
        raise ProviderError(f"{path} at {sha[:12]} failed to load: {type(error).__name__}: {error}") from None
    return module


def of(data: dict) -> Providers:
    """The providers of the repository the drive works in, read from its coordinator's clone, their commands run in the drive's checkout."""
    from drive import state

    clone = Path(next(iter(state.bound_sessions(data).values()))["cwd"])
    return at(clone, state.drive_checkout(data["name"], str(clone)))
