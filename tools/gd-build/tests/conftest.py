from __future__ import annotations

import sys
import types
from collections.abc import Callable

import pytest


@pytest.fixture
def install_great_docs_cli(monkeypatch: pytest.MonkeyPatch) -> Callable[[Callable[[], object]], None]:
    def install(main: Callable[[], object]) -> None:
        great_docs = types.ModuleType("great_docs")
        module = types.ModuleType("great_docs.cli")
        module.main = main
        great_docs.cli = module
        monkeypatch.setitem(sys.modules, "great_docs", great_docs)
        monkeypatch.setitem(sys.modules, "great_docs.cli", module)

    return install
