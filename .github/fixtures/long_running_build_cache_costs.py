import hashlib
import json
from datetime import timedelta
from pathlib import Path

import pytest
from test_desk_runner import build_reads, config, landing_of, runner_module, shell, with_ci_build


def charged_writes():
    values = dict(line.split(": ", 1) for line in Path("/proc/self/io").read_text().splitlines())
    return int(values["write_bytes"])


@pytest.mark.parametrize("population", [32, 10_000])
def test_actual_build_cache_one_new_finished_build_rewrites_history(shell, config, tmp_path, monkeypatch, population):
    with_ci_build(config)
    finished_at = runner_module.actions.stamp(shell.now() - timedelta(hours=1))
    jobs = [{"type": "script", "name": f"job-{number}-" + "x" * 128, "step_key": f"step-{number}", "state": "passed", "soft_failed": False} for number in range(4)]
    stored = {"state": "passed", "commit": "a" * 40, "finished_at": finished_at, "jobs": jobs}
    history = {f"https://buildkite.com/forge/test/builds/{number}": stored for number in range(1, population + 1)}
    cache = config.with_name(f"{config.stem}.{runner_module.BUILDS_FILE}")
    cache.write_text(json.dumps(history))
    initial_bytes = cache.stat().st_size
    landing = landing_of(shell, config, tmp_path)
    costs = {"historical_job_projections": 0, "payload_writes": 0, "payload_bytes": 0}

    class Observed(dict):
        def get(self, key, default=None):
            if key == "jobs":
                costs["historical_job_projections"] += 1
            return super().get(key, default)

    for url in history:
        landing.builds[url] = Observed(landing.builds[url])
    original_write = Path.write_text

    def write(path, text, *args, **kwargs):
        if path.parent == cache.parent and path.name.startswith(f".{cache.name}."):
            costs["payload_writes"] += 1
            costs["payload_bytes"] += len(text.encode())
        return original_write(path, text, *args, **kwargs)

    monkeypatch.setattr(Path, "write_text", write)
    number = "99999999"
    url = f"https://buildkite.com/forge/test/builds/{number}"
    newest = {**stored, "commit": "b" * 40}
    shell.builds[number] = newest
    before = charged_writes()
    resumed = None
    try:
        assert landing.build(url) == newest
        landing.keep_builds()
        first = dict(costs)
        landing.keep_builds()
        assert costs["payload_writes"] == first["payload_writes"]
        resumed = landing_of(shell, config, tmp_path)
        assert resumed.build(url) == newest
        assert len(build_reads(shell, number)) == 1
        assert json.loads(cache.read_text()) == history | {url: newest}
        measured = {"caller": "actual Landing.build/keep_builds + real Book/Store/file writes; FakeShell supplies one exact CLI build result", "population": population, "changed_builds": 1, "source_sha256": hashlib.sha256(Path(runner_module.__file__).read_bytes()).hexdigest(), "initial_cache_bytes": initial_bytes, "final_cache_bytes": cache.stat().st_size, "process_charged_write_bytes": charged_writes() - before, "first_persist": first, "after_unchanged_persist": costs, "remote_build_reads": len(build_reads(shell, number)), "resumed_record_equal": True, "limits": "CI fixed-work counts and logical filesystem payload bytes; process write_bytes is not physical-exclusive host attribution or the captured live file path"}
        print(json.dumps(measured))
        assert first["payload_writes"] == 1
        assert first["payload_bytes"] == cache.stat().st_size >= initial_bytes
        assert first["historical_job_projections"] == population
        assert costs["historical_job_projections"] == 2 * population
    finally:
        landing.close()
        if resumed is not None:
            resumed.close()
