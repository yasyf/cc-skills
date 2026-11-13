import argparse
import contextlib
import io
import json
import os
import shlex
import subprocess
import uuid

from drive import cli, limits, reads, render, settled, state, superseded, talk, view
from drive.state import DriveError

UNTYPED = {"help", "drive", "json"}
OPS = {"order": ("add", "set", "posted", "told"), "job": ("add", "set", "brief"), "pr": ("add", "set"), "incident": ("open", "stop", "freeze")}
ONE_VERB = {"heard": ("heard",), "handler": ("handler",), "status": ("status",), "search_plan": ("plan", "search")}
POST_SECONDS = 60
UNSENT = (
    "no record went out, so while cci is down reach the session it was for with ListAgents, then SendMessage to"
    " `{drive}-coordinator`, `{drive}-communicator` or `{drive}-meta` carrying it, and post it once cci is back; a"
    " disconnect is never a reason a message did not go, by his ruling 4535fb1: \"use regualr claude code sesion"
    " messaging until it sback then there sno excuse\""
)
DRY_RUN = {"type": "boolean", "description": "run each op through its verb's own checks, refused as the call would be, print the drive command or record each would run, and keep nothing, an op stopping at a command that does more than read"}
DRIVE = {"type": "string", "description": "the drive this call acts on, as --drive names it; default the drive bound to this session, else the only drive"}
OPS_HELP = (
    "{lead} Checks every op by its verb's own checks before any is kept, each one verb with its flags as fields, then returns"
    " one line per op. An op its verb refuses keeps none of them, and the call names every refused op with its fix: fix each"
    " and send the whole call again. {saves}"
)
ONE_SAVE = ("order", "job")
SAVED_ONCE = "The call runs the ops once, in order, under one save of the drive, so a refusal anywhere writes nothing."
KEPT_BEFORE = "It runs them once all pass; a refusal while they run keeps each op before it, since {why}: send the ops it names unrun again."
STARTS_OUTSIDE = {"pr": "pr add starts the lander", "incident": "open and freeze start an evidence record and a freeze"}
LEADS = {
    "order": (
        "His orders: add records one from his words, set changes one, posted records the communicator's post on one, told records"
        " the one note each of its reporters gets, a bug's fix-live note or a question's answer, in the thread of their message, or in their"
        " group DM with him and the bot."
    ),
    "job": "The drive's jobs: add starts one on an order's parts, none when its inline order joins an open order, set changes one, brief writes its brief.",
    "pr": "The drive's PRs: add records one a job opened, set changes one.",
    "incident": (
        "Incidents: open adds his order as one, with its fix and diagnosis jobs and its evidence record; stop adds the"
        " immediate fix that turns the failing path off and lands at once as an incident fix until the root fix replaces"
        " it; freeze holds the service's other releases."
    ),
}
TEXT = {"type": "string"}
RECORD_ORDER = (
    f'the order the communicator\'s owner record about the drive goes to, written before it goes out so his words reach the meta session'
    f' holding an order: the fields of drive order add for a new order the meta session holds, such as "{state.TOOLING} <what his words'
    ' ask for>" with its title, or his question in his own words with its title, project and workstream, added from his message at url in'
    " his thread, or the id of the open order the meta session holds that his words carry on, whatever its words open with, which takes"
    " them as a new part; left out, they carry on the open one the record's attr order, job or thread names, and with none the record is refused."
    " The record goes out naming it as attr order, and the part it added as attr part, <o>:<part>"
)
RECORD = {
    "type": "object",
    "properties": {
        "kind": {
            "enum": ["owner", "answer", "handoff", "defect"],
            "description": (
                "handoff asks the session holding the order its attrs.order names for a step only it takes on that order, such as"
                " starting a builder, with no re, since cci also delivers a record to the writer of the record its re names; a"
                " report goes through the cci `post` tool instead, whose hook reads it, and no record draws his card on a PR"
            ),
        },
        "text": {
            "type": "string",
            "description": "his words verbatim for owner, the tapped label or his words for answer, the step asked for handoff, the refusal verbatim for defect, each time it quotes as it stands",
        },
        "url": {
            "type": "string",
            "description": (
                "his message's or the card's permalink, or terminal for words he typed into a drive session's terminal,"
                " always given for his words: an answer under a question card, in"
                " whatever thread the record names, ends the job's question that card asks as answered, and owner words"
                " under it about that card's work end it as withdrawn, and the card follows, showing his words with no"
                " buttons; his feedback on the drive under a product card leaves it asking, and only the coordinator's"
                " --rework naming its PR, on his order or the job carrying it, withdraws a PR card. A ruling the record names"
                " is kept as his drive answer at this link, listing each other card, part and PR it could change. A record"
                " that ends a question or names a ruling is refused unless his message or card answer here holds its text,"
                " and the ruling's words above Notes:"
            ),
        },
        "thread": {"type": "string", "description": "the thread it is about, kept as attr thread"},
        "waits": {
            "type": "string",
            "description": (
                "the waits id, <drive>:<j>-<n>, of the question card his tap or words answer: attrs waits and job=<j>,"
                " ending the job's question as answered in the same call, and the card follows, showing his words with no"
                " buttons; a question his recorded words settle before its card drew is answered by its waits id the same"
                " way, so no card draws"
            ),
        },
        "thread_card": {
            "type": "string",
            "description": (
                "the waits id his message's thread_card names, given on every record of his words under a question card:"
                " kept as attr thread_card; his owner words about that card's work, such as a rework or a correction, end the"
                " job's question as withdrawn in the same call, and the card follows, showing his words, while his feedback"
                " on the drive under a product card, and his feedback on a post or card under any card, leave it asking. Only that card ends: a sibling card, part or PR his"
                " ruling removes is marked --dropped on the drive answer the call keeps for the ruling"
            ),
        },
        "undone": {"type": "boolean", "description": "an Undo tap: attr undone=<text> and the text undone: <text>"},
        "ruling": {
            "type": "string",
            "description": (
                "a ruling's cc-notes answer id, as attr ruling, whose words above Notes: his message at url must hold; on an owner or"
                " answer record with url, his owner ruling on his work included, the call keeps it as a drive answer listing each card"
                " asking him, part undelivered and PR not yet live it could change, which the watch's ANSWER line chases until drive"
                " answer <id> marks each, --dropped for each his words remove"
            ),
        },
        "about": {
            "enum": list(state.ABOUTS),
            "description": (
                f'required on an owner record, as attr about: "{state.ABOUT_DRIVE}" for drive tooling, {state.DRIVE_TOOLING},'
                " his ruling or feedback on any of it included, which reaches the meta session alone whatever lane to names,"
                " holding the order its order field adds or carries on;"
                f' "{state.ABOUT_HIS_WORK}" for everything else, his product orders and the code they change, which reaches'
                " the coordinator session hearing its order, his words kept first as a new part of the open order its attrs or"
                " thread serve, which the watch names until a job or card carries it"
            ),
        },
        "re": {
            "type": "string",
            "description": "the seq this answers, when no waits id names one; a reply to several records names the newest, whose own re reaches the rest",
        },
        "resolves": {"type": "array", "items": TEXT, "description": "seqs this closes"},
        "topic": {"type": "string", "description": "a pairing key, such as card:<thread_ts> for a card with no waits id"},
        "to": {
            "type": "string",
            "description": (
                "the lane it goes to in place of the default, the coordinator's from the communicator and the communicator's"
                " from any other session; a defect goes to the meta session, the communicator's owner record about the drive"
                " to the meta session alone, its answer or handoff record naming no job to the coordinator that wrote the record"
                " its re answers, and its other owner, answer or handoff record, and any other session's handoff, to the session"
                " holding the order its attrs.order names, else the order of the job it answers, else its thread's newest order,"
                " whatever lane this names"
            ),
        },
        "attrs": {
            "type": "object",
            "additionalProperties": TEXT,
            "description": (
                "the record's attrs; an owner record sharing a report someone else wrote names reporter, that message's permalink,"
                " title, a few words naming it, and job, the job at work on its fix: his words joining an open order become an order"
                " of their own holding that reporter, filed like the order they join and carried by that job, while an order his same"
                " message added takes the reporter itself"
            ),
        },
        "body": {"type": "string", "description": "the rest past the text's one line"},
    },
    "required": ["kind", "text"],
    "additionalProperties": False,
}


