from __future__ import annotations

import json
import re
import subprocess
from collections.abc import Iterator
from dataclasses import dataclass
from functools import cached_property
from pathlib import Path

from captain_hook import Allow, BaseHookEvent, Block, Call, Cmd, Event, HookResult, Input, ast_grep, on, Tool
from captain_hook.util import reqenv

from .compaction_handoff import Bound
from .gate5_release import OWNER_LANES

LANE = {"CLAUDE_LONG_RUNNING_LANE": "lane-a"}
READ_SECONDS = 5
DEPTH = 3
CONFIG_READ = ["git", "config", "--global", "--get-regexp", r"^long-running\.production"]
CONTEXT_READ = ["kubectl", "config", "current-context"]
KUBE_CONTEXT, KUBE_NAMESPACE, DB_HOST = "long-running.productionkubecontext", "long-running.productionkubenamespace", "long-running.productiondbhost"
SHELLS = frozenset({"bash", "sh", "zsh", "dash"})
SCRIPT_SUFFIXES = frozenset({".sh", ".bash", ".zsh"})
SSH_VALUE_FLAGS = frozenset(f"-{letter}" for letter in "BbcDEeFIiJLlmOoPpQRSWw")
SPRITE_VALUE_FLAGS = frozenset({"-s", "--sprite", "-o", "--org"})
AWS_VALUE_FLAGS = frozenset({"--profile", "--region", "--output", "--query", "--endpoint-url", "--color", "--cli-read-timeout", "--cli-connect-timeout", "--ca-bundle", "--cli-binary-format"})
SSM_READS = frozenset({"get-parameter", "get-parameters", "get-parameters-by-path"})
SECRET_READS = frozenset({"get-secret-value", "batch-get-secret-value"})
KEYCHAIN_LOOKUPS = frozenset({"find-generic-password", "find-internet-password"})
OP_VALUE_FLAGS = frozenset({"--account", "--vault", "--fields", "--format", "--session", "--config", "--cache"})
OP_REVEALS = frozenset({"--reveal", "--fields"})
KUBECTL_VALUE_FLAGS = frozenset({"-n", "--namespace", "--context", "-c", "--container", "--kubeconfig", "--cluster", "--user", "-s", "--server", "--pod-running-timeout", "--address"})
POD_VERBS = frozenset({"exec", "cp", "port-forward"})
BK_VALUE_FLAGS = frozenset({"-X", "--method", "-p", "--pipeline", "-m", "--message", "-b", "--branch", "-c", "--commit", "-e", "--env", "-d", "--data", "-f", "--file"})
DB_READ_PIPELINE = re.compile(r"(?:^|/)db-read(?:/|$)")
TABLE = re.compile(r"\b(?:from|join)\s+([\w.\"]+)", re.IGNORECASE)
CONNINFO_HOST = re.compile(r"(?:^|\s)host=(\S+)|^postgres(?:ql)?://(?:[^@/]*@)?([^:/?]+)")
REFUSAL = (
    "A production secret, pod or database read needs the owner's tap on that exact read. "
    "Ask him with `lr ask-owner` naming the parameter, pod or table, then rerun with `# ccx:owner-approval=<seq>` citing owner-relay's owner record."
)
PRODUCTION = {"git config": f"{KUBE_CONTEXT} plat-usw2-prod\n{KUBE_NAMESPACE} plat-prod\n{DB_HOST} prod-db.internal\n", "kubectl config": "plat-usw2-staging\n"}
CURRENT_PROD = PRODUCTION | {"kubectl config": "plat-usw2-prod\n"}
POD_EXEC = "kubectl --context plat-usw2-prod exec api-runtime-0 -- ls /traces"
DB_READ = 'ci db-read --sql "SELECT id FROM iris_sessions" --reason count'


def approval(text: str, lane: str = "owner-relay", kind: str = "owner") -> dict[str, object]:
    return PRODUCTION | {"cci show 491400 --json": json.dumps({"lane": lane, "kind": kind, "text": text[:40]}), "cci show 491400 --body": text}


