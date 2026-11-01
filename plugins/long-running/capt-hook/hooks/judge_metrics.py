from collections.abc import Mapping
from types import ModuleType

from captain_hook.dispatch_metrics import measured_call


def judge_call(module: ModuleType, kind: str, evidence: str, artifact: str, *, backend: str, drive: str | None, timeout: float, env: Mapping[str, str]):
    name = f"{module.__name__}:{kind}:{backend}"

    def run(argv: list[str], *, input: str | None = None, timeout: float, env: Mapping[str, str] | None = None) -> str:
        return measured_call("cli", f"{name}:{argv[0]}", module.command, argv, input=input, timeout=timeout, env=env)

    return measured_call("llm", name, module.judge, kind, evidence, artifact, backend=backend, drive=drive, timeout=timeout, env=env, run=run)