def verb_tools() -> list[dict]:
    """The drive verbs the MCP server serves beside say, each typed from the CLI's own parser, built once for them all."""
    root = cli.parser()
    job_add = schema(leaf("job", "add", root=root))
    job_add["properties"]["order"] = {
        "anyOf": [TEXT, schema(leaf("order", "add", root=root))],
        "description": "the order's id, or the fields of drive order add, which adds that order first and takes its id",
    }
    item = RECORD | {"properties": RECORD["properties"] | {"order": {"anyOf": [TEXT, schema(leaf("order", "add", root=root))], "description": RECORD_ORDER}}}
    return [
        {
            "name": "heard",
            "description": (
                "Record that he wrote, each time he does. Returns heard, or away when away is set, and nothing to act on:"
                " the drive's watch posts his catch-up through a handler it starts."
            ),
            "inputSchema": {"type": "object", "properties": {**schema(leaf("heard", root=root))["properties"], "drive": DRIVE, "dry_run": DRY_RUN}},
        },
        {
            "name": "handler",
            "description": (
                "Start a long-running:handler on its brief as its own claude --bg session, recording that he wrote when the"
                " brief carries his words, so his message takes the ack and this one call. Returns started <its id>; the"
                " line it ends on comes back as one cci done record from lane handler when it ends, and its session ends"
                " with it. A message of his and a thread each have one handler at a time: a brief, his words or a record, in"
                " a live handler's thread, a record's own thread or else its brief's Thread: line, on the message of his it"
                " holds, or naming its record up a re chain or named by its record's, returns handed to <that handler's id>,"
                " which takes it at its next step and covers it in its post; it refuses a brief while a live handler holds"
                " the same record or the same message of his, a copy that needs nothing. For his read-only question, answer"
                " alone adds his answer order and the handler's reader job on it in the same call, so no job call follows,"
                " and the handler files that order under the project and workstream it reads his question is about; a brief"
                " handed over adds neither."
            ),
            "inputSchema": {"type": "object", "properties": {**schema(leaf("handler", root=root))["properties"], "drive": DRIVE, "dry_run": DRY_RUN}, "required": ["brief"]},
        },
        {
            "name": "record",
            "description": (
                "Send his words (owner), his answer to a card (answer), a step the session holding an order owes on it"
                " (handoff) or a tool's refusal (defect) to another session of the drive in one call, as this session's role,"
                " a handler's included: a defect reaches the meta session, the communicator's handoff naming no job the"
                " coordinator that wrote the record its re answers, else, as any other session's handoff, the session holding the"
                " order its attrs.order names,"
                " and an owner record names about, refused without it: the drive's owner record"
                " about the drive, a ruling on how it works included, reaches the meta session alone holding its order, which"
                " the call adds first, a new order the meta session holds or his words as a part of the open one they carry on, as its"
                " order field says, and one about his work the coordinator hearing its order."
                " A question card he answered takes waits, which fills re with the ask's seq and attr job; an"
                " Undo tap sets undone. Once the records go out, each open card his words close is closed: a question card his"
                " answer answers settles showing his words, and one his owner words under it change is withdrawn showing them;"
                " a PR card stays up, since only the coordinator's --rework naming its PR withdraws it; an answer"
                " whose waits names a card no longer waiting on him is refused, since his answer there is"
                " recorded already. A ruling an owner or answer record names with url is kept as his drive answer in the same"
                " call, listing each card, part and PR it could change for drive answer <id> to mark. A defect's or handoff's"
                " text past one 120-character line goes as that line cut at a word, the whole text atop its body. Returns each"
                " record's seq, then each card closed, then each ruling kept with its listing."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {"records": {"type": "array", "minItems": 1, "items": item}, "drive": DRIVE, "dry_run": DRY_RUN},
                "required": ["records"],
            },
        },
        *(ops_tool(group, {"add": job_add} if group == "job" else {}, root) for group in OPS),
        {
            "name": "status",
            "description": "Read where each order, job and PR stands, as drive status prints it: the ones named, each whole, or with none a coordinator's own open orders, a line each, and any other session's every open order. A handler, the coordinator or the meta session reads this, never the communicator.",
            "inputSchema": {"type": "object", "properties": {**schema(leaf("status", root=root))["properties"], "drive": DRIVE, "dry_run": DRY_RUN}},
            "annotations": {"readOnlyHint": True},
        },
        {
            "name": "search_plan",
            "description": (
                "Search the plans the coordinator's checkout keeps in cc-notes for the phrases, to check a step against the plan"
                " before building it or asking. Returns each line carrying one of them whole, in any case, under its plan's title,"
                " id, status and the headings above it, plans best first, cut at 12,000 characters: narrow the phrases when it cuts."
            ),
            "inputSchema": {"type": "object", "properties": {**schema(leaf("plan", "search", root=root))["properties"], "drive": DRIVE, "dry_run": DRY_RUN}, "required": ["words"]},
            "annotations": {"readOnlyHint": True},
        },
    ]


