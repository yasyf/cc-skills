"""What one repository adds to every drive working in it, through the provider classes its .claude/long-running.toml names under [providers]: how it releases, stages, proves a build on a Mac box, reads its CI builds, grants elevated access and guards its teams' data, and the text each adds to the skill's pages. A role the table leaves out runs the base class, which does none of it."""

import json
import re
import shlex
import subprocess
import time
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

    def filled(self, template: str, **fields: str) -> str:
        """The shell command template with each `{field}` it is given filled, quoted, and every other brace, such as a jq filter's, left as written."""
        return re.sub(r"\{(\w+)\}", lambda found: shlex.quote(fields[found[1]]) if found[1] in fields else found[0], template)

    def run(self, template: str, stdin: str | None = None, timeout: int = COMMAND_SECONDS, **fields: str) -> str:
        """The output of the shell command template filled by filled, run in the checkout; a ProviderError when it exits non-zero."""
        command = self.filled(template, **fields)
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
    """The repository's identity, its landing queue and the tools its lanes run; research_tools are the programs, by name or by the path they end in, whose runs read CI or the repository's release state, which a communicator hands to its subagent rather than running itself."""

    name: str | None = None
    guarded_owner: str | None = None
    review_ask: str | None = None
    fde_scan: str | None = None
    page_owner: str | None = None
    research_tools: frozenset[str] = frozenset()

    def queue(self) -> Queue | None:
        """The merge queue a PR to this repository lands through; None when PRs land through GitHub alone."""
        return None

    def lane_tool(self) -> LaneTool | None:
        """The tool a lane runs from its tree that the drive points at its checkout; None when lanes run each tool as their own tree holds it."""
        return None

    def reviewer(self) -> str | None:
        """The review bot review_ask asks, by its login; None when the repository asks none."""
        return self.review_ask.split()[0].removeprefix("@") if self.review_ask else None

    def ci_config(self, path: str) -> bool:
        return False

    def checkout_tools(self, path: str) -> bool:
        return False

    def dangerous_paths(self, paths: list[str]) -> list[str]:
        return []


class ReleaseProvider(Provider):
    """How the repository releases: its targets, a release's record, the freezes standing, what a PR reaches, what runs where, and how a cut names its holder; approve_command, hold_command and show_command are the shell commands, each filling `{release}`, that pass a release build stopped at its approve step, hold it and show the whole release, which his approve card runs and names, and a repository naming no approve_command draws no such card."""

    whole: str | None = None
    rehearsal_envs: frozenset[str] = frozenset()
    approve_command: str | None = None
    hold_command: str | None = None
    show_command: str | None = None

    def prebuild(self, commit: str) -> str | None:
        """Kick off the builds a later release at commit adopts, so a production start there builds nothing its staging start already built, returning the link of the build it started; None when the repository prebuilds nothing."""
        return None

    def gives_way(self, words: list[str]) -> bool:
        """Whether a release start, as its words, is the staging release a production release runs first, which goes through every staging freeze and every staging slot a test holds."""
        return False

    def targets(self) -> list[str]:
        return []

    def status(self, release: str) -> dict | None:
        return None

    def show(self, release: str) -> str:
        return ""

    def post(self, release: str) -> str | None:
        """The permalink of the Slack post that announces release, named by its name or build number, which a post to him links in place of its build page; None when the repository keeps none."""
        return None

    def released(self, link: str) -> str | None:
        """The release a link to one of its builds names, by build number, which a post links as its Slack post in place of the build page; None for any other link, or when the repository keeps no release posts."""
        return None

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

    def running(self) -> list[dict]:
        """Each release start the release tool still runs, whoever started it, one row per target it ships and place it names, with its `target`, `env`, None when it names no place and so ships every place its target reaches, its full `commit` and its `build` link; empty when the repository knows of none."""
        return []

    def place(self, env: str) -> Place:
        return Place(None, True, ())

    def start(self, words: list[str]) -> dict[str, tuple[str, ...]] | None:
        """What a release start or hotfix the shell words run ships and from where, each flag's values by name, `targets`, `env`, `without`, `only`, `exclude`, `from`, `order`, `hotfix` and the rest, as the release tool reads them; None for any other command."""
        return None

    def parts(self) -> dict[str, tuple[str, ...]]:
        """Each release target's components."""
        return {}

    def dry_run(self, pr: str, components: list[str]) -> str:
        """The plan a production release of components at the head of the PR numbered pr would apply, read without releasing, which a PR reaching only components staging runs no copy of carries as its proof."""
        raise ProviderError("this repository reads no production dry-run plan")

    def freeze(self, components: list[str], why: str, until: str) -> str:
        """Freeze releases of components until the stamp until, for why; the freeze's id."""
        raise ProviderError("this repository sets no release freeze")

    def unfreeze(self, freeze: str) -> str:
        """The command that lifts the freeze with id freeze."""
        return ""

    def mutation(self, words: list[str]) -> dict | None:
        """What the shell words write to the repository's releases, read from the words alone: `{"kind": "release", "verb": <verb or None>}` for a run of the release tool's own verb; `{"kind": "write", "verb": <verb>, "build": <the build it names, as written, or None>, "job": <the cluster Job its inline body starts, or None>}` for a rerun, cancel or API write to a build of the release pipeline, its verb `rerun`, `cancel` or `api`; `{"kind": "job", "job": <the Job, or None>}` for a start of a Job from a suspended CronJob; a Job as `{"cluster", "cronJob", "reason"}`; None for any other command."""
        return None

    def cut(self, build: str) -> bool:
        """Whether the release pipeline's build numbered build is a release cut, read now; a ProviderError or the CI read's own error when it cannot be read."""
        return False

    def build_job(self, build: str) -> dict | None:
        """The cluster Job the release pipeline's build numbered build starts, as mutation() gives one, read now; None when it starts none; a ProviderError or the CI read's own error when it cannot be read."""
        return None


