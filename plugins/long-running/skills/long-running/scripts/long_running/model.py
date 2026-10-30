from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone

STATUSES = ("accepted", "started", "completed", "verified", "failed", "unverifiable")
OPEN = ("accepted", "started", "completed", "unverifiable")
TERMINAL = ("verified", "failed")


class StaleGeneration(RuntimeError):
    """The stored owner generation moved past the writer's, so the writer no longer owns the incident."""


class UnknownAction(KeyError):
    """No action with this id exists in the incident."""


def stamp(at: datetime) -> str:
    return at.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_stamp(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


@dataclass
class Action:
    incident_id: str
    action_id: str
    kind: str
    target: str
    authority_ref: str | None
    input_revision: int
    owner_generation: int
    accepted_at: str
    dispatch_id: str | None = None
    started_at: str | None = None
    deadline: str | None = None
    status: str = "accepted"
    response: dict | None = None
    verification_receipt: dict | None = None
    reason: str | None = None

    @classmethod
    def load(cls, record: dict) -> Action:
        return cls(**record)

    def overdue(self, now: datetime) -> bool:
        return self.status in OPEN and self.deadline is not None and parse_stamp(self.deadline) <= now


class ActionState:
    def action(self, action_id: str) -> Action:
        if action_id not in self.actions:
            raise UnknownAction(action_id)
        return self.actions[action_id]

    def of_kind(self, kind: str) -> list[Action]:
        return [action for action in self.actions.values() if action.kind == kind]

    def accept(self, action_id: str, kind: str, target: str, authority_ref: str | None, at: datetime) -> Action:
        if action_id not in self.actions:
            self.actions[action_id] = Action(
                self.incident_id, action_id, kind, target, authority_ref, self.input_revision, self.owner_generation, stamp(at)
            )
        return self.actions[action_id]

    def start(self, action_id: str, at: datetime, deadline: datetime | None = None, dispatch_id: str | None = None) -> Action:
        action = self.action(action_id)
        if action.status != "accepted":
            raise ValueError(f"{action_id} is {action.status}, not accepted")
        action.status, action.started_at = "started", stamp(at)
        action.deadline = stamp(deadline) if deadline else None
        action.dispatch_id = dispatch_id or action.dispatch_id
        return action

    def complete(self, action_id: str, response: dict, dispatch_id: str | None = None) -> Action:
        action = self.action(action_id)
        if action.status not in ("started", "unverifiable"):
            raise ValueError(f"{action_id} is {action.status}, not started")
        action.status, action.response = "completed", response
        action.dispatch_id = dispatch_id or action.dispatch_id
        return action

    def verify(self, action_id: str, receipt: dict) -> Action:
        action = self.action(action_id)
        if action.status in TERMINAL:
            raise ValueError(f"{action_id} is already {action.status}")
        action.status, action.verification_receipt = "verified", receipt
        return action

    def fail(self, action_id: str, reason: str) -> Action:
        action = self.action(action_id)
        if action.status in TERMINAL:
            raise ValueError(f"{action_id} is already {action.status}")
        action.status, action.reason = "failed", reason
        return action

    def lose(self, action_id: str) -> Action:
        action = self.action(action_id)
        if action.status != "started":
            raise ValueError(f"{action_id} is {action.status}, not started")
        action.status, action.reason = "unverifiable", "response lost; settle from external state"
        return action

    def decide(self, key: str, text: str, at: datetime) -> bool:
        if any(decision["key"] == key for decision in self.decisions):
            return False
        self.decisions.append({"key": key, "text": text, "at": stamp(at), "resolved_at": None})
        return True

    def resolve(self, key: str, at: datetime) -> None:
        for decision in self.decisions:
            if decision["key"] == key and decision["resolved_at"] is None:
                decision["resolved_at"] = stamp(at)

    def open_decisions(self) -> list[dict]:
        return [decision for decision in self.decisions if decision["resolved_at"] is None]

    def milestone(self, name: str, text: str, at: datetime) -> bool:
        if any(entry["name"] == name for entry in self.milestones):
            return False
        self.milestones.append({"name": name, "text": text, "at": stamp(at)})
        return True

    def reached(self, name: str) -> dict | None:
        return next((entry for entry in self.milestones if entry["name"] == name), None)



@dataclass
class Incident(ActionState):
    incident_id: str
    owner: str
    owner_generation: int
    opened_at: str
    facts: dict
    input_revision: int = 1
    status: str = "open"
    pending_owner: str | None = None
    actions: dict[str, Action] = field(default_factory=dict)
    decisions: list[dict] = field(default_factory=list)
    milestones: list[dict] = field(default_factory=list)

    @classmethod
    def load(cls, record: dict) -> Incident:
        actions = {key: Action.load(value) for key, value in record.pop("actions").items()}
        return cls(**record, actions=actions)

    def dump(self) -> dict:
        return asdict(self)