@on(  # capt-hook: no-escape the owner ordered a refusal, and only his record naming that exact read lets it through
    Event.PreToolUse,
    only_if=[Bound(), Tool("Bash")],
    tests={
        Input(command="kubectl --context plat-usw2-prod -n plat exec -it api-runtime-0 -- sh", env=LANE, commands=PRODUCTION): Block(pattern=r"^A production secret, pod or database read"),
        Input(command="kubectl exec api-runtime-0 -- cat /traces | ssh pass-1 'cat > /tmp/traces'", env=LANE, commands=CURRENT_PROD): Block(),
        Input(command="kubectl -n plat-prod cp api-runtime-0:/tmp/traces ./traces", env=LANE, commands=PRODUCTION): Block(),
        Input(command="kubectl --context=plat-usw2-prod port-forward pod/api-runtime-0 5432:5432", env=LANE, commands=PRODUCTION): Block(),
        Input(command="ssh -i ~/.ssh/pass pass-1 'aws ssm get-parameter --name /app/forge/api/turbopuffer_api_key --with-decryption --profile plat-prod'", env=LANE, commands=PRODUCTION): Block(),
        Input(command="ssh pass-1 \"bash -c 'aws ssm get-parameters --names /a /b --with-decryption'\"", env=LANE, commands=PRODUCTION): Block(),
        Input(command="sprite exec -s desk -- aws secretsmanager get-secret-value --secret-id prod/db", env=LANE, commands=PRODUCTION): Block(),
        Input(command="nsc ssh box-1 -- security find-generic-password -s cci-service -w", env=LANE, commands=PRODUCTION): Block(),
        Input(command="ssh pass-1 <<'EOF'\nop read op://Private/turbopuffer/key\nEOF", env=LANE, commands=PRODUCTION): Block(),
        Input(command="ssh pass-1 'op item get Turbopuffer --fields credential --reveal'", env=LANE, commands=PRODUCTION): Block(),
        Input(command=f"cat > /tmp/stream.sh <<'EOF'\n{POD_EXEC}\nEOF\nbash /tmp/stream.sh", env=LANE, commands=PRODUCTION): Block(),
        Input(command=DB_READ, env=LANE, commands=PRODUCTION): Block(),
        Input(command="tools/ci bk api -X POST /organizations/forge/pipelines/db-read/builds", env=LANE, commands=PRODUCTION): Block(),
        Input(command="bk build create --pipeline forge/db-read --env SQL='select id from teams'", env=LANE, commands=PRODUCTION): Block(),
        Input(command="PGHOST=prod-db.internal psql -c 'select id from users'", env=LANE, commands=PRODUCTION): Block(),
        Input(command="psql postgres://reader@prod-db.internal:5432/forge -c 'select * from teams'", env=LANE, commands=PRODUCTION): Block(),
        Input(command=f"{POD_EXEC}  # ccx:owner-approval=491400", env=LANE, commands=approval("Owner tapped: exec into api-runtime-1 once")): Block(),
        Input(command=f"{POD_EXEC}  # ccx:owner-approval=491400", env=LANE, commands=approval("exec into api-runtime-0", lane="meta-desk")): Block(),
        Input(command=f"{POD_EXEC}  # ccx:owner-approval=491400", env=LANE, commands=approval("exec into api-runtime-0", kind="note")): Block(),
        Input(command=f"{POD_EXEC}  # ccx:owner-approval=491400", env=LANE, commands=approval("Owner tapped: exec into api-runtime-0 for the trace stream")): Allow(),
        Input(command=f"{DB_READ}  # ccx:owner-approval=491400", env=LANE, commands=approval("Owner tapped: one db-read count on iris_sessions")): Allow(),
        Input(command="kubectl --context plat-usw2-staging exec api-runtime-0 -- ls", env=LANE, commands=PRODUCTION): Allow(),
        Input(command="kubectl exec api-runtime-0 -- ls", env=LANE, commands=PRODUCTION): Allow(),
        Input(command="kubectl --context plat-usw2-prod logs api-runtime-0", env=LANE, commands=PRODUCTION): Allow(),
        Input(command="kubectl --context plat-usw2-prod get pods", env=LANE, commands=PRODUCTION): Allow(),
        Input(command="ssh pass-1 'aws ssm get-parameter --name /config/region'", env=LANE, commands=PRODUCTION): Allow(),
        Input(command="ssh pass-1 'aws ssm describe-parameters --profile plat-prod'", env=LANE, commands=PRODUCTION): Allow(),
        Input(command="ssh pass-1 'security find-generic-password -s cci-service'", env=LANE, commands=PRODUCTION): Allow(),
        Input(command="aws sso login --profile plat-prod", env=LANE, commands=PRODUCTION): Allow(),
        Input(command="ssh pass-1 'aws sso login --profile plat-prod && desk-creds'", env=LANE, commands=PRODUCTION): Allow(),
        Input(command="ci break-glass --reason 'apply the blocked stack'", env=LANE, commands=PRODUCTION): Allow(),
        Input(command="bk build create --pipeline forge/monorepo", env=LANE, commands=PRODUCTION): Allow(),
        Input(command="psql -h localhost -c 'select id from users'", env=LANE, commands=PRODUCTION): Allow(),
        Input(command=f"echo '{POD_EXEC}'", env=LANE, commands=PRODUCTION): Allow(),
        Input(command=POD_EXEC, env=LANE, commands={"git config": (1, "")}): Allow(),
        Input(command=POD_EXEC, env={}, commands=PRODUCTION): Allow(),
    },
)
def production_reads_need_his_record(evt: BaseHookEvent) -> HookResult | None:
    production = Production(evt.cwd)
    direct = [found for call in evt.command.calls() if (found := targets(call, production, nested=False))]
    reached = [found for cmd in reached_commands(evt.command, evt.cwd) for call in cmd.calls() if (found := targets(call, production, nested=True))]
    if not (named := [target for found in (*direct, *reached) for target in found]):
        return None
    return None if approved(evt.annotations.get("owner-approval"), named, evt.cwd) else evt.block(REFUSAL)