class StagingProvider(Provider):
    """Where the repository stages a change before it lands, and the steps a staging pass takes there."""

    env: str | None = None

    def unread(self, error: str) -> str:
        return ""

    def exception_steps(self) -> str:
        """The steps a staging test takes for components staging has no copy of."""
        return ""

    def required_cases(self, paths: list[str]) -> list[str]:
        """The staging cases a PR changing these paths must pass, each named as its tester's CASE line names it, such as a Mac agent's LLM gateway call for a change to that gateway's auth: its tester's Staging line lists them, and drive job report keeps its DONE only when each reads pass, never not exercised."""
        return []


class MacBoxProvider(Provider):
    """The repository's Mac boxes: a staging pass proving a desktop build on one, and taking each down with the job it is named after."""

    builds: frozenset[str] = frozenset()
    signed_in_step: str | None = None

    def down(self, box: str, holder: str, finished: bool) -> int:
        """Give the box back, finished saying its holder job is done or failed, so the apps it left open may be quit first."""
        return 0

    def runs(self, box: str, pid: int) -> bool:
        """Whether the process numbered pid runs on the Mac box named box now, which a job's pid:<n>@<box> wait holds on."""
        raise ProviderError("this repository reads no process on a Mac box")

    def live_boxes(self) -> list[str] | None:
        """The name of each live box; None when the repository keeps none."""
        return None

    def hand_over(self, box: str, holder: str, prior: str) -> str | None:
        return None

    def reap(self, keep: list[str]) -> None:
        """Scale the repository's warm pool down by one decision, never touching a box keep names; nothing for a repository that keeps none."""

    def warm_boxes(self) -> list[str]:
        """The name of each warm box the pool holds free."""
        return []

    def take(self, box: str, holder: str) -> None:
        """Give holder a box named box, a warm one first, else a new one; ProviderError when no host is free."""

    def affected(self, components: list[str]) -> bool:
        return False

    def affected_paths(self, paths: list[str]) -> bool:
        """Whether a PR changing paths needs its staging cases run on a Mac box though no component it reaches does, read only when the repository defines it."""
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
    artifact_url: re.Pattern | None = None
    receiver_path: str | None = None
    graders: str = "CI"

    def match(self, text: str) -> re.Match | None:
        """The build link text starts with, by build_url; None for any other text, or for every text when the repository names no build links."""
        return self.build_url.match(text) if self.build_url else None

    def fullmatch(self, text: str) -> re.Match | None:
        """The build link text is whole, by build_url; None for any other text, or for every text when the repository names no build links."""
        return self.build_url.fullmatch(text) if self.build_url else None

    def finditer(self, text: str) -> list[re.Match]:
        """Each build link text holds, by build_url; none when the repository names no build links."""
        return list(self.build_url.finditer(text)) if self.build_url else []

    def read(self, build: re.Match) -> dict:
        """The build a link matched by build_url names, as the CI reads it now: its state, its jobs and the environment it started with; a reads.ReadError when it does not read back."""
        return {}

    def builds(self, links: list[str], fields: str) -> dict[str, dict | None]:
        """Each build by its link, as one read of the repository's CI answers its GraphQL fields, None for a build it answers none for."""
        return {}

    def webhook(self, headers: Any, body: bytes, key: bytes) -> list[str] | None:
        """The build links a CI webhook delivery names once its signature checks against key, [] for one naming no build; None when the delivery is no CI event of this repository's, so the receiver reads it as GitHub's; a ProviderError naming the event when its signature fails."""
        return None

    def log_tip(self, job_url: str) -> str:
        """Where a failed CI job's own output is read, for the guard refusing a rerun past it."""
        return ""

    def artifact_link(self, text: str) -> re.Match | None:
        """The CI artifact link text is whole, by artifact_url; None for any other text, or for every text when the repository names no artifact links."""
        return self.artifact_url.fullmatch(text) if self.artifact_url else None

    def artifact(self, link: re.Match, out: Path) -> None:
        """Write the CI artifact a link matched by artifact_url names to out, such as a picture a lane on another machine uploaded for a Show me; a ProviderError when it does not download."""
        raise ProviderError("this repository names no CI artifacts")

    def retried(self, words: list[str]) -> dict | None:
        """The build or job a run of the repository's own CI tool reruns, as `{"step": <the words naming the rerun>, "pipeline": <org/slug>, "build": <number or None>, "job": <job id or None>, "branch": <branch or None>}`, a branch meaning its pipeline's newest build there to finish; None for a command of its tool that reruns nothing, and for any other command."""
        return None