def described(tool: dict) -> str:
    """A tool's description, then a line per field of its input, a nested one by its path, with its type, whether it is required and its description."""
    return "\n".join([f"{tool['name']}: {tool['description']}", *field_lines(tool["inputSchema"], "")])


def field_lines(shape: dict, path: str, alternative: bool = False) -> list[str]:
    """A line per field, one naming a oneOf alternative, such as an op's verb, never marked required."""
    lines = []
    for field, value in shape.get("properties", {}).items():
        required = ", required" if field in shape.get("required", []) and not alternative else ""
        lines.append(f"{path}{field} ({kind(value)}{required})" + (f": {value['description']}" if "description" in value else ""))
        lines += nested(value, f"{path}{field}")
    return lines


def nested(shape: dict, path: str, alternative: bool = False) -> list[str]:
    if "properties" in shape:
        return field_lines(shape, f"{path}.", alternative)
    if "items" in shape:
        return nested(shape["items"], f"{path}[]")
    return [
        *(line for each in shape.get("oneOf", []) for line in nested(each, path, alternative=True)),
        *(line for each in shape.get("anyOf", []) for line in nested(each, path)),
    ]


def kind(shape: dict) -> str:
    if "enum" in shape:
        return " | ".join(map(str, shape["enum"]))
    if "items" in shape:
        return f"list of {kind(shape['items'])}"
    if alternatives := shape.get("oneOf") or shape.get("anyOf"):
        return " or ".join(dict.fromkeys(map(kind, alternatives)))
    return shape["type"]


def run(tool: str, name: str, arguments: dict) -> str:
    if tool == "say":
        return talk.say(name, arguments)
    dry_run = arguments.pop("dry_run", False)
    match tool:
        case "record":
            return record(name, listing(arguments, "records", "owner, answer, handoff or defect records"), dry_run)
        case "heard" | "handler" | "status" | "search_plan":
            said: list[str] = []
            printed = ran(name, said, 1, ONE_VERB[tool], arguments, dry_run)
            return "\n".join(said if dry_run or not printed else printed)
        case group:
            return batch(name, group, listing(arguments, "ops", ", ".join(OPS[group])), dry_run)


def parsed_arguments(arguments: dict, shapes: dict[str, dict]) -> dict:
    """The call's arguments with each list or object a client sent as JSON text parsed, at any depth, such as an op's flaky pair, as the tool's schema types it."""
    return parsed("arguments", arguments, {"properties": shapes})


def parsed(key: str, value: object, shape: dict) -> object:
    choices = shape.get("oneOf") or shape.get("anyOf") or ()
    kinds = {shape.get("type"), *(choice.get("type") for choice in choices)}
    if isinstance(value, str) and "string" not in kinds and kinds & {"array", "object"}:
        try:
            value = json.loads(value)
        except json.JSONDecodeError as error:
            raise DriveError(f"{key} is a list or object, and its text is not JSON: {error}") from None
    if isinstance(value, list) and "items" in shape:
        return [parsed(key, item, shape["items"]) for item in value]
    if isinstance(value, dict):
        properties = {field: each for choice in (shape, *choices) for field, each in choice.get("properties", {}).items()}
        return {field: parsed(field, item, properties.get(field, {})) for field, item in value.items()}
    return value


def listing(arguments: dict, field: str, items: str) -> list[dict]:
    if not arguments.get(field):
        raise DriveError(f"name {field}: a list of at least one of {items}")
    return arguments[field]


