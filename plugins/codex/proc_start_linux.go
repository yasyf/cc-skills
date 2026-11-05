package main

import (
	"bytes"
	"fmt"
	"os"
)

// procStart: the kernel's start tick for pid, field 22 of /proc/<pid>/stat, which
// never changes. ps lstart adds it to /proc/stat btime, whose whole second moves
// on a running machine, so that form reads every live process on the box as recycled.
func procStart(pid int) string {
	stat, err := os.ReadFile(fmt.Sprintf("/proc/%d/stat", pid)) //nolint:gosec // only the pid varies
	if err != nil {
		return ""
	}
	afterCommand := stat[bytes.LastIndexByte(stat, ')')+1:]
	return string(bytes.Fields(afterCommand)[19])
}