class AccessProvider(Provider):
    """How a drive takes elevated access to one of the repository's stacks."""

    stack_example: str = ""

    @contextmanager
    def grant(self, stack: str, reason: str, asker: str, started: Any) -> Iterator[Any]:
        """The grant of elevated access to the stack, started in the asker's name for the reason, its link handed to started the moment it exists, and passed as the drive's reads.Grant the command under it runs with, its credentials gone when the block ends and the drive's stop signals recorded rather than acted on from their writing to their deletion."""
        raise ProviderError("this repository grants no elevated access")
        yield


class DataProvider(Provider):
    """What holds a team's data in this repository, which a guard refuses any session reading or writing where it runs: rule, the drive's rule on where a team's data is read or written, which each refusal and a brief's Runs as refusal quote; staff_routes, the words in a reader's tool calls that show it took one of the repository's staff routes to a refused read, and staff_note, the text naming those routes and where a team's data is read, which the reader ending on a refused read is told."""

    team_area: re.Pattern | None = None
    namespaces: frozenset[str] = frozenset()
    rule: str = "a drive job reads or writes a team's data only as a service account, on a machine the repository grants it or in the team's own cluster, never on this Mac, a Sprite or under `drive break-glass`"
    staff_routes: frozenset[str] = frozenset()
    staff_note: str = ""

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


LOADED: dict[tuple[str, str, str], Providers] = {}
HEADS: dict[str, tuple[str, float]] = {}
HEAD_SECONDS = 60


def git(clone: Path, *argv: str) -> subprocess.CompletedProcess:
    """Run git on the clone by -C, never as the process's working folder, so a folder a hook parsed from a command's words, such as `$(drive --root)/lib` unexpanded or a lane's worktree removed since, is a git error default_head turns into a ProviderError, never a FileNotFoundError that crashes the hook (733991, 734096)."""
    return subprocess.run(["git", "-C", str(clone), *argv], capture_output=True, text=True, check=False)


def default_head(clone: Path) -> str:
    """The commit the clone's origin/HEAD, its default branch, names, read again once HEAD_SECONDS pass, so a hot path pays for no git process per call."""
    if (kept := HEADS.get(str(clone))) and time.monotonic() - kept[1] < HEAD_SECONDS:
        return kept[0]
    found = git(clone, "rev-parse", "--verify", "refs/remotes/origin/HEAD^{commit}")
    if found.returncode:
        raise ProviderError(f"{clone} has no origin/HEAD to read its providers from: {found.stderr.strip()}")
    HEADS[str(clone)] = (found.stdout.strip(), time.monotonic())
    return HEADS[str(clone)][0]


def blob(clone: Path, sha: str, path: str) -> str | None:
    shown = git(clone, "show", f"{sha}:{path}")
    return shown.stdout if shown.returncode == 0 else None


def at(clone: Path, checkout: Path | None = None) -> Providers:
    """The providers the .claude/long-running.toml at the default branch's head of the repository cloned at clone names, their module read from that same commit, never a working tree, so no branch changes the providers that judge it; each run commands in checkout, the clone when none is named. One load per process per clone, checkout and commit."""
    sha = default_head(clone)
    if (key := (str(clone), str(checkout or clone), sha)) in LOADED:
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
