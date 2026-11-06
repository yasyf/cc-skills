from __future__ import annotations

from captain_hook import Allow, BaseHookEvent, Block, Cmd, CommandSchema, Event, HookResult, Input, Operand, Option, on, Tool
from captain_hook.cmd import COMMAND_VALUE_FLAGS
from captain_hook.primitives.tap import launched, tapped

from .compaction_handoff import Bound
from .no_production_as_him import ROOT, SESSION, brief_naming, granted_lane, read

LANE = {"CLAUDE_LONG_RUNNING_LANE": "lane-a"}
GRANTS_READ = ["git", "config", "--global", "--get-all", "long-running.credentialWrite"]
GRANTED = {"git config": "lane-a=ssm:/ci/desk-host/cci-service-key\nlane-a=keychain:cci service\nlane-b=secretsmanager:prod/db\n"}
UNGRANTED = {"git config": (1, "")}
KEY_WRITE = "sprite exec -s desk -- cat service.pem | AWS_PROFILE=core-auto aws ssm put-parameter --name /ci/desk-host/cci-service-key --type SecureString --overwrite --value file:///dev/stdin"
AS_OWNER = "/bin/launchctl asuser 501 /usr/bin/sudo -H -u owner"
NOT_A_CREDENTIAL = frozenset({"String", "StringList"})
AWS_VALUES = (
    *COMMAND_VALUE_FLAGS["aws"],
    "--cli-input-json",
    "--cli-input-yaml",
    "--client-request-token",
    "--description",
    "--key-id",
    "--kms-key-id",
    "--value",
    "--allowed-pattern",
    "--tier",
    "--data-type",
    "--policies",
    "--tags",
    "--secret-string",
    "--secret-binary",
    "--version-stages",
    "--add-replica-regions",
    "--rotation-lambda-arn",
    "--rotation-rules",
    "--rotation-token",
    "--recovery-window-in-days",
)
AWS_SWITCHES = (
    "--debug",
    "--no-verify-ssl",
    "--no-paginate",
    "--no-sign-request",
    "--no-cli-pager",
    "--cli-auto-prompt",
    "--no-cli-auto-prompt",
    "--overwrite",
    "--no-overwrite",
    "--force-overwrite-replica-secret",
    "--no-force-overwrite-replica-secret",
    "--force-delete-without-recovery",
    "--no-force-delete-without-recovery",
    "--rotate-immediately",
    "--no-rotate-immediately",
)
PROMPTED_PASSWORD = ("-w",)
OPERATION = (Operand("operation"), Operand("more", count="*"))
AWS_OPTIONS = (Option("name", ("--name", "--names", "--secret-id")), Option("type", ("--type",)), Option("value", AWS_VALUES), Option("switch", AWS_SWITCHES, bool))
KEYCHAIN_OPTIONS = (Option("name", ("-s",)), Option("attribute", tuple(f"-{letter}" for letter in "acCdDGjlpPrtTwX")), Option("switch", ("-A", "-U"), bool))
WRITES = (
    ("ssm", CommandSchema("aws", subcommand=("ssm",), operands=OPERATION, options=AWS_OPTIONS), frozenset({"put-parameter", "delete-parameter", "delete-parameters"})),
    ("secretsmanager", CommandSchema("aws", subcommand=("secretsmanager",), operands=OPERATION, options=AWS_OPTIONS), frozenset({"create-secret", "put-secret-value", "update-secret", "delete-secret", "rotate-secret"})),
    ("keychain", CommandSchema("security", operands=OPERATION, options=KEYCHAIN_OPTIONS), frozenset({"add-generic-password", "add-internet-password", "delete-generic-password", "delete-internet-password"})),
)
REFUSAL = (
    "A lane writes an SSM SecureString, a Secrets Manager secret or a keychain item only when the owner's words name that write. "
    "Post a blocker to root naming the store and name; root records his grant with `git config --global --add long-running.credentialWrite <lane>=<store>:<name>`."
)