def ops_tool(group: str, typed: dict[str, dict], root: argparse.ArgumentParser) -> dict:
    verbs = OPS[group]
    shapes = [
        {"type": "object", "properties": {verb: typed.get(verb) or chained(schema(leaf(group, verb, root=root)))}, "required": [verb], "additionalProperties": False}
        for verb in verbs
    ]
    return {
        "name": group,
        "description": OPS_HELP.format(lead=LEADS[group], saves=SAVED_ONCE if group in ONE_SAVE else KEPT_BEFORE.format(why=STARTS_OUTSIDE[group])),
        "inputSchema": {
            "type": "object",
            "properties": {"ops": {"type": "array", "minItems": 1, "items": {"oneOf": shapes}}, "drive": DRIVE, "dry_run": DRY_RUN},
            "required": ["ops"],
        },
    }


def chained(shape: dict) -> dict:
    if "id" in shape["properties"]:
        shape["required"].remove("id")
        shape["properties"]["id"]["description"] += "; default the one this call's last add made"
    return shape


def leaf(*words: str, root: argparse.ArgumentParser | None = None) -> argparse.ArgumentParser:
    parser = root or cli.parser()
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
        if isinstance(action.nargs, int):
            shape = {"type": "array", "items": one, "minItems": action.nargs, "maxItems": action.nargs}
        elif action.nargs in ("+", "*") or isinstance(action, argparse._AppendAction):
            shape = {"type": "array", "items": one}
        else:
            shape = one
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
        elif action.nargs in ("+", "*") or isinstance(action.nargs, int):
            line += [action.option_strings[0], *values]
        else:
            line += [f"{action.option_strings[0]}={item}" for item in values]
    return line


def listed(value: object) -> list[str]:
    return [] if value is None else [str(item) for item in (value if isinstance(value, list) else [value])]


def batch(name: str, group: str, ops: list[dict], dry_run: bool) -> str:
    """Run the ops, each by its verb's own handler: the order and job tools once, under one save of the drive; a dry run, and the pr and incident tools before they run, in a dry state.batch, which keeps nothing and starts no command that does more than read."""
    if group in ONE_SAVE and not dry_run:
        with state.batch(name) as after:
            said = performed(name, group, ops, False)
        return "\n".join([*said, *after])
    with state.batch(name, dry=True):
        said = performed(name, group, ops, True)
    return "\n".join(said if dry_run else performed(name, group, ops, False))


def performed(name: str, group: str, ops: list[dict], dry_run: bool) -> list[str]:
    """Run the ops in order, a line each, an op after an add acting on what it made unless it names its id, and a job add whose inline order joins an open order adding no job while a job of that order carries it, or its owner job does on an order hurting someone now, and added on the new order otherwise. Under one save or in a dry run, every op runs and every refusal is named with its fix, so nothing is kept; otherwise the first refusal stops the call, naming the ops it left unrun."""
    whole = dry_run or group in ONE_SAVE
    said: list[str] = []
    refused: list[str] = []
    made: str | None = None
    for index, op in enumerate(ops, start=1):
        try:
            made = op_ran(name, group, ops, index, said, made, dry_run)
        except DriveError as error:
            if not whole:
                left = f"; ops {index + 1}-{len(ops)} not run" if index < len(ops) else ""
                raise DriveError("\n".join([*said, f"{error}{left}"])) from None
            refused.append(str(error))
            made = None if "add" in op else made
    if refused:
        raise DriveError("\n".join([f"refused, nothing written: {len(refused)} of {len(ops)} ops refused; fix each and send the whole call again", *refused]))
    return said


def op_ran(name: str, group: str, ops: list[dict], index: int, said: list[str], made: str | None, dry_run: bool) -> str | None:
    """Run the call's op at index, refused when it is not one verb of the tool or names no id with no add before it, and left unchecked when it acts on an add that made no id, one a dry run stopped before its write; the id it made, or the one made before it for an op that is no add."""
    op = ops[index - 1]
    if len(op) != 1 or (verb := next(iter(op))) not in OPS[group]:
        why = f"an op names one verb, not {', '.join(op) or 'none'}" if len(op) != 1 else f"the {group} tool runs {', '.join(OPS[group])}, not {verb}"
        raise DriveError(refusal(index, (group, *op), {}, why))
    given, words = op[verb], (group, verb)
    if verb != "add" and "id" in fields(leaf(*words)) and "id" not in given:
        if not any("add" in earlier for earlier in ops[: index - 1]):
            raise DriveError(refusal(index, words, given, "name its id, since no add came before it"))
        if made is None:
            said.append(f"{index} {shlex.join(['drive', *argv(words, given)])}: unchecked, since the add before it made no id")
            return None
        given = given | {"id": made}
    if words == ("job", "add") and isinstance(given.get("order"), dict):
        if (order_id := first(ran(name, said, index, ("order", "add"), given["order"], dry_run))) is None:
            said.append(f"{index} drive job add: unchecked, since its order add made no id")
            return None
        data = state.load(name)
        if (joined := data["orders"][order_id])["joins"] and (joined["jobs"] or state.hurts_now(data, joined)):
            said.append(f"{index} job add: none, since {order_id} joins {joined['joins']}")
            return None
        given = given | {"order": order_id}
    printed = ran(name, said, index, words, given, dry_run)
    return first(printed) if verb == "add" else made


