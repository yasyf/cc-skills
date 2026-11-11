import argparse
import contextlib
import io
import json
import os
import shlex
import subprocess

from drive import cli, limits, state, talk
from drive.state import DriveError

UNTYPED = {"help", "drive", "json"}
OPS = {"order": ("add", "set", "posted"), "job": ("add", "set", "brief"), "pr": ("add", "set"), "incident": ("open", "stop", "freeze", "release")}
POST_SECONDS = 60
DRY_RUN = {"type": "boolean", "description": "print the drive command or record each op would run, and run nothing"}
OPS_HELP = (
    "{lead} Runs the ops in order, each one verb with its flags as fields, and returns one line per op. A refusal stops"
    " the call, keeps the lines of the ops that ran, and names the op, its fix and the ops left unrun: make the fix and"
    " send those ops again."
)
LEADS = {
    "order": "His orders: add records one from his words, set changes one, posted records the communicator's post on one.",
    "job": "The drive's jobs: add starts one on an order's parts, set changes one, brief writes its brief.",
    "pr": "The drive's PRs: add records one a job opened, set changes one.",
    "incident": (
        "Incidents: open adds his order as one, with its fix and diagnosis jobs and its evidence record; stop adds the job"
        " that turns the failing path off; freeze holds the service's other releases; release reopens the stop when a"
        " release starts."
    ),
}
TEXT = {"type": "string"}
RECORD = {
    "type": "object",
    "properties": {
        "kind": {"enum": ["owner", "answer", "defect"], "description": "a report, ready or ask goes through cci post instead, whose hook reads it"},
        "text": {"type": "string", "description": "his words verbatim for owner, the tapped label or his words for answer, the refusal verbatim for defect"},
        "url": {"type": "string", "description": "his message's or the card's permalink"},
        "thread": {"type": "string", "description": "the thread it is about, kept as attr thread"},
        "waits": {"type": "string", "description": "the tapped question card's waits id, <drive>:<j>-<seq>: answers ask <seq> with attr job=<j>"},
        "undone": {"type": "boolean", "description": "an Undo tap: attr undone=<text> and the text undone: <text>"},
        "ruling": {"type": "string", "description": "a ruling's cc-notes answer id, as attr ruling"},
        "re": {"type": "array", "items": TEXT, "description": "seqs this answers, when no waits id names one"},
        "resolves": {"type": "array", "items": TEXT, "description": "seqs this closes"},
        "topic": {"type": "string", "description": "a pairing key, such as card:<thread_ts> for a card with no ask record"},
        "to": {
            "type": "string",
            "description": (
                "the lane it goes to in place of the other session's; the communicator's owner or answer record goes to the"
                " coordinator session hearing the order of the job it answers, else of its thread's newest order, whatever"
                " lane this names"
            ),
        },
        "attrs": {"type": "object", "additionalProperties": TEXT},
        "body": {"type": "string", "description": "the rest past the text's one line"},
    },
    "required": ["kind", "text"],
    "additionalProperties": False,
}


def verb_tools() -> list[dict]:
    """The drive verbs the MCP server serves beside say, each typed from the CLI's own parser."""
    job_add = schema(leaf("job", "add"))
    job_add["properties"]["order"] = {
        "anyOf": [TEXT, schema(leaf("order", "add"))],
        "description": "the order's id, or the fields of drive order add, which adds that order first and takes its id",
    }
    return [
        {
            "name": "heard",
            "description": (
                "Record that he wrote, each time he does. Returns heard, away when away is set, or, when he is back from"
                " away, CATCH-UP since <time>: <ids>, the orders his catch-up covers; given his thread, it adds the lane"
                " his owner record goes to when that thread's newest order has a sub-coordinator."
            ),
            "inputSchema": {"type": "object", "properties": {**schema(leaf("heard"))["properties"], "dry_run": DRY_RUN}},
        },
        {
            "name": "record",
            "description": (
                "Send his words (owner), his answer to a card (answer) or a tool's refusal (defect) to the drive's other"
                " session in one call, as this session's role. A question card he tapped takes waits, which fills re with"
                " the ask's seq and attr job; an Undo tap sets undone. A defect's text past one 120-character line goes as"
                " that line cut at a word, the whole text atop its body. Returns each record's seq."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {"records": {"type": "array", "minItems": 1, "items": RECORD}, "dry_run": DRY_RUN},
                "required": ["records"],
            },
        },
        *(ops_tool(group, {"add": job_add} if group == "job" else {}) for group in OPS),
        {
            "name": "status",
            "description": "Read where each order, job and PR stands, as drive status prints it: the ones named, or every open order.",
            "inputSchema": {"type": "object", "properties": {**schema(leaf("status"))["properties"], "dry_run": DRY_RUN}},
            "annotations": {"readOnlyHint": True},
        },
    ]


