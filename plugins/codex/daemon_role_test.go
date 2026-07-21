package main

import (
	"os"
	"path/filepath"
	"testing"
)

func TestProvisionDaemonRoleInstallsCurrentExecutable(t *testing.T) {
	rolePath := filepath.Join(t.TempDir(), "bin", binaryName)
	if err := provisionDaemonRole(rolePath); err != nil {
		t.Fatal(err)
	}
	want, err := os.Executable()
	if err != nil {
		t.Fatal(err)
	}
	want, err = filepath.EvalSymlinks(want)
	if err != nil {
		t.Fatal(err)
	}
	got, err := filepath.EvalSymlinks(rolePath)
	if err != nil {
		t.Fatal(err)
	}
	if got != want {
		t.Fatalf("role target = %q, want %q", got, want)
	}
	if err := provisionDaemonRole(rolePath); err != nil {
		t.Fatalf("reprovision: %v", err)
	}
}

func TestProvisionDaemonRoleIsNewerWins(t *testing.T) {
	originalVersion := appVersion
	t.Cleanup(func() { appVersion = originalVersion })

	root := t.TempDir()
	rolePath := filepath.Join(root, "bin", binaryName)
	if err := os.MkdirAll(filepath.Dir(rolePath), 0o700); err != nil {
		t.Fatal(err)
	}
	incumbent := filepath.Join(root, "incumbent")
	if err := os.WriteFile(incumbent, []byte("#!/bin/sh\necho 2.0.0\n"), 0o755); err != nil { //nolint:gosec // executable fixture
		t.Fatal(err)
	}
	if err := os.Symlink(incumbent, rolePath); err != nil {
		t.Fatal(err)
	}

	appVersion = "1.0.0"
	if err := provisionDaemonRole(rolePath); err != nil {
		t.Fatal(err)
	}
	got, err := filepath.EvalSymlinks(rolePath)
	if err != nil {
		t.Fatal(err)
	}
	wantIncumbent, err := filepath.EvalSymlinks(incumbent)
	if err != nil {
		t.Fatal(err)
	}
	if got != wantIncumbent {
		t.Fatalf("older caller replaced newer role: got %q, want %q", got, wantIncumbent)
	}

	appVersion = "3.0.0"
	if err := provisionDaemonRole(rolePath); err != nil {
		t.Fatal(err)
	}
	current, err := os.Executable()
	if err != nil {
		t.Fatal(err)
	}
	current, err = filepath.EvalSymlinks(current)
	if err != nil {
		t.Fatal(err)
	}
	got, err = filepath.EvalSymlinks(rolePath)
	if err != nil {
		t.Fatal(err)
	}
	if got != current {
		t.Fatalf("newer caller role target = %q, want %q", got, current)
	}
}
