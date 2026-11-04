from __future__ import annotations

from captain_hook import Allow, Annotated, BaseHookEvent, Event, HookResult, Input, Tool, Warn, on

from .compaction_handoff import Bound, drive_entry
from .lane_landing import DRIVE_ENV, FIXTURES
from .owner_facing import post_values

OVER_THE_PARENTS_HEAD = frozenset({"owner", "owner-relay"})
SUB_DRIVE = {"CLAUDE_LONG_RUNNING_DRIVE": "brook--billing", "CLAUDE_CONFIG_DIR": str(FIXTURES / "sub-drive-config")}


def addressed(evt: BaseHookEvent) -> set[str]:
    posts = [values for call in evt.command.calls("cci") if (values := post_values(evt, call)).get("words", ())[:1] == ("post",)]
    return {lane.partition("@")[0] for values in posts for value in values.get("to", ()) for lane in str(value).split(",")}


class OverTheParentsHead:
    def check(self, evt: BaseHookEvent) -> bool:
        return bool(addressed(evt) & OVER_THE_PARENTS_HEAD) and bool((drive_entry(evt) or {}).get("parent"))


PARENT_ONLY = "Warning, nothing was blocked: A sub-drive reports only to its parent's coordinator. Post with `--to root@<parent drive>`, the address in your brief, and the parent relays what the owner must see."


@on(
    Event.PreToolUse,
    only_if=[Bound(), Tool("Bash"), OverTheParentsHead()],
    skip_if=[Annotated("raw")],
    tests={
        Input(command="cci post --lane root --drive brook--billing --kind report --to owner-relay 'billing is up'", env=SUB_DRIVE): Warn(pattern=r"^Warning, nothing was blocked: A sub-drive reports only"),
        Input(command="cci post --lane root --kind ask --to owner-relay@brook 'tap this PR'", env=SUB_DRIVE): Warn(),
        Input(command="cci post --lane invoices --kind ask --to root,owner 'tap this PR'", env=SUB_DRIVE, agent_id="a1b2c3"): Warn(),
        Input(command="cci post --lane root --kind report --to owner-relay 'billing is up'  # ccx:raw", env=SUB_DRIVE): Allow(),
        Input(command="cci post --lane root --kind report --to root@brook 'billing is up'", env=SUB_DRIVE): Allow(),
        Input(command="cci tail --drive brook --to owner-relay", env=SUB_DRIVE): Allow(),
        Input(command="cci post --lane root --kind report --to owner-relay 'the drive is up'", env=DRIVE_ENV): Allow(),
        Input(command="cci post --lane root --kind report --to owner-relay 'no drive'"): Allow(),
    },
)
def sub_drives_report_to_their_parent(evt: BaseHookEvent) -> HookResult:
    return evt.context(PARENT_ONLY)