def run(tool: str, name: str, arguments: dict) -> str:
    if tool == "say":
        return talk.say(name, arguments)
    dry_run = arguments.pop("dry_run", False)
    match tool:
        case "record":
            return record(name, listing(arguments, "records", "owner, answer or defect records"), dry_run)
        case "heard":
            return batch(name, (tool,), [{tool: arguments}], dry_run)
        case "status":
            said: list[str] = []
            return "\n".join(ran(name, said, 1, 1, (tool,), arguments, dry_run) or said)
        case group:
            return batch(name, (group,), listing(arguments, "ops", ", ".join(OPS[group])), dry_run)


def listing(arguments: dict, field: str, items: str) -> list[dict]:
    if not arguments.get(field):
        raise DriveError(f"name {field}: a list of at least one of {items}")
    return arguments[field]


def ops_tool(group: str, typed: dict[str, dict]) -> dict:
    verbs = OPS[group]
    shapes = [
        {"type": "object", "properties": {verb: typed.get(verb) or chained(schema(leaf(group, verb)))}, "required": [verb], "additionalProperties": False}
        for verb in verbs
    ]
    return {
        "name": group,
        "description": OPS_HELP.format(lead=LEADS[group], verbs=", ".join(verbs)),
        "inputSchema": {
            "type": "object",
            "properties": {"ops": {"type": "array", "minItems": 1, "items": {"oneOf": shapes}}, "dry_run": DRY_RUN},
            "required": ["ops"],
        },
    }


def chained(shape: dict) -> dict:
    if "id" in shape["properties"]:
        shape["required"].remove("id")
        shape["properties"]["id"]["description"] += "; default the one this call's last add made"
    return shape


def leaf(*words: str) -> argparse.ArgumentParser:
    parser = cli.parser()
    for word in words:
        parser = next(action for action in parser._actions if isinstance(action, argparse._SubParsersAction)).choices[word]
    return parser


def fields(parser: argparse.ArgumentParser) -> dict[str, argparse.Action]:
    return {
        (action.option_strings[0].removeprefix("--") if action.option_strings else action.dest).replace("-", "_"): action
        for action in parser._actions
        if action.dest not in UNTYPED
    }


def schema(parser: argparse.ArgumentParser) -> dict:
    taken = fields(parser)
    return {
        "type": "object",
        "properties": {field: typed(action) for field, action in taken.items()},
        "required": [field for field, action in taken.items() if action.required],
        "additionalProperties": False,
    }


def typed(action: argparse.Action) -> dict:
    if action.nargs == 0:
        shape: dict = {"type": "boolean"}
    else:
        one: dict = {"type": "integer" if action.type is int else "string"}
        if action.choices:
            one["enum"] = list(action.choices)
        many = action.nargs in ("+", "*") or isinstance(action, argparse._AppendAction)
        shape = {"type": "array", "items": one} if many else one
    if action.help:
        shape["description"] = action.help
    return shape


def argv(words: tuple[str, ...], given: dict) -> list[str]:
    """The drive command an op runs: its positionals by name, then each flag, a value glued to its flag so a value opening with a dash stays a value."""
    taken = fields(leaf(*words))
    if unknown := sorted(set(given) - set(taken)):
        raise DriveError(f"drive {' '.join(words)} takes no {', '.join(unknown)}; it takes {', '.join(taken)}")
    line = [*words, *(item for field, action in taken.items() if not action.option_strings for item in listed(given.get(field)))]
    for field, action in taken.items():
        if (value := given.get(field)) is None or not action.option_strings:
            continue
        values = listed(value)
        if isinstance(action, argparse.BooleanOptionalAction):
            line.append(action.option_strings[0 if value else 1])
        elif action.nargs == 0:
            line += [action.option_strings[0]] if value else []
        elif action.nargs in ("+", "*"):
            line += [action.option_strings[0], *values]
        else:
            line += [f"{action.option_strings[0]}={item}" for item in values]
    return line


def listed(value: object) -> list[str]:
    return [] if value is None else [str(item) for item in (value if isinstance(value, list) else [value])]


def batch(name: str, group: tuple[str, ...], ops: list[dict], dry_run: bool) -> str:
    said: list[str] = []
    made = None
    for index, op in enumerate(ops, start=1):
        if len(op) != 1:
            raise DriveError("\n".join([*said, f"{index}: an op names one verb, not {', '.join(op) or 'none'}"]))
        [(verb, given)] = op.items()
        if verb not in (allowed := OPS.get(group[0], group)):
            raise DriveError("\n".join([*said, f"{index}: the {group[0]} tool runs {', '.join(allowed)}, not {verb}"]))
        words = (*group, verb) if group[0] in OPS else group
        if words == ("job", "add") and isinstance(given.get("order"), dict):
            given = given | {"order": first(ran(name, said, index, len(ops), ("order", "add"), given["order"], dry_run))}
        if verb != "add" and "id" in fields(leaf(*words)) and "id" not in given:
            if made is None:
                raise DriveError("\n".join([*said, f"{index} {' '.join(words)}: name its id, since no add came before it"]))
            given = given | {"id": made}
        printed = ran(name, said, index, len(ops), words, given, dry_run)
        if verb == "add":
            made = first(printed)
    return "\n".join(said)


