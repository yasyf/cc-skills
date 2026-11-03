"""Every check a launch's line and brief pass, one implementation: `desk-launch.py` runs them all before it posts, and the orca desk runner runs them when it reads a launch line. STDLIB ONLY."""

from __future__ import annotations

import re
import subprocess
import tempfile
from pathlib import Path

import lane_brief
from long_running import lanes

INCIDENT_MODEL = "incident"
MODELS = re.compile(r"opus|sonnet|fable|astra|codex|sol|incident|claude-[\w.-]+|gpt-[\w.-]+")
EFFORTS = ("low", "medium", "high", "xhigh", "max")
SLICE_LINE = "Register slice:"
SLICE_HEADER = re.compile(rf"^{SLICE_LINE}", re.MULTILINE)
INCIDENT_ANNOTATION = re.compile(r"^ccx:.*\bincident=([\w.-]+)", re.MULTILINE)
NAMES_NOTHING = "none"
ROLE = re.compile(r"^ccx:(?:.*[ \t])?role=([\w-]+)", re.MULTILINE)
FIX_ROLES = frozenset({"fix", INCIDENT_MODEL})
CCX_KEY = r"^ccx:(?:.*[ \t])?{}(?:\s|$)"
PLACE = re.compile(CCX_KEY.format("place=(remote|local)"), re.MULTILINE)
FABLE_MODEL = re.compile(r"(^|[-_.])fable([-_.0-9]|$)", re.IGNORECASE)


def brief_incidents(brief: Path) -> set[str]:
    return set(INCIDENT_ANNOTATION.findall(brief.read_text())) if brief.is_file() else set()


def incident_fix(lane: str, model: str, brief: Path, role: str) -> bool:
    """Whether a launch is an incident fix lane's: the incident alias, or a brief whose `ccx:` line says `role=incident` or names an `incident=`, and in either case a launch and brief naming no role but fix or incident, for a lane whose name names no evidence, proof, closeout, desk, or audit role."""
    roles = (set(ROLE.findall(brief.read_text())) if brief.is_file() else set()) | ({role} if role else set())
    return roles <= FIX_ROLES and not lanes.NOT_FIX_ROLES & set(lane.split("-")) and (model == INCIDENT_MODEL or INCIDENT_MODEL in roles or bool(brief_incidents(brief) - {NAMES_NOTHING}))


def not_a_model(model: str) -> str:
    return f"{model} is not a model orca-launch.sh starts: opus, sonnet, fable, claude-*, sol, codex, incident, astra, or gpt-*"


def launch_faults(lane: str, model: str, effort: str, brief: Path, role: str = "") -> list[str]:
    """Every reason this launch cannot start, each naming its fix."""
    faults = [not_a_model(model)] if not MODELS.fullmatch(model) else []
    if effort not in EFFORTS:
        faults.append(f"{effort} is not an effort: {', '.join(EFFORTS)}")
    if not brief.is_file():
        return [*faults, f"brief {brief} is not a file"]
    if not SLICE_HEADER.search(brief.read_text()) and not (model == INCIDENT_MODEL or incident_fix(lane, model, brief, role)):
        faults.append(f"brief {brief} has no `{SLICE_LINE} lane=<name> step=<n>` line; add one and run `rulings.py slice --refresh {brief}`")
    return faults


def wrong_lane_fault(lane: str, model: str, effort: str, now: bool, role: str, brief: Path) -> str:
    """Why a `brief.md` in another lane's directory cannot launch this lane, or empty."""
    if brief.name != "brief.md" or brief.parent.name == lane:
        return ""
    order = f"launch {brief.parent.name}{' NOW' if now else ''} {model} {effort}{f' role={role}' if role else ''} brief={brief}"
    return f"brief {brief} is lane {brief.parent.name}'s, and a lane keeps one name from launch on; post `orca-desk: {order}`"


def scale_fault(lane: str, model: str, brief: Path, role: str = "") -> str:
    """The `Scale:` line this launch's brief owes and lacks, or empty; no incident lane owes one."""
    if INCIDENT_MODEL in (model, role) or incident_fix(lane, model, brief, role) or not lane_brief.owes_scale(lane, brief.read_text(), brief):
        return ""
    return lane_brief.no_scale(brief)


def mac_pinned(model: str, brief_text: str, fast: bool = False) -> bool:
    """Whether a launch runs only on the Mac at any load: Fable and the fast and incident routes, which no Sprite starts, and a brief whose author wrote `place=local`."""
    placed = PLACE.search(brief_text)
    return bool(FABLE_MODEL.search(model) or model == INCIDENT_MODEL or fast or (placed and placed[1] == "local"))


def carry_fault(brief: Path, repo: str) -> str:
    """Why a Sprite cannot receive a file or ccn entity `brief` names, from the `lr carry` that `worker-launch.sh` runs before it prepares one, or empty."""
    with tempfile.TemporaryDirectory() as into:
        done = subprocess.run(["lr", "carry", str(brief), "--into", into, "--box", into, "--repo", repo], capture_output=True, text=True)
    return done.stderr.strip().removeprefix("lr: ") if done.returncode else ""


def preflight_faults(lane: str, model: str, effort: str, now: bool, role: str, brief: Path, repo: str) -> list[str]:
    """All of a launch's faults at once, so a brief author fixes every missing line, and every reference no Sprite can receive, in one round."""
    faults = launch_faults(lane, model, effort, brief, role)
    if wrong := wrong_lane_fault(lane, model, effort, now, role, brief):
        faults.append(wrong)
    if brief.is_file() and (scale := scale_fault(lane, model, brief, role)):
        faults.append(scale)
    if brief.is_file() and not mac_pinned(model, brief.read_text()) and (carry := carry_fault(brief, repo)):
        faults.append(carry)
    return faults
