from __future__ import annotations

import re
import subprocess
from pathlib import Path

from captain_hook import Allow, BaseHookEvent, Block, Event, HookResult, Input, on, Tool, Warn
from captain_hook.util import reqenv

from .call_dir import unknown_dir
from .compaction_handoff import Bound, script_module
from .pr_ledger import response_text

LANE = {"CLAUDE_LONG_RUNNING_LANE": "lane-a"}
TOKEN = re.compile(r"\b(?:gh[pousr]_[A-Za-z0-9]{20,}|github_pat_\w{20,}|sk-[\w-]{20,}|xox[abprs]-[\w-]{10,}|(?:AKIA|ASIA)[0-9A-Z]{16})\b")
READER = re.compile(r"\bgh auth token\b|\bsecurity find-generic-password\b[^|;]*\s-w\b")
INTO_A_SHELL = re.compile(r"\|\s*(?:(?:sprite exec|ssh)\b[^|]*?\s)?(?:(?:/\S+/)?(?:ba|z|da)?sh)(?:\s|$)")
ON_THE_LINE = "A credential on a command line reaches the transcript and the process list. Read it from a 0600 file or an env var, and if it is real post a blocker to meta-desk and owner-relay without the value."
INTO_SHELL = "A shell runs what it reads, so a credential piped into one prints in an error. Write it to a 0600 file or send it to `curl --data-binary @-` instead."
PRINTED = "This output holds a credential, now in your transcript. Post a blocker to meta-desk and owner-relay without the value, naming which credential and where it went; rotating it is the owner's call."
FAKE = "gho_" + "a" * 36
GREP_SECONDS = 10
GREP = ["git", "grep", "-lF", "--untracked", "-f", "-"]
ENV_HOLDING_KINDS = frozenset(
    {"all", "configmap", "configmaps", "cm", "cronjob", "cronjobs", "cj", "daemonset", "daemonsets", "ds", "deployment", "deployments", "deploy", "job", "jobs", "pod", "pods", "po", "replicaset", "replicasets", "rs", "secret", "secrets", "statefulset", "statefulsets", "sts"}
)
WHOLE_OBJECT_OUTPUT = re.compile(r"(?:^|\s)(?:-o[=\s]?|--output[=\s])(?:json|yaml)(?:\s|$)")
ENV_DUMP = re.compile(r"(?:^|[\s;|&(])(?:env(?!\s+\w+=)(?=\s|$|[;|&)])|printenv\b)|/environ\b")
DEVICE_CODE = re.compile(r"\b[A-Z0-9]{4}-[A-Z0-9]{4}\b")
AWS_DEVICE_LINK = re.compile(r"awsapps\.com/start/#/device|device\.sso\.[\w-]+\.amazonaws\.com")
DEVICE_LINK = re.compile(rf"{AWS_DEVICE_LINK.pattern}|github\.com/login/device|microsoft\.com/devicelogin")
RECORD_WRITES = (("cci", "post"), ("ccn", "doc", "add"))
DEVICE_SIGN_IN = (
    "A device code and its sign-in link are a credential until they expire, and a record keeps both. "
    "Send them to him on a card with `lr ask-owner`, and post only that a sign-in waits on him."
)
AWS_SIGN_IN = (
    "An AWS read never waits on his sign-in, and a device code in a record is a credential. "
    "Run it with `AWS_PROFILE` set to `$CI_AWS_PROFILE` for the CI account or `$DEVBOX_AWS_PROFILE` for plat-dev, and card the IaC PR for any grant that profile lacks."
)
SIGN_IN_LINK = "https://d-0000000000.awsapps.com/start/#/device"
GITHUB_SIGN_IN_LINK = "https://github.com/login/device"
WHOLE_OBJECT = "A whole object or an env dump prints every env value, and a pod env var can hold a token, which a transcript cannot give back. Read the fields you need with `kubectl get <kind> <name> -o jsonpath=...`, or with custom-columns, name or wide."


