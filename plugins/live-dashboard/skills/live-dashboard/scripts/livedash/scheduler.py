from __future__ import annotations

import json
import os
import threading
import time
from collections.abc import Callable
import subprocess
from concurrent.futures import Executor, Future, ThreadPoolExecutor, wait
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from livedash import payloads, registry, secrets
from livedash.context import Context, RateLimited, failure_text
from livedash.layout import Card

WORKERS = 4
RATE_LIMIT_BACKOFF = 300.0
STALE_FACTOR = 2
BACKOFF_CAP = 4
CACHE_DIR = "cache"


def iso(seconds: float) -> str:
    return datetime.fromtimestamp(seconds, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class InlineExecutor(Executor):
    def submit(self, fn, /, *args, **kwargs) -> Future:
        future: Future = Future()
        try:
            future.set_result(fn(*args, **kwargs))
        except BaseException as failure:
            future.set_exception(failure)
        return future


def key_of(card: Card) -> str:
    return json.dumps([card.use, card.bound], sort_keys=True, default=str)


@dataclass
class Instance:
    key: str
    spec: registry.Spec
    bound: dict
    every: float | None
    cards: list[str]
    as_of: float | None = None
    payload: object | None = None
    data: dict | None = None
    error: str | None = None
    ms: int | None = None
    due: float = 0.0
    failures: int = 0
    started: float | None = None
    future: Future | None = None
    hung: bool = False


@dataclass
class Scheduler:
    dir: Path
    facts: dict
    clock: Callable[[], float] = time.time
    executor: Executor = field(default_factory=lambda: ThreadPoolExecutor(WORKERS, thread_name_prefix="card"))
    env: dict = field(default_factory=lambda: dict(os.environ))
    instances: dict[str, Instance] = field(default_factory=dict)
    cards: dict[str, Card] = field(default_factory=dict)
    lock: threading.RLock = field(default_factory=threading.RLock)

    def apply(self, cards: list[Card]) -> None:
        with self.lock:
            fresh: dict[str, Instance] = {}
            for card in cards:
                if card.spec is None:
                    continue
                key = key_of(card)
                every = registry.CADENCES[card.every]
                if key not in fresh:
                    fresh[key] = self.instances.get(key) or self.restored(Instance(key, card.spec, card.bound, every, []), card.id)
                    fresh[key].cards = []
                    fresh[key].spec = card.spec
                    fresh[key].every = every
                elif every is not None and (fresh[key].every is None or every < fresh[key].every):
                    fresh[key].every = every
                fresh[key].cards.append(card.id)
            self.instances = fresh
            self.cards = {card.id: card for card in cards}

    def restored(self, instance: Instance, card: str) -> Instance:
        path = self.dir / CACHE_DIR / f"{card}.json"
        if not path.exists():
            return instance
        cached = json.loads(path.read_text())
        if cached.get("key") != instance.key or not cached["ok"]:
            return instance
        instance.as_of = cached["as_of"]
        instance.data = cached["payload"]
        instance.due = cached["as_of"] + (instance.every or 0) if instance.every else float("inf")
        return instance

    def instance_of(self, card: str) -> Instance | None:
        return next((instance for instance in self.instances.values() if card in instance.cards), None)

    def payload_of(self, card: str):
        instance = self.instance_of(card)
        return instance.payload if instance else None

    def refresh(self, card: str) -> bool:
        with self.lock:
            if (instance := self.instance_of(card)) is None:
                return False
            instance.due = 0.0
            return True

    def tick(self) -> None:
        now = self.clock()
        with self.lock:
            for instance in self.instances.values():
                if instance.future is not None:
                    if instance.future.done():
                        self.settle(instance, now)
                    elif now - instance.started > instance.spec.timeout:
                        instance.hung = True
                elif now >= instance.due:
                    self.launch(instance, now)

    def launch(self, instance: Instance, now: float) -> None:
        context = Context(self.dir, self.facts, datetime.fromtimestamp(now, timezone.utc), instance.spec.timeout, instance.payload, self.payload_of, self.summary)
        instance.started = now
        instance.hung = False
        instance.due = float("inf")
        instance.future = self.executor.submit(self.call, instance.spec, context, instance.bound)

    def call(self, spec: registry.Spec, context: Context, bound: dict):
        started = time.monotonic()
        result = spec.fn(context, **bound)
        return result, int((time.monotonic() - started) * 1000)

    def settle(self, instance: Instance, now: float) -> None:
        future, instance.future, instance.hung = instance.future, None, False
        try:
            result, instance.ms = future.result()
            data = self.vetted(instance.spec, result)
        except RateLimited as failure:
            self.failed(instance, now, str(failure), RATE_LIMIT_BACKOFF)
            return
        except Exception as failure:
            text = failure_text(failure) if isinstance(failure, (subprocess.SubprocessError, OSError)) else f"{type(failure).__name__}: {failure}"[:300]
            self.failed(instance, now, text, None)
            return
        instance.payload, instance.data, instance.as_of, instance.error, instance.failures = result, data, now, None, 0
        instance.due = now + instance.every if instance.every else float("inf")
        self.store(instance)

    def vetted(self, spec: registry.Spec, result) -> dict:
        if not isinstance(result, spec.payload):
            raise TypeError(f"{spec.id} returned {type(result).__name__}, not the {spec.payload.__name__} its signature names")
        if found := payloads.problems(result):
            raise ValueError("; ".join(found))
        data = result.json()
        if names := secrets.hits(json.dumps(data, default=str), self.env):
            raise ValueError(f"secret-shaped value ({', '.join(names)}); the payload was dropped")
        return data

    def failed(self, instance: Instance, now: float, error: str, wait: float | None) -> None:
        instance.error = secrets.redacted(error, self.env)
        instance.failures += 1
        if wait is None and instance.every:
            wait = min(instance.every * 2**instance.failures, BACKOFF_CAP * instance.every)
        instance.due = now + wait if wait else float("inf")

    def store(self, instance: Instance) -> None:
        directory = self.dir / CACHE_DIR
        directory.mkdir(parents=True, exist_ok=True)
        body = json.dumps({"as_of": instance.as_of, "ok": True, "error": None, "payload": instance.data, "key": instance.key}, default=str)
        for card in instance.cards:
            staged = directory / f".{card}.json.{os.getpid()}"
            staged.write_text(body)
            staged.replace(directory / f"{card}.json")

    def status(self, instance: Instance, now: float) -> str:
        if instance.hung:
            return "hung"
        if instance.error:
            return "error"
        if instance.as_of is None:
            return "pending"
        if instance.every and now - instance.as_of > STALE_FACTOR * instance.every:
            return "stale"
        return "ok"

    def envelopes(self, layout_cards: list[Card]) -> list[dict]:
        now = self.clock()
        with self.lock:
            out = []
            for card in layout_cards:
                base = {"id": card.id, "use": card.use, "title": card.title, "question": card.question, "section": card.section, "width": card.width, "pinned": card.pinned, "every": card.every}
                instance = self.instance_of(card.id)
                if instance is None:
                    out.append(base | {"kind": None, "payload": None, "as_of": None, "status": "error", "error": card.error, "ms": None, "actions": []})
                    continue
                out.append(
                    base
                    | {
                        "kind": instance.spec.payload.kind,
                        "payload": instance.data,
                        "as_of": iso(instance.as_of) if instance.as_of else None,
                        "status": self.status(instance, now),
                        "error": instance.error,
                        "ms": instance.ms,
                        "actions": sorted(instance.spec.actions),
                    }
                )
            return out

    def summary(self) -> list[dict]:
        return [{key: value for key, value in envelope.items() if key != "payload"} for envelope in self.envelopes(list(self.cards.values()))]

    def run_once(self, card: str) -> None:
        with self.lock:
            instance = self.instance_of(card)
            self.launch(instance, self.clock())
        wait([instance.future])
        with self.lock:
            self.settle(instance, self.clock())