def refusal(index: int, words: tuple[str, ...], given: dict, why: str) -> str:
    """An op's refusal, naming it by its index, verb and opening words, with its fix."""
    opening = given.get("words") or given.get("goal")
    return f"{index} {' '.join(words)}{f' {json.dumps(render.cut(opening, 60), ensure_ascii=False)}' if opening else ''} refused: {why}"


def first(printed: list[str] | None) -> str | None:
    """The id an add printed first, or None for one a dry run stopped before its write."""
    return printed[0].split()[0] if printed else None


def ran(name: str, said: list[str], index: int, words: tuple[str, ...], given: dict, dry_run: bool) -> list[str] | None:
    """Run one drive verb in this process, appending its line to said, its printed lines back; a refusal raises naming the op and why. A dry run runs it the same way in a dry state.batch, so it is refused as the call would be and keeps nothing, its line the drive command; one stopped at a command that does more than read, which every check before it passed, says so and gives back None."""
    try:
        line = argv(words, given)
    except DriveError as error:
        raise DriveError(refusal(index, words, given, str(error))) from None
    out, usage, stopped = io.StringIO(), False, None
    with state.batch(name, dry=True) if dry_run else contextlib.nullcontext(), contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
        try:
            code = cli.main([*line, "--drive", name])
        except SystemExit as stop:
            code, usage = stop.code, True
        except state.Rehearsed as reached:
            code, stopped = 0, str(reached)
    printed = [text.strip() for text in out.getvalue().splitlines() if text.strip()]
    if code:
        why = printed[-1:] if usage else printed
        raise DriveError(refusal(index, words, given, "; ".join(text.removeprefix("drive: ") for text in why)))
    if not dry_run:
        said.append(f"{index} {' '.join(words)}: {'; '.join(printed)}")
        return printed
    said.append(f"{index} {shlex.join(['drive', *line])}" + (f": checked up to `{stopped}`, which a dry run never starts" if stopped else ""))
    return None if stopped else printed


def record(name: str, records: list[dict], dry_run: bool) -> str:
    """Post the records as one cci batch from this session's lane, a sub-coordinator's its project's, to the lane each names, else the communicator, the coordinator's for the communicator, each sent where the drive routes it."""
    data = state.load(name)
    session = os.environ.get("CLAUDE_CODE_SESSION_ID")
    role = state.session_role(data, session)
    if role is None:
        raise DriveError(f"this session holds no role in drive {name}, so no lane to post as")
    if role == "meta" and any(item["kind"] == "defect" for item in records):
        raise DriveError(
            "refused, no record posted: the meta session holds drive tooling, so a defect it hits is its own: one job call adds"
            " its order and reader, by drive manual meta defect"
        )
    project = state.session_project(data, session)
    lane, other = {"communicator": ("communicator", "root"), "meta": ("meta", "communicator")}.get(role) or (state.project_lane(project) if project else "root", "communicator")
    posts = [post(item, data, other, session) for item in records]
    found = his_cards(name) if any(made["kind"] in ("owner", "answer") for made in posts) else view.NO_CARDS
    closes = [closed_by(data, index, made, item.get("order")) for index, (item, made) in enumerate(zip(records, posts, strict=True), start=1)]
    for index, (made, questions) in enumerate(zip(posts, closes, strict=True), start=1):
        if (questions or (made.get("attr") or {}).get("ruling")) and (unsaid := unheard(data, found, made)):
            raise DriveError(f"{index}: refused, nothing posted: {unsaid}")
    ordering = [state.meta_alone(role, made["kind"], made.get("attr") or {}) for made in posts]
    if unordered := [index for index, (item, alone) in enumerate(zip(records, ordering, strict=True), start=1) if "order" in item and not alone]:
        raise DriveError(f"{unordered[0]}: refused, nothing posted: order is the order the communicator's owner record about the drive goes to; name the order any other record serves as attrs.order")
    said = ordered_first(name, records, posts, [state.his_words(role, made["kind"]) for made in posts], dry_run)
    if dry_run:
        said += [f"{index} {lane}: {json.dumps(item, ensure_ascii=False)}" for index, item in enumerate(posts, start=1)]
        said += [
            f"{index} would end {job_id}'s question \"{render.cut(question['title'] or '', 80)}\" {'answered' if made['kind'] == 'answer' else 'withdrawn'}"
            + (", closing its card" if question["card"] or question["waits"] else "")
            for index, (made, questions) in enumerate(zip(posts, closes, strict=True), start=1)
            for job_id, question in questions
        ]
        said += [
            f"{index} would keep his ruling {ruling} as a drive answer listing each card, part and PR it could change"
            for index, made in enumerate(posts, start=1)
            if (ruling := (made.get("attr") or {}).get("ruling")) and made.get("url") and ruling not in data["answers"]
        ]
        return "\n".join(said)
    try:
        seqs = sent(name, lane, posts)
    except DriveError as error:
        raise DriveError("\n".join([*said, f"{error}; {UNSENT.format(drive=name)}"])) from None
    said += [f"{index} {item['kind']} {seq}" for index, (item, seq) in enumerate(zip(posts, seqs, strict=True), start=1)]
    keep_undelivered(name, posts, seqs, closes, found)
    ended = keep_closed(name, posts, seqs, closes, [superseded_by_answer(data, made, questions, found) for made, questions in zip(posts, closes, strict=True)])
    said += [f"{index} {job_id}'s question \"{render.cut(question['title'] or '', 80)}\" ended {question['closed']['how']} by {seqs[index - 1]}" for index, job_id, question in ended]
    said += superseded.follow(name, [(job_id, question) for _, job_id, question in ended])
    said += keep_rulings(name, posts, found)
    return "\n".join(said)


