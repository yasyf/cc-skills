from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType


def chains(keys: list[str], branches: dict[str, tuple[str, str]]) -> list[list[str]]:
    """The PRs grouped into stacks, bottom first: a PR sits on another of its repository when its base branch is the other's head branch, each as branches holds them."""
    by_head = {(key.split("#")[0], branches[key][0]): key for key in keys if branches[key][0]}
    below = {key: by_head.get((key.split("#")[0], branches[key][1])) for key in keys}
    above = {under: key for key, under in below.items() if under}
    found: list[list[str]] = []
    for key in keys:
        if below[key]:
            continue
        chain = [key]
        while (nxt := above.get(chain[-1])) and nxt not in chain:
            chain.append(nxt)
        found.append(chain)
    placed = {key for chain in found for key in chain}
    return found + [[key] for key in keys if key not in placed]



@dataclass(frozen=True, slots=True)
class PrGraph:
    """An immutable snapshot of PR topology, shared by a projection and its approval overlays."""

    native: Mapping[str, tuple[str, ...]]
    branches: Mapping[str, tuple[str, str]]
    opened: frozenset[str]
    stacks: Mapping[str, tuple[str, ...]]

    @classmethod
    def read(cls, prs: dict[str, dict]) -> "PrGraph":
        """Read topology once and group every open non-native PR into its card stack."""
        native = {key: tuple(pr["native"]) for key, pr in prs.items()}
        branches = {key: (pr["branch"] or "", pr["base"] or "") for key, pr in prs.items()}
        opened = [key for key, pr in prs.items() if pr["state"] == "open" and not native[key]]
        stacks = {key: tuple(stack) for stack in chains(opened, branches) for key in stack}
        return cls(MappingProxyType(native), MappingProxyType(branches), frozenset(opened), MappingProxyType(stacks))

    def stack(self, key: str) -> tuple[str, ...]:
        """The native or branch chain for a PR, including a closed PR among its open peers."""
        if native := self.native[key]:
            return native
        if key in self.opened:
            return self.stacks[key]
        keys = [mate for mate in self.branches if mate == key or mate in self.opened]
        return next(tuple(stack) for stack in chains(keys, self.branches) if key in stack)