@on(  # capt-hook: no-escape root ordered a refusal, and a credential written over cannot be taken back
    Event.PreToolUse,
    only_if=[Bound(), Tool("Bash")],
    tests={
        Input(command=KEY_WRITE, env=LANE, commands=UNGRANTED): Block(pattern=r"^A lane writes an SSM SecureString"),
        Input(command="aws --profile core-auto ssm put-parameter --name /ci/desk-host/cci-service-key --overwrite --value v", env=LANE, commands=UNGRANTED): Block(),
        Input(command="cc-sudo run -- aws ssm put-parameter --name /ci/x --type SecureString --value v", env=LANE, commands=UNGRANTED): Block(),
        Input(command=f"cc-sudo run -- {AS_OWNER} /usr/bin/security add-generic-password -s 'cci service' -a lane -w", env=LANE, commands=UNGRANTED): Block(),
        Input(command="env AWS_PROFILE=core-auto aws ssm delete-parameter --name /ci/x", env=LANE, commands=UNGRANTED): Block(),
        Input(command="sudo -u owner aws secretsmanager put-secret-value --secret-id prod/db --secret-string v", env=LANE, commands=UNGRANTED): Block(),
        Input(command="aws secretsmanager rotate-secret --secret-id prod/db", env=LANE, commands=UNGRANTED): Block(),
        Input(command="bash -lc 'aws secretsmanager create-secret --name prod/db --secret-string v'", env=LANE, commands=UNGRANTED): Block(),
        Input(command="security delete-generic-password -s cci-service", env=LANE, commands=UNGRANTED): Block(),
        Input(command="aws ssm put-parameter --name /ci/x --type SecureString --value v", env=LANE, commands=GRANTED): Block(),
        Input(command='aws ssm put-parameter --name "$KEY" --type SecureString --value v', env=LANE, commands=GRANTED): Block(),
        Input(command="aws ssm delete-parameters --names /ci/desk-host/cci-service-key /ci/x", env=LANE, commands=GRANTED): Block(),
        Input(command="aws secretsmanager put-secret-value --secret-id prod/db --secret-string v", env=LANE, commands=GRANTED): Block(),
        Input(command=KEY_WRITE, env={"CLAUDE_LONG_RUNNING_LANE": "lane-c"}, commands=GRANTED): Block(),
        Input(command=KEY_WRITE, env=LANE, agent_id="a3", transcript=SESSION, commands=GRANTED): Block(),
        Input(command=KEY_WRITE, env={"CLAUDE_LONG_RUNNING_LANE": "", "CLAUDE_LONG_RUNNING_DRIVE": "d"}, commands={"git config": "=ssm:/ci/desk-host/cci-service-key\n"}): Block(),
        Input(command=KEY_WRITE, env=ROOT, agent_id="a2", transcript=SESSION, commands=GRANTED): Block(),
        Input(command=KEY_WRITE, env=ROOT, agent_id="a1", transcript=SESSION, commands=GRANTED): Allow(),
        Input(command=KEY_WRITE, env=ROOT, agent_id="a5", transcript=SESSION, file=brief_naming("lane-a"), commands=GRANTED): Allow(),
        Input(command=KEY_WRITE, env=ROOT, agent_id="a5", transcript=SESSION, file=brief_naming("lane-c"), commands=GRANTED): Block(),
        Input(command=KEY_WRITE, env=ROOT, agent_id="a5", transcript=SESSION, commands=GRANTED): Block(),
        Input(command=KEY_WRITE, env=ROOT, agent_id="a6", transcript=SESSION, file=brief_naming("lane-a"), commands=GRANTED): Block(),
        Input(command='KIND=Secure; aws ssm put-parameter --name /ci/x --type "${KIND}String" --value v', env=LANE, commands=UNGRANTED): Block(),
        Input(command="aws ssm --region us-east-1 delete-parameter --name /ci/x", env=LANE, commands=UNGRANTED): Block(),
        Input(command="bash -l -c 'aws ssm delete-parameter --name /ci/x'", env=LANE, commands=UNGRANTED): Block(),
        Input(command="security add-generic-password -a lane -s='cci service' -w v", env=LANE, commands=GRANTED): Block(),
        Input(command="security add-internet-password -a lane -s 'cci service' -P 443 -s other -w v", env=LANE, commands=GRANTED): Block(),
        Input(command="aws ssm put-parameter --name /ci/desk-host/cci-service-key --type SecureString --generate-cli-skeleton output --name /ci/x", env=LANE, commands=GRANTED): Block(),
        Input(command=KEY_WRITE, env=LANE, commands=GRANTED): Allow(),
        Input(command="security add-generic-password -a lane -s 'cci service' -w '-s=other' -U", env=LANE, commands=GRANTED): Allow(),
        Input(command="security add-generic-password -a lane -s 'cci service' -w", env=LANE, commands=GRANTED): Allow(),
        Input(command="aws secretsmanager put-secret-value --secret-id prod/db --secret-string v", env={"CLAUDE_LONG_RUNNING_LANE": "lane-b"}, commands=GRANTED): Allow(),
        Input(command="aws ssm get-parameter --name /ci/desk-host/cci-service-key --with-decryption", env=LANE, commands=UNGRANTED): Allow(),
        Input(command="aws ssm put-parameter --name /config/region --type String --value us-west-2 --overwrite", env=LANE, commands=UNGRANTED): Allow(),
        Input(command="aws ssm describe-parameters --parameter-filters Key=Name,Values=/ci/desk-host/cci-service-key", env=LANE, commands=UNGRANTED): Allow(),
        Input(command="security find-generic-password -s cci-service", env=LANE, commands=UNGRANTED): Allow(),
        Input(command="echo aws ssm put-parameter --name /ci/x --type SecureString", env=LANE, commands=UNGRANTED): Allow(),
        Input(command=KEY_WRITE, env={}, commands=UNGRANTED): Allow(),
    },
)
def credential_writes_need_his_words(evt: BaseHookEvent) -> HookResult | None:
    if not (writes := credential_writes(evt.command)):
        return None
    lane = granted_lane(evt)
    granted = {grant for line in read(GRANTS_READ, evt.cwd).splitlines() for named, _, grant in [line.partition("=")] if named == lane}
    return None if all(names and all(name is not None and f"{store}:{name}" in granted for name in names) for store, names in writes) else evt.block(REFUSAL)


def credential_writes(cmd: Cmd) -> list[tuple[str, tuple[str | None, ...]]]:
    writes = []
    for call in cmd.calls():
        if (inner := tapped(call) or launched(call)) is not None:
            writes += credential_writes(inner)
        for store, schema, verbs in WRITES:
            if call.name != schema.program or not schema.invokes(call):
                continue
            bound = schema.bind(call)
            operation = (bound.values.get("operation") or (None,))[0]
            if operation not in verbs or (bound.values.get("type") or (None,))[-1] in NOT_A_CREDENTIAL:
                continue
            stopped = bool(bound.unread) and tuple(word.value for word in bound.unread) != PROMPTED_PASSWORD
            listed = bound.values.get("more", ()) if operation == "delete-parameters" else ()
            writes.append((store, () if stopped else (*bound.values.get("name", ()), *listed)))
    return writes