def keep_rulings(name: str, posts: list[dict], found: dict) -> list[str]:
    """Keep each ruling an owner or answer record of his words names with its link as his drive answer, beside each card asking him, part undelivered and PR not yet live it could change, as drive answer records one, so the watch's ANSWER line chases each until drive answer marks it; a ruling kept already is left as it is. Its listing each, or why Jev's reading is missing from it."""
    said = []
    for made in posts:
        data = state.load(name)
        if not ((ruling := (made.get("attr") or {}).get("ruling")) and made.get("url")) or ruling in data["answers"]:
            continue
        asked = superseded.asking(data, found)
        try:
            changes = settled.changed(settled.shown(ruling, data["roles"]["coordinator"]["cwd"])["title"], made["text"], asked)
        except DriveError:
            changes = set()
            said.append(
                f"his ruling {ruling}: Jev gave no reading of it against his other open cards, so it lists only what its card's job and order"
                f" hold and its words name: mark any other card it changes on drive answer {ruling}"
            )
        bound = superseded.binding(data, made["url"], asked, data["roles"]["coordinator"]["cwd"])
        with state.edit(name) as data:
            kept = data["answers"][ruling] = superseded.record(data, made["text"], made["url"], asked, changes, bound, state.stamp())
        said.append(superseded.listing(ruling, kept, superseded.unmarked(kept, asked), asked, data) or f"his ruling {ruling} kept as a drive answer: nothing it could change asks him or waits on it")
    return said


def ordered_first(name: str, records: list[dict], posts: list[dict], ordering: list[bool], dry_run: bool) -> list[str]:
    """Write, under one save of the drive and before any record goes out, the order or part each communicator's owner record makes, so it stands even when cci is down and a resent call adds none twice; a refusal writes none and posts nothing. What each write did, a line each."""
    said: list[str] = []
    if not any(ordering):
        return said
    try:
        with state.batch(name, dry=dry_run) as after:
            for index, (item, made, alone) in enumerate(zip(records, posts, ordering, strict=True), start=1):
                if alone:
                    carried(name, said, index, item, made["attr"], dry_run)
    except DriveError as error:
        raise DriveError(f"{error}; nothing posted and no order written: fix it and send the whole call again") from None
    return said + after


def carried(name: str, said: list[str], index: int, item: dict, attrs: dict, dry_run: bool) -> None:
    """Write the order or part the communicator's owner record makes before it goes out, so no word of his adding work goes unchased: about the drive, the new order the meta session holds that its order field's fields make, holding the reporter attr reporter names, else his words as a new part of the open order the meta session holds that its order field names or its attrs or thread serve, whatever its words open with, refused when it serves none; about his work, his words as a new part of the open order a coordinator holds that its attrs or thread serve, and none when it serves none, since the coordinator files his new order. Words sharing someone else's report, attr reporter, that serve an open order go to the order reporters_order names in place of a part. The record names the order as attr order, and a part it added as attr part."""
    named = item.get("order")
    if isinstance(named, dict):
        fields = {"kind": "build", "owner": "meta"} | sourced(item) | ({"reporter": attrs["reporter"]} if attrs.get("reporter") else {}) | named
        if fields["owner"] != "meta":
            raise DriveError(f"{index}: refused, his words about the drive go to an order the meta session holds, so leave order.owner out or name it meta")
        attrs["order"] = first(ran(name, said, index, ("order", "add"), fields, dry_run))
        return
    data = state.load(name)
    order_id = named or state.served_order(data, attrs)
    order = data["orders"].get(order_id or "")
    tooling = attrs["about"] == state.ABOUT_DRIVE
    held = bool(order) and order["state"] not in state.CLOSED_ORDER_STATES and (order["owner"] == "meta") == tooling
    if not held and not tooling:
        return
    if not held:
        raise DriveError(
            f"{index}: refused, his words about the drive reach the meta session holding an order, and"
            f' {f"{order_id} is no open order the meta session holds" if order_id else "no order holds this record or its thread"}:'
            f' name order, the fields of drive order add for a new order the meta session holds, such as "{state.TOOLING} <what his words'
            ' ask for>" with its title, or the id of the open order the meta session holds that his words carry on'
        )
    if attrs.get("reporter"):
        attrs["order"] = reporters_order(name, said, index, item, attrs, data, order_id, dry_run)
        return
    part = state.whole_order(item["text"])
    attrs |= {"order": order_id, "part": f"{order_id}:{part}"}
    if part not in order["parts"]:
        ran(name, said, index, ("order", "set"), {"id": order_id, "parts": [*order["parts"], part]}, dry_run)


def reporters_order(name: str, said: list[str], index: int, item: dict, attrs: dict, data: dict, served: str, dry_run: bool) -> str:
    """The order keeping the reporter of a report his words share into the open order they serve, so that person hears once its own fix is live, never at the proof of another fix the served order holds: an open order holding that reporter already, then the served order when his same message added it, beside any reporter it holds, else a new order of his words filed, owned and kinded like the served one, titled by attr title and carried by the job attr job names while that job is at work on his feature work."""
    link, order = attrs["reporter"], data["orders"][served]
    if holder := next((order_id for order_id, held in state.open_orders(data).items() if any(kept["link"] == link for kept in held["reporters"])), None):
        return holder
    message = state.slack_message(item.get("url") or "")
    if message and order["from"] == cli.slack_key(item["url"], message):
        ran(name, said, index, ("order", "set"), {"id": served, "reporter": link}, dry_run)
        return served
    if not attrs.get("title"):
        raise DriveError(
            f"{index}: refused, these words share a report someone else wrote into {served}, so they become an order of their own holding"
            " its reporter, which hears once that order's fix is live: name that order's few words as attrs.title"
        )
    fields = {"words": item["text"], "title": attrs["title"], "project": order["project"], "workstream": order["workstream"], "owner": order["owner"], "kind": order["kind"], "reporter": link}
    added = first(ran(name, said, index, ("order", "add"), sourced(item) | fields, dry_run))
    job = data["jobs"].get(attrs.get("job") or "")
    if added and job and job["state"] not in state.FINISHED_JOB_STATES and not cli.tooling(data["orders"][job["order"]]):
        ran(name, said, index, ("job", "set"), {"id": attrs["job"], "carries": [added]}, dry_run)
    return added


