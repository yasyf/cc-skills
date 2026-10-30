from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path


@dataclass
class _Document:
    identity: tuple[int, int, int, int, int]
    lines: dict[int, str]
    words: dict[str, set[int]]


def _identity(path: Path) -> tuple[int, int, int, int, int]:
    info = path.stat()
    return info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns


class DocumentIndex:
    def __init__(self, words: Callable[[str], Iterable[str]], wanted: set[str] | None):
        self.words = words
        self.wanted = wanted
        self.documents: dict[Path, _Document] = {}

    def lines(self, path: Path, wanted: set[str] | None) -> list[tuple[int, str]]:
        if not path.is_file():
            self.documents.pop(path, None)
            return []
        identity = _identity(path)
        document = self.documents.get(path)
        if document is None or document.identity != identity:
            data = path.read_bytes()
            lines, words = {}, {}
            if b"\0" not in data[:4096]:
                for number, text in enumerate(data.decode(errors="replace").splitlines(), start=1):
                    found = set(self.words(text))
                    selected = found if self.wanted is None else found & self.wanted
                    if self.wanted is None or selected:
                        lines[number] = text
                        for word in selected:
                            words.setdefault(word, set()).add(number)
            document = _Document(identity, lines, words)
            try:
                current = _identity(path)
            except OSError:
                current = None
            if current == identity:
                self.documents[path] = document
            else:
                self.documents.pop(path, None)
        numbers = document.lines if wanted is None else {number for word in wanted for number in document.words.get(word, ())}
        return [(number, document.lines[number]) for number in sorted(numbers)]
