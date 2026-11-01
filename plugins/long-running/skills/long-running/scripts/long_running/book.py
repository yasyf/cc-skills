from __future__ import annotations

from collections.abc import Callable, Mapping
from datetime import datetime
from typing import Protocol

from . import actions
from .sqlite_store import ReadVersion


class Clock(Protocol):
    def now(self) -> datetime: ...


class Book:
    """Generation-checked edits over the actions store; a change raising ValueError writes nothing."""

    def __init__(self, store: actions.Store, shell: Clock):
        self.store = store
        self.shell = shell

    def stamp(self) -> str:
        return actions.stamp(self.shell.now())

    def ensure(self, container: str, owner: str) -> None:
        self.store.ensure(actions.Incident(container, owner, 0, self.stamp(), {}))

    def load(self, container: str) -> actions.Incident:
        return self.store.load(container)

    def exists(self, container: str) -> bool:
        return self.store.exists(container)

    def fact(self, container: str, key: str, *default):
        return self.store.fact(container, key, *default)

    def fact_path(self, container: str, keys, *default):
        return self.store.fact_path(container, keys, *default)

    def facts(self, container: str, keys=None) -> dict:
        return self.store.facts(container, keys)

    def read_facts(self, container: str, read: Callable[[Mapping[str, object]], object]) -> object:
        return self.store.read_facts(container, read)

    def read_facts_with_version(self, container: str, read: Callable[[Mapping[str, object]], object]) -> tuple[object, ReadVersion]:
        return self.store.read_facts_with_version(container, read)

    def unchanged(self, container: str, version: ReadVersion) -> bool:
        return self.store.unchanged(container, version)

    def fact_children(self, container: str, key: str, keys) -> dict:
        return self.store.fact_children(container, key, keys)

    def fact_entries(self, container: str, key: str, field: str, values) -> dict:
        return self.store.fact_entries(container, key, field, values)

    def header(self, container: str) -> dict:
        return self.store.header(container)

    def action(self, container: str, key: str) -> actions.Action:
        return self.store.action(container, key)

    def containers(self, prefix: str) -> list[str]:
        return [name for name in self.store.ids() if name.startswith(prefix)]

    def edit(self, container: str, change: Callable[[actions.Transaction], object]) -> object:
        while True:
            try:
                with self.store.transaction(container) as incident:
                    return actions.detached(change(incident))
            except actions.StaleGeneration:
                continue

    def attempt(self, container: str, change: Callable[[actions.Transaction], object]) -> bool:
        try:
            self.edit(container, change)
        except (ValueError, actions.UnknownAction):
            return False
        return True

    def as_owner(self, container: str, owner: str, change: Callable[[actions.Transaction], object]) -> bool:
        """Apply `change` only while `owner` holds the container at the generation read; a transfer in between refuses it."""
        current_owner, generation = self.store.ownership(container)
        if current_owner != owner:
            return False
        try:
            with self.store.transaction(container, generation=generation) as live:
                change(live)
        except (actions.StaleGeneration, ValueError, actions.UnknownAction):
            return False
        return True

    def known(self, container: str, action_id: str) -> actions.Action | None:
        return self.store.find_action(container, action_id) if self.store.exists(container) else None

    def accept(self, container: str, action_id: str, kind: str, target: str, authority: str, deadline: datetime | None) -> tuple[actions.Action, bool]:
        if known := self.known(container, action_id):
            return known, False

        def change(incident: actions.Transaction) -> tuple[actions.Action, bool]:
            created = action_id not in incident.actions
            action = incident.accept(action_id, kind, target, authority, self.shell.now())
            if created and deadline:
                action.deadline = actions.stamp(deadline)
            return action, created

        return self.edit(container, change)

    def actions(self, container: str, kind: str | None = None, status: str | None = None, *, statuses=None) -> list[actions.Action]:
        return self.store.actions(container, kind, status, statuses=statuses)

    def blocker_candidates(self, container: str) -> list[actions.Action]:
        return self.store.blocker_candidates(container)

    def direct_merge_candidates(self, container: str) -> list[actions.Action]:
        return self.store.direct_merge_candidates(container)

    def actions_with_version(self, container: str, kind: str | None = None, status: str | None = None, *, statuses=None) -> tuple[list[actions.Action], ReadVersion]:
        return self.store.actions_with_version(container, kind, status, statuses=statuses)