def sourced(item: dict) -> dict:
    """Where an order a record of his words adds comes from: his message at its url, else the thread it names."""
    if state.slack_message(item.get("url") or ""):
        return {"from": item["url"]}
    return {"thread": item["thread"]} if item.get("thread") else {}


def unheard(data: dict, found: dict, made: dict) -> str | None:
    """Why a record keeping words as his answer or ruling, one closing his card, settling a question or naming a ruling, is not his: its text, or the ruling's recorded words above its notes, is not what he said at its url."""
    ruling = (made.get("attr") or {}).get("ruling")
    said = [made["text"], *([settled.his(settled.shown(ruling, data["roles"]["coordinator"]["cwd"])["body"])] if ruling else [])]
    return settled.unsaid(said, made.get("url"), found, data)


def superseded_by_answer(data: dict, made: dict, questions: list[tuple[str, dict]], found: dict) -> list[tuple[str, dict, dict]]:
    """Each older open question of a job his answer ends a question of that it decides too, as Jev reads it, with the question his answer is on."""
    if made["kind"] != "answer":
        return []
    return [(job_id, older, question) for job_id, question in questions for older in superseded.decided_by(data, job_id, question, made["text"], found)]


def keep_closed(name: str, posts: list[dict], seqs: list[str], closes: list[list[tuple[str, dict]]], decided: list[list[tuple[str, dict, dict]]]) -> list[tuple[int, str, dict]]:
    """End in one write each job question his words in these records are on, answered by an answer and withdrawn by other words, with his words, their link, the record and the ruling it names, and each older question of its job his answer decides too, superseded; each question ended, by its record's place and its job."""
    ended = []
    with state.edit(name) as data:
        for index, (made, seq, questions, deciding) in enumerate(zip(posts, seqs, closes, decided, strict=True), start=1):
            closed = state.new_close("answered" if made["kind"] == "answer" else "withdrawn", made["text"], made.get("url"), seq=seq, ruling=(made.get("attr") or {}).get("ruling"))
            for job_id, question in questions:
                if (kept := state.question_in(data["jobs"][job_id], question))["closed"] is None:
                    ended += [(index, *each) for each in superseded.end(data, job_id, kept, closed)]
            for job_id, older, question in deciding:
                if (kept := state.question_in(data["jobs"][job_id], older))["closed"] is None:
                    ended += [(index, *each) for each in superseded.end(data, job_id, kept, superseded.superseded_by(closed, question))]
    return ended


def keep_undelivered(name: str, posts: list[dict], seqs: list[str], closes: list[list[tuple[str, dict]]], found: dict) -> None:
    """Hold each answer of his verbatim, under a fresh Orca request id, on every job it frees: the job its attr names and each whose question it ends while they are not finished, and every builder not finished on their orders and the orders those carry, so the watch sends it to a running builder, hands it to the next worker of one that returned and launches one never started with it in its brief, with no session's turn between; an Undo tap takes the answer it undoes off those jobs."""
    answers = [(made, seq, questions) for made, seq, questions in zip(posts, seqs, closes, strict=True) if made["kind"] == "answer"]
    if not answers:
        return
    with state.edit(name) as data:
        for made, seq, questions in answers:
            attrs = made.get("attr") or {}
            asking = list(dict.fromkeys(filter(None, [state.named_job(data, attrs), *(job_id for job_id, _ in questions)])))
            for job_id in hearers(data, asking):
                kept = data["jobs"][job_id]["undelivered"]
                if "undone" in attrs:
                    kept[:] = [answer for answer in kept if not superseded.carries(answer["words"], f'verbatim: "{attrs["undone"]}"')]
                elif all(answer["seq"] != seq for answer in kept):
                    kept.append({"seq": seq, "words": verbatim(made, questions, found), "at": state.stamp(), "said": None, "request": str(uuid.uuid4())})


def hearers(data: dict, asking: list[str]) -> list[str]:
    """The jobs his answer to these jobs frees: each of them not finished, and every builder not finished whose order or carried orders meet theirs."""
    orders = {order_id for job_id in asking for order_id in (data["jobs"][job_id]["order"], *data["jobs"][job_id]["carries"])}
    freed = [
        job_id
        for job_id, job in data["jobs"].items()
        if job["kind"] == "builder" and job["state"] not in state.FINISHED_JOB_STATES and orders & {job["order"], *job["carries"]}
    ]
    return list(dict.fromkeys([*(job_id for job_id in asking if data["jobs"][job_id]["state"] not in state.FINISHED_JOB_STATES), *freed]))


