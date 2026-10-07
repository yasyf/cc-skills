import subprocess
from concurrent.futures import ThreadPoolExecutor


def fetch(job_id):
    return subprocess.run(["bk", "job", "log", job_id, "--no-timestamps"], capture_output=True, text=True).stdout


with ThreadPoolExecutor(6) as pool:
    logs = list(pool.map(fetch, open("ids.txt").read().split()))
