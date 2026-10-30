from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from typing import Protocol

from . import actions


class Clock(Protocol):
    def now(self) -> datetime: ...


class Book:
    """Generation-checked edits over the actions store; a change raising ValueError writes nothing."""

    def __init__(self, store: actions.Store, shell: Clock):
        self.store = store
        self.shell = shell

    def stamp(self) -> str:
        return actions.stamp(self.shell.now())

    def ensure(self, container: str, owner: str) -> actions.Incident:
        return self.store.create(actions.Incident(container, owner, 0, self.stamp(), {}))

    def load(self, container: str) -> actions.Incident:
        return self.store.load(container)

    def containers(self, prefix: str) -> list[str]:
        return [name for name in self.store.ids() if name.startswith(prefix)]

    def edit(self, container: str, change: Callable[[actions.Incident], object]) -> object:
        while True:
            try:
                with self.store.edit(container) as incident:
                    return change(incident)
            except actions.StaleGeneration:
                continue

    def attempt(self, container: str, change: Callable[[actions.Incident], object]) -> bool:
        try:
            self.edit(container, change)
        except (ValueError, actions.UnknownAction):
            return False
        return True

    def as_owner(self, container: str, owner: str, change: Callable[[actions.Incident], object]) -> bool:
        """Apply `change` only while `owner` holds the container at the generation read; a transfer in between refuses it."""
        incident = self.store.load(container)
        if incident.owner != owner:
            return False
        try:
            with self.store.owned(container, incident.owner_generation) as live:
                change(live)
        except (actions.StaleGeneration, ValueError, actions.UnknownAction):
            return False
        return True

    def known(self, container: str, action_id: str) -> actions.Action | None:
        return self.store.load(container).actions.get(action_id) if self.store.path(container).is_file() else None

    def accept(self, container: str, action_id: str, kind: str, target: str, authority: str, deadline: datetime | None) -> tuple[actions.Action, bool]:
        if known := self.known(container, action_id):
            return known, False

        def change(incident: actions.Incident) -> tuple[actions.Action, bool]:
            created = action_id not in incident.actions
            action = incident.accept(action_id, kind, target, authority, self.shell.now())
            if created and deadline:
                action.deadline = actions.stamp(deadline)
            return action, created

        return self.edit(container, change)

    def actions(self, container: str, kind: str | None = None, status: str | None = None) -> list[actions.Action]:
        return [action for action in self.store.load(container).actions.values() if (kind is None or action.kind == kind) and (status is None or action.status == status)]