@dataclass
class Production:
    cwd: str

    @cached_property
    def config(self) -> dict[str, set[str]]:
        entries: dict[str, set[str]] = {}
        for line in read(CONFIG_READ, self.cwd).splitlines():
            key, _, value = line.partition(" ")
            entries.setdefault(key.lower(), set()).add(value.strip())
        return entries

    @cached_property
    def current_context(self) -> str:
        return read(CONTEXT_READ, self.cwd).strip()

    def names(self, key: str, value: str | None) -> bool:
        return bool(value) and value in self.config.get(key, set())


def targets(call: Call, production: Production, *, nested: bool) -> tuple[str, ...] | None:
    return (secret_targets(call) if nested else None) or pod_targets(call, production, nested=nested) or db_targets(call, production)


def secret_targets(call: Call) -> tuple[str, ...] | None:
    args = call.args
    match call.name:
        case "aws":
            service = operands(args, AWS_VALUE_FLAGS)[:2]
            if service[:1] == ["ssm"] and service[1:] and service[1] in SSM_READS and "--with-decryption" in args:
                return named_or_whole(values(args, "--name", "--names", "--path"), args)
            if service[:1] == ["secretsmanager"] and service[1:] and service[1] in SECRET_READS:
                return named_or_whole(values(args, "--secret-id", "--secret-id-list"), args)
        case "security" if args[:1] and args[0] in KEYCHAIN_LOOKUPS and {"-w", "-g"} & set(args):
            return named_or_whole(values(args, "-s", "-l", "-a")[:1], args)
        case "op":
            words = operands(args, OP_VALUE_FLAGS)
            if words[:1] == ["read"]:
                return named_or_whole(tuple(words[1:2]), args)
            if words[:2] == ["item", "get"] and any(arg.partition("=")[0] in OP_REVEALS for arg in args):
                return named_or_whole(tuple(words[2:3]), args)
    return None


def pod_targets(call: Call, production: Production, *, nested: bool) -> tuple[str, ...] | None:
    if call.name != "kubectl":
        return None
    words = operands(call.args, KUBECTL_VALUE_FLAGS)
    if not words or words[0] not in POD_VERBS:
        return None
    context = next(iter(values(call.args, "--context")), None) or (None if nested else production.current_context)
    namespace = next(iter(values(call.args, "-n", "--namespace")), None)
    if not production.names(KUBE_CONTEXT, context) and not production.names(KUBE_NAMESPACE, namespace):
        return None
    pods = [word.partition(":")[0].rpartition("/")[2] for word in words[1:] if words[0] != "cp" or ":" in word]
    return named_or_whole(tuple(pods[:1]), call.args)


