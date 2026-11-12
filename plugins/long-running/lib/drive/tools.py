import argparse
import contextlib
import io
import json
import os
import shlex
import subprocess
from pathlib import Path

from drive import brief, cli, limits, orca, reads, render, settled, slack, state, superseded, talk, view
from drive.state import DriveError

UNTYPED = {"help", "drive", "json"}
OPS = {"order": ("add", "set", "posted", "told"), "job": ("add", "set", "brief"), "pr": ("add", "set"), "incident": ("open", "stop", "freeze", "release")}
ONE_VERB = {"heard": ("heard",), "handler": ("handler",), "status": ("status",), "search_plan": ("plan", "search")}
POST_SECONDS = 60
UNSENT = (
    "no record went out, so while cci is down reach the session it was for with ListAgents, then SendMessage to"
    " `{drive}-coordinator`, `{drive}-communicator` or `{drive}-meta` carrying it, and post it once cci is back; a"
    " disconnect is never a reason a message did not go, by his ruling 4535fb1: \"use regualr claude code sesion"
    " messaging until it sback then there sno excuse\""
)
CHAINED = "<id>"
RECORDS = {"order": ("orders", "order"), "incident": ("orders", "order"), "job": ("jobs", "job")}
DRY_RUN = {"type": "boolean", "description": "check each op as a call does, print the drive command or record each would run, and run nothing"}
DRIVE = {"type": "string", "description": "the drive this call acts on, as --drive names it; default the drive bound to this session, else the only drive"}
OPS_HELP = (
    "{lead} Checks every op before any runs, each one verb with its flags as fields, then runs them in order and returns one"
    " line per op. An op the check refuses runs none of them, and the call names every refused op with its fix: fix each"
    " and send the whole call again. {saves}"
)
ONE_SAVE = ("order", "job")
SAVED_ONCE = "The call saves the drive once, so a refusal while it runs writes nothing either."
KEPT_BEFORE = "A refusal while it runs keeps each op before it, since {why}: send the ops it names unrun again."
STARTS_OUTSIDE = {"pr": "pr add starts the lander", "incident": "open and freeze start an evidence record and a freeze"}
LEADS = {
    "order": (
        "His orders: add records one from his words, set changes one, posted records the communicator's post on one, told records"
        " the one fix-live note in the thread the bug it fixes was reported in, or in its reporter's group DM with him and the bot."
    ),
    "job": "The drive's jobs: add starts one on an order's parts, set changes one, brief writes its brief.",
    "pr": "The drive's PRs: add records one a job opened, set changes one.",
    "incident": (
        "Incidents: open adds his order as one, with its fix and diagnosis jobs and its evidence record; stop adds the"
        " immediate fix that turns the failing path off and lands at once as an incident fix until the root fix replaces"
        " it; freeze holds the service's other releases; release reopens the stop when a release starts."
    ),
}
TEXT = {"type": "string"}
RECORD_ORDER = (
    f'the order the communicator\'s owner record about the drive goes to, written before it goes out so his words reach the meta session'
    f' holding an order: the fields of drive order add for a new "{state.TOOLING} <what his words ask for>" order with its title, added'
    f' from his message at url in his thread, or the id of the open "{state.TOOLING}" order his words carry on, which takes them as a new'
    " part; left out, they carry on the open one the record's attr order, job or thread names, and with none the record is refused."
    " The record goes out naming it as attr order, and the part it added as attr part, <o>:<part>"
)
RECORD = {
    "type": "object",
    "properties": {
        "kind": {
            "enum": ["owner", "answer", "handoff", "defect"],
            "description": (
                "handoff asks the session holding the order its attrs.order names for a step only it takes on that order, such as"
                " redrawing its card, with no re, since cci also delivers a record to the writer of the record its re names; a"
                " report, ready or ask goes through the cci `post` tool instead, whose hook reads it"
            ),
        },
        "text": {
            "type": "string",
            "description": "his words verbatim for owner, the tapped label or his words for answer, the step asked for handoff, the refusal verbatim for defect",
        },
        "url": {
            "type": "string",
            "description": (
                "his message's or the card's permalink; it closes no card, so his words under a question card that answer"
                " nothing it asks, such as feedback on the drive, leave it up: waits settles his question card, thread_card"
                " withdraws it, and only the coordinator's --rework naming its PR, on his order or the job carrying it,"
                " withdraws a PR card. A record that closes his card, settles a question or names a ruling is refused"
                " unless his message or card answer here holds its text, and the ruling's words above Notes:"
            ),
        },
        "thread": {"type": "string", "description": "the thread it is about, kept as attr thread"},
        "waits": {
            "type": "string",
            "description": (
                "the waits id, <drive>:<j>-<seq>, of the question card his tap or words answer: answers ask <seq> with attr"
                " job=<j>, and the card settles in the same call, showing his words with no buttons; for an ask his recorded"
                " words settled before any card drew, its ask record's waits id with his answer as ruling settles the job's"
                " question kept in that ask's file, so no card draws"
            ),
        },
        "thread_card": {
            "type": "string",
            "description": (
                "the waits id his message's thread_card names when his owner words under a question card change what it asks,"
                " such as a rework or a correction: kept as attr thread_card, and the card is withdrawn in the same call, showing"
                " his words; left out for words that leave it asking, such as feedback on the drive, so the card stays up"
            ),
        },
        "undone": {"type": "boolean", "description": "an Undo tap: attr undone=<text> and the text undone: <text>"},
        "ruling": {"type": "string", "description": "a ruling's cc-notes answer id, as attr ruling, whose words above Notes: his message at url must hold"},
        "about": {
            "enum": list(state.ABOUTS),
            "description": (
                f'required on an owner record, as attr about: "{state.ABOUT_DRIVE}" for drive tooling, {state.DRIVE_TOOLING},'
                " his ruling or feedback on any of it included, which reaches the meta session alone whatever lane to names,"
                " holding the order its order field adds or carries on;"
                f' "{state.ABOUT_HIS_WORK}" for everything else, his product orders and the code they change, which reaches'
                " the coordinator session hearing its order"
            ),
        },
        "re": {
            "type": "string",
            "description": "the seq this answers, when no waits id names one; a reply to several records names the newest, whose own re reaches the rest",
        },
        "resolves": {"type": "array", "items": TEXT, "description": "seqs this closes"},
        "topic": {"type": "string", "description": "a pairing key, such as card:<thread_ts> for a card with no ask record"},
        "to": {
            "type": "string",
            "description": (
                "the lane it goes to in place of the default, the coordinator's from the communicator and the communicator's"
                " from any other session; a defect goes to the meta session, the communicator's owner record about the drive"
                " to the meta session alone, and its other owner, answer or handoff record to the session holding the order its"
                " attrs.order names, else the order of the job it answers, else its thread's newest order, whatever lane this names"
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
    item = RECORD | {"properties": RECORD["properties"] | {"order": {"anyOf": [TEXT, schema(leaf("order", "add"))], "description": RECORD_ORDER}}}
    return [
        {
            "name": "heard",
            "description": (
                "Record that he wrote, each time he does. Returns heard, or away when away is set, and nothing to act on:"
                " the drive's watch posts his catch-up through a handler it starts."
            ),
            "inputSchema": {"type": "object", "properties": {**schema(leaf("heard"))["properties"], "drive": DRIVE, "dry_run": DRY_RUN}},
        },
        {
            "name": "handler",
            "description": (
                "Start a long-running:handler on its brief as its own claude --bg session, recording that he wrote when the"
                " brief carries his words, so his message takes the ack and this one call. Returns started <its id>; the"
                " line it ends on comes back as one cci done record from lane handler when it ends, and its session ends"
                " with it. One message of his has one handler at a time: it refuses a brief while a live handler holds the"
                " same record, the same message of his, or his message in the thread of a record that reaches none, and"
                " keeps a refused record's brief for that handler's done record to name."
            ),
            "inputSchema": {"type": "object", "properties": {**schema(leaf("handler"))["properties"], "drive": DRIVE, "dry_run": DRY_RUN}, "required": ["brief"]},
        },
        {
            "name": "record",
            "description": (
                "Send his words (owner), his answer to a card (answer), a step the session holding an order owes on it"
                " (handoff) or a tool's refusal (defect) to another session of the drive in one call, as this session's role,"
                " a handler's included: a defect reaches the meta session, the communicator's handoff the session holding the"
                " order its attrs.order names, and an owner record names about, refused without it: the drive's owner record"
                " about the drive, a ruling on how it works included, reaches the meta session alone holding its order, which"
                f' the call adds first, a new "{state.TOOLING}" order or his words as a part of the open one they carry on, as its'
                " order field says, and one about his work the coordinator hearing its order."
                " A question card he answered takes waits, which fills re with the ask's seq and attr job; an"
                " Undo tap sets undone. Once the records go out, each open card his words close is closed: a question card his"
                " answer answers settles showing his words, and one his owner words under it change is withdrawn showing them;"
                " a PR card stays up, since only the coordinator's --rework naming its PR withdraws it; an answer"
                " whose waits names a card no longer waiting on him is refused, since his answer there is"
                " recorded already. A defect's or handoff's text past one 120-character line goes as that line cut at a word,"
                " the whole text atop its body. Returns each record's seq, then each card closed."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {"records": {"type": "array", "minItems": 1, "items": item}, "drive": DRIVE, "dry_run": DRY_RUN},
                "required": ["records"],
            },
        },
        *(ops_tool(group, {"add": job_add} if group == "job" else {}) for group in OPS),
        {
            "name": "status",
            "description": "Read where each order, job and PR stands, as drive status prints it: the ones named, or every open order. A handler, the coordinator or the meta session reads this, never the communicator.",
            "inputSchema": {"type": "object", "properties": {**schema(leaf("status"))["properties"], "drive": DRIVE, "dry_run": DRY_RUN}},
            "annotations": {"readOnlyHint": True},
        },
        {
            "name": "search_plan",
            "description": (
                "Search the plans the coordinator's checkout keeps in cc-notes for the words, to check a step against the plan"
                " before building it or asking. Returns the sections carrying the most of them, best first, each under its"
                " plan's title, id, status and the headings above it."
            ),
            "inputSchema": {"type": "object", "properties": {**schema(leaf("plan", "search"))["properties"], "drive": DRIVE, "dry_run": DRY_RUN}, "required": ["words"]},
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
            return "\n".join(ran(name, said, 1, ONE_VERB[tool], arguments, dry_run) or said)
        case group:
            return batch(name, group, listing(arguments, "ops", ", ".join(OPS[group])), dry_run)


def parsed_arguments(arguments: dict, shapes: dict[str, dict]) -> dict:
    """The call's arguments with each list or object a client sent as JSON text parsed, the list's items too, as the tool's schema types it."""
    return {key: parsed(key, value, shapes.get(key, {})) for key, value in arguments.items()}


def parsed(key: str, value: object, shape: dict) -> object:
    kinds = {shape.get("type"), *(choice.get("type") for choice in shape.get("oneOf") or shape.get("anyOf") or ())}
    if isinstance(value, str) and "string" not in kinds and kinds & {"array", "object"}:
        try:
            value = json.loads(value)
        except json.JSONDecodeError as error:
            raise DriveError(f"{key} is a list or object, and its text is not JSON: {error}") from None
    if isinstance(value, list) and "items" in shape:
        return [parsed(key, item, shape["items"]) for item in value]
    return value


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


def batch(name: str, group: str, ops: list[dict], dry_run: bool) -> str:
    """Check every op first, then run them in order, the order and job tools under one save of the drive."""
    if refused := refusals(name, group, ops):
        raise DriveError("\n".join([f"refused, nothing written: {len(refused)} of {len(ops)} ops refused; fix each and send the whole call again", *refused]))
    said: list[str] = []
    made = None
    index = 0
    try:
        with state.batch(name) if group in ONE_SAVE and not dry_run else contextlib.nullcontext([]) as after:
            for index, op in enumerate(ops, start=1):
                [(verb, given)] = op.items()
                words = (group, verb)
                if words == ("job", "add") and isinstance(given.get("order"), dict):
                    given = given | {"order": first(ran(name, said, index, ("order", "add"), given["order"], dry_run))}
                if verb != "add" and "id" in fields(leaf(*words)) and "id" not in given:
                    given = given | {"id": made}
                printed = ran(name, said, index, words, given, dry_run)
                if verb == "add":
                    made = first(printed)
    except DriveError as error:
        if group in ONE_SAVE:
            raise DriveError(f"{error}; nothing written: fix it and send the whole call again") from None
        left = f"; ops {index + 1}-{len(ops)} not run" if index < len(ops) else ""
        raise DriveError("\n".join([*said, f"{error}{left}"])) from None
    return "\n".join([*said, *after])


def refusals(name: str, group: str, ops: list[dict]) -> list[str]:
    """Every refusal the ops meet before any runs, a line each naming the op by its index, verb and opening words, with its fix."""
    data = state.load(name)
    lines = []
    for index, op in enumerate(ops, start=1):
        given = next(iter(op.values())) if len(op) == 1 else {}
        opening = given.get("words") or given.get("goal")
        try:
            refuse(data, name, group, op, any("add" in earlier for earlier in ops[: index - 1]))
        except DriveError as error:
            named = " ".join([group, *op]) + (f" {json.dumps(render.cut(opening, 60), ensure_ascii=False)}" if opening else "")
            lines.append(f"{index} {named} refused: {error}")
    return lines


def refuse(data: dict, name: str, group: str, op: dict, after_add: bool) -> None:
    """Raise what the op's verb refuses before it writes, read from the drive as the call found it and the adds before it: an op that is not one verb of the tool, a field or flag its verb refuses, an id no record holds or no earlier add makes, an order add added_order refuses, a "Drive tooling:" order's new words check-post finds anything in, and a job add naming no part of its order."""
    if len(op) != 1:
        raise DriveError(f"an op names one verb, not {', '.join(op) or 'none'}")
    [(verb, given)] = op.items()
    if verb not in OPS[group]:
        raise DriveError(f"the {group} tool runs {', '.join(OPS[group])}, not {verb}")
    words = (group, verb)
    chained = verb != "add" and "id" in fields(leaf(*words)) and "id" not in given
    if chained and not after_add:
        raise DriveError("name its id, since no add came before it")
    nested = given.get("order") if words == ("job", "add") and isinstance(given.get("order"), dict) else None
    args = parsed_args(name, words, given | ({"id": CHAINED} if chained else {}) | ({"order": ordered(data, name, nested)} if nested else {}))
    if group in RECORDS and not chained and "id" in given:
        cli.find(data[RECORDS[group][0]], args.id, RECORDS[group][1])
    if words == ("order", "add"):
        ordered(data, name, given)
    if words == ("order", "set") and (args.words or "").startswith(state.TOOLING):
        cli.check_tooling_words(name, args.words)
    if words == ("job", "add"):
        cli.pick_parts(args.order, cli.find(data["orders"], args.order, "order"), args.part)
        data["jobs"][state.next_id(data["jobs"], "j")] = {"order": args.order}


def ordered(data: dict, name: str, given: dict) -> str:
    """The id of the order an order add makes or returns, refused as added_order refuses before it writes: a flag it refuses, his order filed under no project or workstream, one with no short title, and a "Drive tooling:" order's words check-post finds anything in, which an open order already holding them returns unchecked. The order it makes stands in the drive the check reads, so a later op may name it."""
    args = parsed_args(name, ("order", "add"), given)
    tooling = args.words.startswith(state.TOOLING)
    if tooling and (held := next((order_id for order_id, order in state.open_orders(data).items() if order["text"] == args.words), None)):
        return held
    project, workstream = cli.filing(data, args.words, args.project, args.workstream)
    title = cli.titled(args.title)
    if tooling:
        cli.check_tooling_words(name, args.words)
    order_id = state.next_id(data["orders"], "o")
    data["orders"][order_id] = {"text": args.words, "state": "open", "parts": dict.fromkeys(args.parts or [state.whole_order(args.words)]), "project": project, "workstream": workstream, "title": title}
    return order_id


def parsed_args(name: str, words: tuple[str, ...], given: dict) -> argparse.Namespace:
    """The op's flags as the drive's parser reads them, its refusal raised as one line."""
    out = io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
        try:
            return cli.parser().parse_args([*argv(words, given), "--drive", name])
        except SystemExit:
            pass
    raise DriveError(out.getvalue().strip().splitlines()[-1])


def first(printed: list[str]) -> str:
    return printed[0].split()[0] if printed else "<id>"


def ran(name: str, said: list[str], index: int, words: tuple[str, ...], given: dict, dry_run: bool) -> list[str]:
    """Run one drive verb in this process, appending its line to said; a refusal raises naming the op and why."""
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
        why = printed[-1:] if usage else printed
        raise DriveError(f"{index} {' '.join(words)} refused: {'; '.join(why)}")
    said.append(f"{index} {' '.join(words)}: {'; '.join(printed)}")
    return printed


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
    closed = [closed_by(name, data, found, index, made) for index, made in enumerate(posts, start=1)]
    closes, settles = [questions for questions, _ in closed], [settling for _, settling in closed]
    for index, (made, questions, settling) in enumerate(zip(posts, closes, settles, strict=True), start=1):
        if (questions or settling or (made.get("attr") or {}).get("ruling")) and (unsaid := unheard(data, found, made)):
            raise DriveError(f"{index}: refused, nothing posted: {unsaid}")
    ordering = [state.meta_alone(role, made["kind"], made.get("attr") or {}) for made in posts]
    if unordered := [index for index, (item, alone) in enumerate(zip(records, ordering, strict=True), start=1) if "order" in item and not alone]:
        raise DriveError(f"{unordered[0]}: refused, nothing posted: order is the order the communicator's owner record about the drive goes to; name the order any other record serves as attrs.order")
    for made, settling in zip(posts, settles, strict=True):
        if settling:
            made["resolves"] = list(dict.fromkeys([*made.get("resolves", []), settling[1]]))
    said = ordered_first(name, records, posts, ordering, dry_run)
    if dry_run:
        said += [f"{index} {lane}: {json.dumps(item, ensure_ascii=False)}" for index, item in enumerate(posts, start=1)]
        said += [f"{index} would settle {settling[0]}'s question kept in {settling[2]}, closing ask {settling[1]}" for index, settling in enumerate(settles, start=1) if settling]
    else:
        try:
            seqs = sent(name, lane, posts)
        except DriveError as error:
            raise DriveError("\n".join([*said, str(error)])) from None
        said += [f"{index} {item['kind']} {seq}" for index, (item, seq) in enumerate(zip(posts, seqs, strict=True), start=1)]
        keep_undelivered(name, posts, seqs, closes)
        said += keep_settled(name, posts, seqs, settles)
    for index, (made, questions) in enumerate(zip(posts, closes, strict=True), start=1):
        close(said, index, made, questions, dry_run)
    return "\n".join(said)


def ordered_first(name: str, records: list[dict], posts: list[dict], ordering: list[bool], dry_run: bool) -> list[str]:
    """Write, under one save of the drive and before any record goes out, the order each communicator's owner record about the drive goes to, so the order stands even when cci is down and a resent call adds none twice; a refusal writes none and posts nothing. What each write did, a line each."""
    said: list[str] = []
    if not any(ordering):
        return said
    try:
        with contextlib.nullcontext([]) if dry_run else state.batch(name) as after:
            for index, (item, made, alone) in enumerate(zip(records, posts, ordering, strict=True), start=1):
                if alone:
                    carried(name, said, index, item, made["attr"], dry_run)
    except DriveError as error:
        raise DriveError(f"{error}; nothing posted and no order written: fix it and send the whole call again") from None
    return said + after


def carried(name: str, said: list[str], index: int, item: dict, attrs: dict, dry_run: bool) -> None:
    """Write the order the communicator's owner record about the drive goes to before it goes out, so no word of his about the drive reaches the meta session without one: the new "Drive tooling:" order its order field's fields make, else his words as a new part of the open "Drive tooling:" order its order field names or its attrs or thread serve, refused when it serves none. The record names the order as attr order, and a part it added as attr part."""
    named = item.get("order")
    if isinstance(named, dict):
        if not str(named.get("words") or "").startswith(state.TOOLING):
            raise DriveError(f'{index}: refused, his words about the drive go to a "{state.TOOLING} <what they ask for>" order, so name order.words opening "{state.TOOLING}"')
        attrs["order"] = first(order_op(name, said, index, "add", new_order_fields(item) | named, dry_run))
        return
    data = state.load(name)
    order_id = named or state.served_order(data, attrs)
    order = data["orders"].get(order_id or "")
    if not order or order["state"] in state.CLOSED_ORDER_STATES or not order["text"].startswith(state.TOOLING):
        raise DriveError(
            f"{index}: refused, his words about the drive reach the meta session holding an order, and"
            f' {f"{order_id} is no open {state.TOOLING!r} order" if order_id else "no order holds this record or its thread"}:'
            f' name order, the fields of drive order add for a new "{state.TOOLING} <what his words ask for>" order with its title,'
            f' or the id of the open "{state.TOOLING}" order his words carry on'
        )
    part = state.whole_order(item["text"])
    attrs |= {"order": order_id, "part": f"{order_id}:{part}"}
    if part not in order["parts"]:
        order_op(name, said, index, "set", {"id": order_id, "parts": [*order["parts"], part]}, dry_run)


def new_order_fields(item: dict) -> dict:
    """What a new "Drive tooling:" order a record of his words adds takes from the record: kind build, from his message at its url, else in the thread it names."""
    if state.slack_message(item.get("url") or ""):
        return {"kind": "build", "from": item["url"]}
    return {"kind": "build"} | ({"thread": item["thread"]} if item.get("thread") else {})


def order_op(name: str, said: list[str], index: int, verb: str, given: dict, dry_run: bool) -> list[str]:
    """Run drive order verb as the order tool runs it, a dry run checking it as the call would."""
    if dry_run:
        try:
            refuse(state.load(name), name, "order", {verb: given}, False)
        except DriveError as error:
            raise DriveError(f"{index} order {verb} refused: {error}") from None
    return ran(name, said, index, ("order", verb), given, dry_run)


def unheard(data: dict, found: dict, made: dict) -> str | None:
    """Why a record keeping words as his answer or ruling, one closing his card, settling a question or naming a ruling, is not his: its text, or the ruling's recorded words above its notes, is not what he said at its url."""
    ruling = (made.get("attr") or {}).get("ruling")
    said = [made["text"], *([settled.his(settled.shown(ruling, data["roles"]["coordinator"]["cwd"])["body"])] if ruling else [])]
    return next((unsaid for words in said if (unsaid := settled.unsaid(words, made.get("url"), found))), None)


def keep_settled(name: str, posts: list[dict], seqs: list[str], settles: list[tuple[str, str, str] | None]) -> list[str]:
    """Keep on each question an answer settled before any card drew for it that answer's seq and the ruling it carries, so the question reads closed everywhere, the builder gate of its order included; what was settled, a line each."""
    done = []
    with state.edit(name) as data:
        for index, (made, seq, settling) in enumerate(zip(posts, seqs, settles, strict=True), start=1):
            if not settling:
                continue
            job_id, asked, file = settling
            question = state.kept_in(data["jobs"][job_id], Path(file))
            question["settled"] = {"seq": seq, "ruling": (made.get("attr") or {}).get("ruling"), "at": state.stamp()}
            done.append(f'{index} {job_id}\'s question "{render.cut(question["title"] or "", 80)}" kept in {file} is settled by {seq}, closing ask {asked} with no card drawn')
    return done


def keep_undelivered(name: str, posts: list[dict], seqs: list[str], closes: list[list[dict]]) -> None:
    """Keep each answer of his on the job that acts on it for each job it answers, the one its attr names and each whose card it closes, until drive job resume carries it there, so the watch says ANSWER undelivered for it meanwhile; an Undo tap takes the answer it undoes off those jobs."""
    answers = [(made, seq, questions) for made, seq, questions in zip(posts, seqs, closes, strict=True) if made["kind"] == "answer"]
    if not answers:
        return
    with state.edit(name) as data:
        for made, seq, questions in answers:
            attrs = made.get("attr") or {}
            named = [state.named_job(data, attrs), *(state.named_job(data, {"waits": card["card"].get("waits")}) for card in questions)]
            for job_id in dict.fromkeys(filter(None, (hearer(data, job_id) for job_id in filter(None, named)))):
                kept = data["jobs"][job_id]["undelivered"]
                if "undone" in attrs:
                    kept[:] = [answer for answer in kept if answer["words"] != attrs["undone"]]
                else:
                    kept.append({"seq": seq, "words": made["text"], "at": state.stamp(), "said": None})


def hearer(data: dict, job_id: str) -> str | None:
    """The job that acts on his answer to this job's question: the job itself, but a finished reader's builder, which builds its research: the one at work whose brief names it Researched, else none, since a builder briefed later carries the answer in its brief."""
    job = data["jobs"][job_id]
    if job["kind"] != "reader" or job["state"] not in state.FINISHED_JOB_STATES:
        return job_id
    return next(
        (
            builder_id
            for builder_id in data["orders"][job["order"]]["jobs"]
            if (builder := data["jobs"][builder_id])["kind"] == "builder"
            and builder["state"] not in state.FINISHED_JOB_STATES
            and builder["brief"]
            and (named := brief.RESEARCHED_BY.search(orca.brief_of(Path(builder["brief"]).read_text())))
            and named[1] == job_id
        ),
        None,
    )


def his_cards(name: str) -> dict:
    """His open cards as cc-slack lists them now, read before a record of his words goes out, so each card it closes is closed in the same call."""
    view.refresh_cards(name)
    if (found := view.live_cards(name)) is None:
        raise DriveError(f"his cards could not be read ({view.stored_cards(name)[1]}), so a card these words close would stay up; nothing posted, run this again")
    return found


def closed_by(name: str, data: dict, found: dict, index: int, made: dict) -> tuple[list[dict], tuple[str, str, str] | None]:
    """The question cards of his this record's words close, and else the question an answer settles that its job keeps with no card drawn, by its job, ask seq and file; an answer naming a card that no longer waits on him is refused, posting nothing."""
    attrs = made.get("attr") or {}
    questions = superseded.closing(found, made["kind"], attrs, made.get("topic"), made.get("re") or [])
    if questions or made["kind"] != "answer" or "undone" in attrs or not (attrs.get("job") or attrs.get("thread_card")):
        return questions, None
    if settling := uncarded(name, data, attrs, made.get("re") or []):
        return [], settling
    raise DriveError(
        f"{index}: refused, nothing posted: the card this answer names no longer waits on him, so his answer on it is"
        " recorded already: a tap or its number typed needs nothing more, and words he wrote are a message from him:"
        ' a question of fact, an order, one proposing a way such as "why not just X?" included, which replaces his'
        " tap with X, or a ruling"
    )


def uncarded(name: str, data: dict, attrs: dict, answered: list[str]) -> tuple[str, str, str] | None:
    """The job, ask seq and file of the question an answer settles before any card drew for it, as his recorded rulings settle one: the ask record it answers names by its `ask` attr the file its job keeps that question in, kept with no card, no refusal and nothing settling it yet."""
    if (job := data["jobs"].get(attrs.get("job") or "")) is None:
        return None
    for seq in answered:
        if (file := asked_file(name, seq)) and (question := state.kept_in(job, Path(file))) and not (question["card"] or question["refused"] or question["settled"]):
            return attrs["job"], seq, file
    return None


def asked_file(name: str, seq: str) -> str | None:
    """The kept ASK file an ask record names by its `ask` attr; None for a record of another kind."""
    try:
        found = json.loads(reads.run(["cci", "show", seq, "--json", "--drive", name], Path.cwd()))
    except reads.ReadError as error:
        raise DriveError(f"nothing posted: the ask {seq} this answer answers could not be read, so whether it closes a question with no card is unknown: {error}") from None
    return found["fields"].get("attrs", {}).get("ask") if found["kind"] == "ask" else None


def close(said: list[str], index: int, made: dict, questions: list[dict], dry_run: bool) -> None:
    """Once the record is sent, close each question card of his its words close, settled showing his answer or withdrawn showing his other words; what was done, or would be on a dry run, appended to said a line each."""
    for card in questions:
        if dry_run:
            said.append(f"{index} would {'settle' if made['kind'] == 'answer' else 'withdraw'} his card {card['url']} ({card['card'].get('waits')}), showing his words")
            continue
        try:
            said.append(f"{index} {superseded.close_card(card, made['kind'], made['text'], made.get('url') or card['url'])}")
        except slack.SlackError as error:
            raise DriveError("\n".join([*said, f"{index} cc-slack card-answer on his card {card['url']} refused: {error}; the record went out, so close the card with slack_card_answer"])) from None


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
        raise DriveError(f"cci post: {error}; {UNSENT.format(drive=name)}") from None
    if done.returncode:
        raise DriveError(f"{done.stdout}{done.stderr}".strip() + f"; {UNSENT.format(drive=name)}")
    return done.stdout.split()


def post(item: dict, data: dict, other: str, session: str | None) -> dict:
    """The cci record an item posts as, to the lane it names, else other, a defect's or handoff's long text cut to one line with the whole at the top of its body, as the post hook cuts a record, and sent where the drive routes it: a defect to the meta session, the communicator's owner record about the drive to the meta session alone, and its other owner, answer or handoff record, whatever lane it names, to the session holding the order its attr order names, else the order of the job it answers, else the newest order in its thread; an owner record naming no about, a defect typing a clock time for work already done, or an item whose re names several seqs, is refused."""
    drive = data["name"]
    if item["kind"] not in RECORD["properties"]["kind"]["enum"]:
        raise DriveError(f"record posts owner, answer, handoff and defect records; a {item['kind']} goes through the cci `post` tool, whose hook reads it")
    if item["kind"] == "defect" and (typed := limits.typed_time(f"{item['text']}\n{item.get('body') or ''}", data)):
        raise DriveError(f"{typed}; nothing posted")
    text, attrs = item["text"], dict(item.get("attrs") or {})
    made = {"kind": item["kind"]}
    for key in ("url", "resolves", "topic", "body"):
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
            raise DriveError(f"{waits} is no question card's waits id, <drive>:<j>-<seq>; a PR card's tap needs no record")
        if named[1] != drive:
            raise DriveError(f"{waits} is a card of drive {named[1]}, not {drive}: record its answer in that drive")
        attrs["job"], made["re"] = named[2], [named[3]]
    for attr in ("thread", "ruling", "thread_card", "about"):
        if item.get(attr):
            attrs[attr] = item[attr]
    if unsaid := state.unabout(item["kind"], attrs):
        raise DriveError(unsaid)
    lanes = ["root", "communicator", "meta", *map(state.project_lane, data["subcoordinators"])]
    made["to"] = state.routed({drive: data}, session, item["kind"], attrs, [f"{item.get('to') or other}@{drive}"])
    if unread := [entry.partition("@")[0] for entry in made["to"] if entry.partition("@")[0] not in lanes]:
        raise DriveError(f"to {', '.join(unread)}: no session of drive {drive} reads that lane; name one of {', '.join(lanes)}")
    if item.get("undone"):
        attrs["undone"], text = text, f"undone: {text}"
    made |= {"text": text} | ({"attr": attrs} if attrs else {})
    return limits.shortened(made) or made
