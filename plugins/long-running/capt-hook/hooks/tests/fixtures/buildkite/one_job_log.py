import subprocess
import sys

build = subprocess.run(["bk", "build", "view", sys.argv[1], "-p", "test", "--json"], capture_output=True, text=True).stdout
print(subprocess.run(["bk", "job", "log", sys.argv[2]], capture_output=True, text=True).stdout)
