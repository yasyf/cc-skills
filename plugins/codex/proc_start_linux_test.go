package main

import (
	"os"
	"os/exec"
	"path/filepath"
	"strconv"
	"testing"
)

func TestProcStartReadsTheKernelsStartTickPastAnyCommandName(t *testing.T) {
	script := filepath.Join(mustTempDir(t), "x) R 1 1")
	if err := os.WriteFile(script, []byte("#!/bin/sh\nread _\n"), 0o755); err != nil { //nolint:gosec // the test's own executable script
		t.Fatal(err)
	}
	child := exec.Command(script) //nolint:gosec // runs the script this test wrote
	if _, err := child.StdinPipe(); err != nil {
		t.Fatal(err)
	}
	if err := child.Start(); err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() {
		_ = child.Process.Kill()
		_ = child.Wait()
	})

	self, err := strconv.ParseUint(procStart(os.Getpid()), 10, 64)
	if err != nil {
		t.Fatalf("own start %q is not a tick count: %v", procStart(os.Getpid()), err)
	}
	started, err := strconv.ParseUint(procStart(child.Process.Pid), 10, 64)
	if err != nil {
		t.Fatalf("child start %q is not a tick count: %v", procStart(child.Process.Pid), err)
	}
	if started < self {
		t.Fatalf("a child started at tick %d, before its parent at %d", started, self)
	}
}
