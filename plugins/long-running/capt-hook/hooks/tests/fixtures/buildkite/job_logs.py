import json
import subprocess
import sys
import time

build = sys.argv[1]
info = json.loads(subprocess.run(["bk", "api", f"/pipelines/release-pr-check/builds/{build}"], capture_output=True, text=True).stdout)
for job in info["jobs"]:
    log = subprocess.run(["bk", "api", f"/pipelines/release-pr-check/builds/{build}/jobs/{job['id']}/log"], capture_output=True, text=True)
    open(f"{job['id']}.log", "w").write(json.loads(log.stdout)["content"])
    time.sleep(0.35)