def verbatim(made: dict, questions: list[tuple[str, dict]], found: dict) -> str:
    """His answer as each job it frees holds it: the card it closes, by waits id or else link, and his message's link, his words verbatim, and the option that card recommended when his words differ from it."""
    named = next((question["waits"] or question["card"] for _, question in questions if question["waits"] or question["card"]), None)
    link = f" ({made['url']})" if made.get("url") and made["url"] != named else ""
    said = f'His answer{f" on card {named}" if named else ""}{link}, verbatim: "{made["text"]}"'
    card = view.asked_card(found, named) if named else None
    if card and (recommended := card["card"].get("recommended")) and " ".join(recommended.split()) != " ".join(made["text"].split()):
        said += f'; the card recommended "{recommended}"'
    return said


def his_cards(name: str) -> dict:
    """His open cards as cc-slack lists them now, read before a record of his words goes out, so his words are checked against what each card shows and Jev reads his answer against the options of the questions it may decide."""
    view.refresh_cards(name)
    if (found := view.live_cards(name)) is None:
        raise DriveError(f"his cards could not be read ({view.stored_cards(name)[1]}), so a card these words close would stay up; nothing posted, run this again")
    return found


def closed_by(data: dict, index: int, made: dict, order: str | dict | None) -> list[tuple[str, dict]]:
    """The open job questions this record's words end, each by its job: each its waits id, `thread_card` or `card:<thread_ts>` topic names or whose card his message sits under, whatever thread the record names; an answer naming only questions that already ended is refused, posting nothing, since his answer on each is recorded already."""
    attrs = made.get("attr") or {}
    named = superseded.closing(data, made["kind"], attrs | {"order": order or attrs.get("order")}, made.get("topic"), made.get("re") or [], made.get("url"))
    if questions := [(job_id, question) for job_id, question in named if question["closed"] is None]:
        return questions
    if made["kind"] != "answer" or "undone" in attrs or not (attrs.get("job") or attrs.get("thread_card")):
        return []
    if not named:
        raise DriveError(
            f"{index}: refused, nothing posted: no job of this drive keeps the question this answer names by its waits id,"
            " thread_card or card topic, so it answers nothing he was asked: name the waits id of the card he answered"
        )
    raise DriveError(
        f"{index}: refused, nothing posted: the question this answer names ended already, so his answer on it is"
        " recorded already: a tap or its number typed needs nothing more, and words he wrote are a message from him:"
        ' a question of fact, an order, one proposing a way such as "why not just X?" included, which replaces his'
        " tap with X, or a ruling"
    )


def sent(name: str, lane: str, posts: list[dict]) -> list[str]:
    """Post the records as one cci batch from the lane, binding the session running it to no lane, and return each one's seq."""
    try:
        done = subprocess.run(
            ["cci", "post", "--drive", name, "--lane", lane, "--batch", "-"],
            input=json.dumps([item | {"json": True} for item in posts]),
            capture_output=True,
            text=True,
            env=reads.posting(),
            timeout=POST_SECONDS,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise DriveError(f"cci post: {error}") from None
    if done.returncode:
        raise DriveError(f"{done.stdout}{done.stderr}".strip())
    return [str(json.loads(line)["seq"]) for line in done.stdout.splitlines() if line.strip()]


def post(item: dict, data: dict, other: str, session: str | None) -> dict:
    """The cci record an item posts as, to the lane it names, else other, a defect's or handoff's long text cut to one line with the whole at the top of its body, as the post hook cuts a record, and sent where the drive routes it: a defect to the meta session, the communicator's owner record about the drive to the meta session alone, its answer or handoff record naming no job, whatever lane it names, to the coordinator that wrote the record its re answers, and its other owner, answer or handoff record, and any other session's handoff, to the session holding the order its attr order names, else the order of the job it answers, else the newest order in its thread; an owner record naming no about, or an item whose re names several seqs, is refused."""
    drive = data["name"]
    if item["kind"] not in RECORD["properties"]["kind"]["enum"]:
        raise DriveError(f"record posts owner, answer, handoff and defect records; a {item['kind']} goes through the cci `post` tool, whose hook reads it")
    text, attrs = item["text"], dict(item.get("attrs") or {})
    made = {"kind": item["kind"]}
    for key in ("url", "resolves", "topic", "body", "id"):
        if item.get(key):
            made[key] = item[key]
    if re := item.get("re"):
        if isinstance(re, list):
            raise DriveError(
                f"re names one seq, not {len(re)}: cci stores one record per seq it answers, so name the newest record this"
                " answers, whose own re reaches the rest; nothing posted"
            )
        made["re"] = [re]
    if waits := item.get("waits"):
        if not (named := state.WAITS_ID.fullmatch(waits)):
            raise DriveError(f"{waits} is no question card's waits id, <drive>:<j>-<n>; a PR card's tap needs no record")
        if named[1] != drive:
            raise DriveError(f"{waits} is a card of drive {named[1]}, not {drive}: record its answer in that drive")
        attrs |= {"job": state.waits_job(data, waits), "waits": waits}
    for attr in ("thread", "ruling", "thread_card", "about"):
        if item.get(attr):
            attrs[attr] = item[attr]
    if unsaid := state.unabout(item["kind"], attrs):
        raise DriveError(unsaid)
    lanes = ["root", "communicator", "meta", *map(state.project_lane, data["subcoordinators"])]
    made["to"] = state.routed({drive: data}, session, item["kind"], attrs, [f"{item.get('to') or other}@{drive}"], made.get("re") or [])
    if unread := [entry.partition("@")[0] for entry in made["to"] if entry.partition("@")[0] not in lanes]:
        raise DriveError(f"to {', '.join(unread)}: no session of drive {drive} reads that lane; name one of {', '.join(lanes)}")
    if item.get("undone"):
        attrs["undone"], text = text, f"undone: {text}"
    made |= {"text": text} | ({"attr": attrs} if attrs else {})
    return limits.shortened(made) or made