def db_targets(call: Call, production: Production) -> tuple[str, ...] | None:
    args = call.args
    words = operands(args, BK_VALUE_FLAGS)
    if call.name == "ci" and words[:1] == ["db-read"]:
        return tables(values(args, "--sql")) or ("db-read",)
    if call.name in {"ci", "bk"} and "api" in words[:2] and "POST" in values(args, "-X", "--method") and any(DB_READ_PIPELINE.search(word) for word in words):
        return tables(args) or ("db-read",)
    if call.name == "bk" and words[:2] == ["build", "create"] and any(DB_READ_PIPELINE.search(pipeline) for pipeline in values(args, "-p", "--pipeline")):
        return tables(args) or ("db-read",)
    if call.name != "psql":
        return None
    hosts = {*values(args, "-h", "--host"), dict(call.source.env).get("PGHOST", ""), *(group for arg in args for found in CONNINFO_HOST.finditer(arg) for group in found.groups() if group)}
    production_hosts = sorted(host for host in hosts if production.names(DB_HOST, host))
    return (tables(values(args, "-c", "--command")) or tuple(production_hosts)) if production_hosts else None


def tables(texts: tuple[str, ...]) -> tuple[str, ...]:
    return tuple(dict.fromkeys(name.strip('"').lower() for text in texts for name in TABLE.findall(text)))


def named_or_whole(named: tuple[str, ...], args: tuple[str, ...]) -> tuple[str, ...]:
    return named or (" ".join(args),)


def operands(args: tuple[str, ...], value_flags: frozenset[str]) -> list[str]:
    words, skip = [], False
    for arg in args:
        if arg == "--":
            break
        if skip:
            skip = False
        elif arg.startswith("-"):
            skip = arg in value_flags
        else:
            words.append(arg)
    return words


def values(args: tuple[str, ...], *flags: str) -> tuple[str, ...]:
    found: list[str] = []
    for index, arg in enumerate(args):
        flag, equals, value = arg.partition("=")
        if flag not in flags:
            continue
        if equals:
            found.append(value)
            continue
        for following in args[index + 1 :]:
            if following.startswith("-"):
                break
            found.append(following)
    return tuple(found)


def reached_commands(cmd: Cmd, cwd: str, depth: int = 0) -> Iterator[Cmd]:
    if depth == DEPTH:
        return
    heredocs = (node.text for node in ast_grep.find_kinds(cmd.raw, "bash", frozenset({"heredoc_body"})))
    for text in (*heredocs, *(remote_command(call) for call in cmd.calls()), *(script_text(call, cwd) for call in cmd.calls())):
        if text and (inner := Cmd.parse(text)) is not None:
            yield inner
            yield from reached_commands(inner, cwd, depth + 1)


def remote_command(call: Call) -> str | None:
    args = call.args
    if "--" in args and (call.name, *args[:1]) in {("sprite", "exec"), ("nsc", "ssh")}:
        return " ".join(args[args.index("--") + 1 :])
    if call.name == "ssh":
        return " ".join(after_options(args, SSH_VALUE_FLAGS)[1:])
    if call.name == "sprite" and args[:1] == ("exec",):
        return " ".join(after_options(args[1:], SPRITE_VALUE_FLAGS))
    if call.name == "nsc" and args[:1] == ("ssh",):
        return " ".join(after_options(args[1:], frozenset())[1:])
    return None


def after_options(args: tuple[str, ...], value_flags: frozenset[str]) -> tuple[str, ...]:
    index = 0
    while index < len(args) and args[index].startswith("-"):
        if args[index] == "--":
            return args[index + 1 :]
        index += 2 if args[index] in value_flags else 1
    return args[index:]


def script_text(call: Call, cwd: str) -> str | None:
    named = next((arg for arg in call.args if not arg.startswith("-")), None) if call.name in SHELLS else call.command.executable
    if not named or any(char.isspace() for char in named) or (call.name not in SHELLS and Path(named).suffix not in SCRIPT_SUFFIXES):
        return None
    path = Path(named) if Path(named).is_absolute() else Path(call.cwd or cwd, named)
    return path.read_text(errors="replace") if path.is_file() else None


def approved(seq: str | None, named: list[str], cwd: str) -> bool:
    if not seq:
        return False
    record = json.loads(next(iter(read(["cci", "show", seq, "--json"], cwd).splitlines()), "{}"))
    if record.get("lane") not in OWNER_LANES or record.get("kind") != "owner":
        return False
    text = read(["cci", "show", seq, "--body", "--budget", "0"], cwd).lower()
    return all(target.lower() in text for target in named)


def read(argv: list[str], cwd: str) -> str:
    try:
        return subprocess.run(argv, capture_output=True, text=True, cwd=cwd, timeout=reqenv.clamp_timeout(READ_SECONDS), env=dict(reqenv.env_map())).stdout
    except (OSError, subprocess.TimeoutExpired):
        return ""