@on(  # capt-hook: no-escape the owner ordered a refusal, and a printed credential cannot be taken back
    Event.PreToolUse,
    only_if=[Bound(), Tool("Bash")],
    tests={
        Input(command=f"curl -H 'Authorization: token {FAKE}' https://api.github.com", env=LANE): Block(pattern=r"^A credential on a command line"),
        Input(command=f"cci post --text 'the value is {FAKE}'", env=LANE): Block(),
        Input(command="{ gh auth token; cat script.sh; } | sprite exec -s desk -- bash -s", env=LANE): Block(pattern=r"^A shell runs what it reads"),
        Input(command="gh auth token | bash", env=LANE): Block(),
        Input(command="security find-generic-password -s key -w | ssh box sh", env=LANE): Block(),
        Input(command="gh auth token | sprite exec -s desk -- curl -sS --data-binary @- http://localhost/creds", env=LANE): Allow(),
        Input(command="gh auth token > creds && chmod 600 creds", env=LANE): Allow(),
        Input(command="cat script.sh | sprite exec -s desk -- bash -s", env=LANE): Allow(),
        Input(command="git log --grep gho_", env=LANE): Allow(),
        Input(command="gh auth token | bash", env={}): Allow(),
    },
)
def no_credential_on_a_shell(evt: BaseHookEvent) -> HookResult | None:
    return evt.block(why) if (why := refusal(evt.command.raw)) else None


def refusal(line: str) -> str | None:
    if TOKEN.search(line):
        return ON_THE_LINE
    return INTO_SHELL if READER.search(line) and INTO_A_SHELL.search(line) else None


@on(
    Event.PostToolUse,
    only_if=[Bound(), Tool("Bash")],
    tests={
        Input(command="bash script.sh", env=LANE, output=f"bash: line 1: {FAKE}: command not found", commands={"git grep": (1, "")}): Warn(pattern=r"^This output holds a credential"),
        Input(command="rg -n gho_ tests", env=LANE, output=f"tests/test_hold.py:176: {FAKE}", commands={"git grep": "tests/test_hold.py\n"}): Allow(),
        Input(command="cat docs/notes.md", env=LANE, output=FAKE, commands={"git grep": "docs/notes.md\n"}): Warn(pattern=r"^This output holds a credential"),
        Input(command=". /env.sh; cd $A; rg -n gho_ tests", env=LANE, output=FAKE, commands={"git grep": (1, "")}): Warn(pattern=r"^This output holds a credential"),
        Input(command="git status", env=LANE, output="clean"): Allow(),
    },
)
def a_printed_credential_is_reported(evt: BaseHookEvent) -> HookResult | None:
    return evt.context(PRINTED) if any(not committed_fixture(evt, token) for token in set(TOKEN.findall(response_text(evt.tool_response)))) else None


def read_dirs(evt: BaseHookEvent) -> set[str]:
    calls = evt.command.calls()
    named = (Path(arg) for call in calls for arg in call.args if arg.startswith("/"))
    return {str(call.cwd or evt.cwd) for call in calls if not unknown_dir(evt, call)} | {str(path if path.is_dir() else path.parent) for path in named if path.exists()}


def committed_fixture(evt: BaseHookEvent, token: str) -> bool:
    tests = script_module("long_running.testfiles").TEST_FILE
    return any(tests.search(name) for directory in read_dirs(evt) for name in files_holding(token, directory))


def files_holding(token: str, directory: str) -> list[str]:
    try:
        return subprocess.run(GREP, input=token, capture_output=True, text=True, cwd=directory, timeout=reqenv.clamp_timeout(GREP_SECONDS)).stdout.splitlines()
    except (OSError, subprocess.TimeoutExpired):
        return []