def first(printed: list[str]) -> str:
    return printed[0].split()[0] if printed else "<id>"


def ran(name: str, said: list[str], index: int, count: int, words: tuple[str, ...], given: dict, dry_run: bool) -> list[str]:
    """Run one drive verb in this process, appending its line to said; a refusal raises with every line so far and the ops left unrun."""
    line = argv(words, given)
    if dry_run:
        said.append(f"{index} {shlex.join(['drive', *line])}")
        return []
    out, usage = io.StringIO(), False
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
        try:
            code = cli.main([*line, "--drive", name])
        except SystemExit as stop:
            code, usage = stop.code, True
    printed = [text.strip() for text in out.getvalue().splitlines() if text.strip()]
    if code:
        left = f"; ops {index + 1}-{count} not run" if index < count else ""
        why = printed[-1:] if usage else printed
        raise DriveError("\n".join([*said, f"{index} {' '.join(words)} refused: {'; '.join(why)}{left}"]))
    said.append(f"{index} {' '.join(words)}: {'; '.join(printed)}")
    return printed


def record(name: str, records: list[dict], dry_run: bool) -> str:
    """Post the records as one cci batch from this session's lane, a sub-coordinator's its project's, to the drive's other session or the lane each names."""
    data = state.load(name)
    session = os.environ.get("CLAUDE_CODE_SESSION_ID")
    role = state.session_role(data, session)
    if role is None:
        raise DriveError(f"this session holds no role in drive {name}, so no lane to post as")
    project = state.session_project(data, session)
    lane, other = ("communicator", "root") if role == "communicator" else (state.project_lane(project) if project else "root", "communicator")
    posts = [post(item, data, other) for item in records]
    if dry_run:
        return "\n".join(f"{index} {lane}: {json.dumps(item, ensure_ascii=False)}" for index, item in enumerate(posts, start=1))
    return "\n".join(f"{index} {item['kind']} {seq}" for index, (item, seq) in enumerate(zip(posts, sent(name, lane, posts), strict=True), start=1))


def sent(name: str, lane: str, posts: list[dict]) -> list[str]:
    """Post the records as one cci batch from the lane, and return each one's seq."""
    try:
        done = subprocess.run(
            ["cci", "post", "--drive", name, "--lane", lane, "--batch", "-"],
            input=json.dumps(posts),
            capture_output=True,
            text=True,
            timeout=POST_SECONDS,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise DriveError(f"cci post: {error}") from None
    if done.returncode:
        raise DriveError(f"{done.stdout}{done.stderr}".strip())
    return done.stdout.split()


def post(item: dict, data: dict, other: str) -> dict:
    """The cci record an item posts as, to the lane it names, else the other session's, a defect's long text cut to one line with the whole at the top of its body, as the post hook cuts a record; the communicator's owner or answer record goes, whatever lane it names, to the coordinator session hearing the order of the job it answers, else of the newest order in its thread, a sub-coordinator's for its project."""
    drive = data["name"]
    if item["kind"] not in RECORD["properties"]["kind"]["enum"]:
        raise DriveError(f"record posts owner, answer and defect records; a {item['kind']} goes through cci post, whose hook reads it")
    text, attrs = item["text"], dict(item.get("attrs") or {})
    made = {"kind": item["kind"]}
    for key in ("url", "re", "resolves", "topic", "body"):
        if item.get(key):
            made[key] = item[key]
    if waits := item.get("waits"):
        if not (named := state.WAITS_ID.fullmatch(waits)):
            raise DriveError(f"{waits} is no question card's waits id, <drive>:<j>-<seq>; a PR card's tap needs no record")
        if named[1] != drive:
            raise DriveError(f"{waits} is a card of drive {named[1]}, not {drive}: record its answer in that drive")
        attrs["job"], made["re"] = named[2], [named[3]]
    for attr in ("thread", "ruling"):
        if item.get(attr):
            attrs[attr] = item[attr]
    lanes = ["root", "communicator", *map(state.project_lane, data["subcoordinators"])]
    if (to := (other == "root" and state.serving_lane(data, item["kind"], attrs)) or item.get("to") or other) not in lanes:
        raise DriveError(f"to {to}: no session of drive {drive} reads that lane; name one of {', '.join(lanes)}")
    made["to"] = [f"{to}@{drive}"]
    if item.get("undone"):
        attrs["undone"], text = text, f"undone: {text}"
    made |= {"text": text} | ({"attr": attrs} if attrs else {})
    return limits.shortened(made) or made
