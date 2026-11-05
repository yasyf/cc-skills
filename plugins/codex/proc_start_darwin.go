package main

import (
	"os/exec"
	"strconv"
	"strings"
)

// procStart: the process start-time via a fixed binary and pinned env, so a TZ or
// locale change between the launch record and a later probe can't reformat the
// timestamp and make a live lane read dead.
func procStart(pid int) string {
	c := exec.Command("/bin/ps", "-o", "lstart=", "-p", strconv.Itoa(pid)) //nolint:gosec // fixed /bin/ps liveness probe; only the pid varies
	c.Env = envWith("TZ=UTC", "LC_ALL=C")
	out, err := c.Output()
	if err != nil {
		return ""
	}
	return strings.TrimSpace(string(out))
}