@on(  # capt-hook: no-escape the owner ordered a refusal, and a printed credential cannot be taken back
    Event.PreToolUse,
    only_if=[Bound(), Tool("Bash")],
    tests={
        Input(command="kubectl get pod runner -o json", env=LANE): Block(pattern=r"^A whole object or an env dump"),
        Input(command="kubectl -n plat --context prod get pods -oyaml", env=LANE): Block(),
        Input(command="kubectl get deploy.apps/api --output=json | jq .spec", env=LANE): Block(),
        Input(command="kubectl get sts,svc --output yaml", env=LANE): Block(),
        Input(command="kubectl get secret api-secrets -o=yaml", env=LANE): Block(),
        Input(command="kubectl get cm,cj,ds,job,rs -o json", env=LANE): Block(),
        Input(command="/opt/homebrew/bin/kubectl get all -o yaml", env=LANE): Block(),
        Input(command="kubectl describe pod runner", env=LANE): Block(),
        Input(command="kubectl --context prod describe deployment/api", env=LANE): Block(),
        Input(command="kubectl exec runner -- env", env=LANE): Block(),
        Input(command="kubectl exec -it runner -c main -- printenv API_KEY", env=LANE): Block(),
        Input(command="kubectl exec runner -- sh -c 'env | grep KEY'", env=LANE): Block(),
        Input(command="kubectl exec runner -- cat /proc/1/environ", env=LANE): Block(),
        Input(command="kubectl get pod runner -o jsonpath='{.status.phase}'", env=LANE): Allow(),
        Input(command="kubectl get pods -o custom-columns=NAME:.metadata.name", env=LANE): Allow(),
        Input(command="kubectl get pods -o name", env=LANE): Allow(),
        Input(command="kubectl get pods -o wide", env=LANE): Allow(),
        Input(command="kubectl get pods", env=LANE): Allow(),
        Input(command="kubectl get svc api -o yaml", env=LANE): Allow(),
        Input(command="kubectl get nodes -o json", env=LANE): Allow(),
        Input(command="kubectl describe node worker-1", env=LANE): Allow(),
        Input(command="kubectl logs runner", env=LANE): Allow(),
        Input(command="kubectl exec -c env runner -- ls /etc/env", env=LANE): Allow(),
        Input(command="kubectl exec runner -- env FOO=bar ./run", env=LANE): Allow(),
        Input(command="echo 'kubectl get pod runner -o json'", env=LANE): Allow(),
        Input(command="kubectl get pod runner -o json", env={}): Allow(),
    },
)
def no_whole_object_kubectl_reads(evt: BaseHookEvent) -> HookResult | None:
    return evt.block(WHOLE_OBJECT) if any(prints_env_values(call.args) for call in evt.command.calls("kubectl")) else None


def prints_env_values(args: tuple[str, ...]) -> bool:
    verb = next((word for word in args if word in {"get", "describe", "exec"}), None)
    if verb is None:
        return False
    after = args[args.index(verb) + 1 :]
    if verb == "exec":
        return bool(ENV_DUMP.search(" ".join(after[after.index("--") + 1 :] if "--" in after else after)))
    names_kind = any(kind.split(".")[0] in ENV_HOLDING_KINDS for word in after if not word.startswith("-") for kind in word.split("/")[0].split(","))
    return names_kind and (verb == "describe" or bool(WHOLE_OBJECT_OUTPUT.search(" ".join(args))))


@on(
    Event.PreToolUse,
    only_if=[Bound(), Tool("Bash")],
    tests={
        Input(command=f"cci post --kind blocker --to meta-desk --text 'sign in at {SIGN_IN_LINK} with code WXYZ-QRST'", env=LANE): Warn(pattern=r"^An AWS read never waits on his sign-in"),
        Input(command=f"ccn doc add --title progress --body 'SSO waits: {SIGN_IN_LINK}, code WXYZ-QRST'", env=LANE): Warn(pattern=r"DEVBOX_AWS_PROFILE"),
        Input(command=f"cci post --kind blocker --to meta-desk --text 'sign in at {GITHUB_SIGN_IN_LINK} with code WXYZ-QRST'", env=LANE): Warn(pattern=r"^A device code and its sign-in link"),
        Input(command="cci post --kind note --to meta-desk --text 'ENG-1234 tracks it; Slack ref ABCD-EFGH'", env=LANE): Allow(),
        Input(command=f"cci post --kind blocker --to meta-desk --text 'a sign-in at {GITHUB_SIGN_IN_LINK} waits on him'", env=LANE): Allow(),
        Input(command=f"echo 'sign in at {SIGN_IN_LINK} with code WXYZ-QRST'", env=LANE): Allow(),
        Input(command=f"cci post --text 'sign in at {SIGN_IN_LINK} with code WXYZ-QRST'", env={}): Allow(),
    },
)
def device_codes_stay_off_records(evt: BaseHookEvent) -> HookResult | None:
    writes = [" ".join(call.args) for call in evt.command.calls() if any((call.name, *call.args[: len(verb) - 1]) == verb for verb in RECORD_WRITES)]
    held = [text for text in writes if DEVICE_CODE.search(text) and DEVICE_LINK.search(text)]
    if not held:
        return None
    return evt.context(AWS_SIGN_IN if any(AWS_DEVICE_LINK.search(text) for text in held) else DEVICE_SIGN_IN)
