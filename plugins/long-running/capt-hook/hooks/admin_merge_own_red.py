from __future__ import annotations

import json
import re
import subprocess

from captain_hook import Allow, BaseHookEvent, Block, Event, HookResult, Input, Runs, Tool, Warn, on
from captain_hook.command_schema import CommandSchema, Operand, Option
from captain_hook.util import reqenv

from .tests.merge_fixtures import GREEN, INHERITED_LINT, INHERITED_TEST, OWN_LINT, OWN_TEST, PENDING

MERGE = CommandSchema(
    "gh",
    operands=(Operand("words", count="*"),),
    options=(
        Option("repo", ("-R", "--repo")),
        Option("author-email", ("-A", "--author-email")),
        Option("body", ("-b", "--body")),
        Option("body-file", ("-F", "--body-file")),
        Option("subject", ("-t", "--subject")),
        Option("match-head-commit", ("--match-head-commit",)),
        Option("admin", ("--admin",), bool),
        *(Option(name, (f"-{name[0]}", f"--{name}"), bool) for name in ("delete-branch", "merge", "rebase", "squash")),
        *(Option(name, (f"--{name}",), bool) for name in ("auto", "disable-auto")),
    ),
)
CHECKS = """
query($url: URI!) {
  resource(url: $url) {
    ... on PullRequest {
      files(first: 100) { nodes { path } }
      commits(last: 1) { nodes { commit { checkSuites(first: 50) { nodes {
        app { slug }
        checkRuns(first: 50) { nodes { name status conclusion databaseId annotations(first: 50) { nodes { path location { start { line } } message } } } }
      } } } } }
    }
  }
}
"""
GH_TIMEOUT_SECONDS = 60
ALLOW_ESCAPES = "--allow-escape-sequences"
WORKFLOW_PATH = ".github"
PULL_URL = re.compile(r"^https://github\.com/(?P<repo>[\w.-]+/[\w.-]+)/pull/\d+$")
LOCATION = re.compile(r"(?P<path>[\w.@+-]+(?:/[\w.@+-]+)*\.\w+):(?P<line>\d+)")
LOG_STAMP = re.compile(r"^\S+Z (?:##\[\w+\])?")
ANSI = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")
OWN_RED = (
    "An admin merge never lands past a check this PR's own change turned red. Fix each finding below and push, then merge once those checks pass:\n"
    "```\n{findings}\n```"
)
INHERITED = "{checks} fail only on files outside this PR's diff, so they are inherited from the base branch and the admin merge goes ahead."


@on(
    Event.PreToolUse,
    only_if=[Tool("Bash"), Runs("gh")],
    tests={
        Input(command="gh pr merge 5 --squash --admin", commands=OWN_LINT): Block(pattern=r"own change turned red.*\n```\nlr-go lr/internal/lanes/sources\.go:336 Consider preallocating"),
        Input(command="gh -R acme/app pr merge 5 --admin --squash", commands=OWN_LINT): Block(pattern=r"lr-go lr/internal/lanes/sources\.go:336"),
        Input(command="gh pr merge 5 --squash --admin", commands=OWN_TEST): Block(pattern=r"lr-go lr/internal/lanes/sources_test\.go:42 sources_test\.go:42: got 2 messages, want 1"),
        Input(command="gh pr merge 5 --squash --admin", commands=INHERITED_LINT): Warn(pattern=r"^`lr-go` fail only on files outside this PR's diff, so they are inherited"),
        Input(command="gh pr merge 5 --squash --admin", commands=INHERITED_TEST): Warn(pattern=r"inherited from the base branch"),
        Input(command="gh pr merge 5 --squash --admin", commands=PENDING): Allow(),
        Input(command="gh pr merge 5 --squash --admin", commands=GREEN): Allow(),
        Input(command="gh pr merge 5 --squash", commands=OWN_LINT): Allow(),
        Input(command="gh pr view 5 --json url", commands=OWN_LINT): Allow(),
    },
)
def admin_merge_never_lands_past_its_own_red(evt: BaseHookEvent) -> HookResult | None:
    own: list[str] = []
    inherited: list[str] = []
    for call in evt.command.calls("gh"):
        merge = MERGE.bind(call).values
        if merge.get("words", ())[:2] != ("pr", "merge") or not any(merge.get("admin", ())):
            continue
        cwd = str(call.cwd or evt.cwd)
        url = gh(cwd, "pr", "view", *merge["words"][2:3], *(f"--repo={repo}" for repo in merge.get("repo", ()) if repo), "--json", "url", "--jq", ".url").strip()
        pr = json.loads(gh(cwd, "api", "graphql", "-f", f"query={CHECKS}", "-f", f"url={url}"))["data"]["resource"]
        files = {node["path"] for node in pr["files"]["nodes"]}
        for suite in pr["commits"]["nodes"][0]["commit"]["checkSuites"]["nodes"]:
            for run in suite["checkRuns"]["nodes"]:
                if run["status"] != "COMPLETED" or run["conclusion"] != "FAILURE":
                    continue
                found = [(note["path"], note["location"]["start"]["line"], note["message"].partition("\n")[0]) for note in run["annotations"]["nodes"] if note["path"] != WORKFLOW_PATH]
                if not found and suite["app"]["slug"] == "github-actions":
                    found = logged(cwd, url, run["databaseId"])
                named = [f"{run['name']} {file}:{line} {text}" for path, line, text in found if (file := changed(path, files))]
                own.extend(named)
                if not named:
                    inherited.append(f"`{run['name']}`")
    if own:
        return evt.block(OWN_RED.format(findings="\n".join(dict.fromkeys(own))))
    return evt.context(INHERITED.format(checks=", ".join(dict.fromkeys(inherited)))) if inherited else None


def logged(cwd: str, url: str, job: int) -> list[tuple[str, int, str]]:
    escapes = (ALLOW_ESCAPES,) if ALLOW_ESCAPES in gh(cwd, "api", "--help") else ()
    log = ANSI.sub("", gh(cwd, "api", f"repos/{PULL_URL.match(url)['repo']}/actions/jobs/{job}/logs", *escapes))
    return [(found["path"], int(found["line"]), LOG_STAMP.sub("", line).strip()) for line in log.splitlines() for found in LOCATION.finditer(line)]


def changed(path: str, files: set[str]) -> str | None:
    return next((file for file in sorted(files) if f"/{path}".endswith(f"/{file}") or f"/{file}".endswith(f"/{path}")), None)


def gh(cwd: str, *args: str) -> str:
    return subprocess.run(["gh", *args], capture_output=True, text=True, timeout=GH_TIMEOUT_SECONDS, cwd=cwd, env=dict(reqenv.env_map()), check=True).stdout
