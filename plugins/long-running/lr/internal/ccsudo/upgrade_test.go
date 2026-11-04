package ccsudo

import (
	"context"
	"errors"
	"os"
	"path/filepath"
	"slices"
	"strings"
	"testing"

	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/shell"
)

const (
	matched = "[info] phones: none\n[ok] verifier: /Library/PrivilegedHelperTools/cc-sudo-exec at 0.14.0\n"
	moved   = "[FAIL] verifier: version skew: CLI 0.14.0 vs verifier 0.15.0 — run 'sudo cc-sudo install'\n"
	skewed  = "[FAIL] verifier: version skew: CLI 0.14.0 vs verifier 0.13.0 — run 'sudo cc-sudo install'\n"
	missing = "[FAIL] verifier: /Library/PrivilegedHelperTools/cc-sudo-exec missing — run 'sudo cc-sudo install'\n"
	landed  = "[ok] verifier: /Library/PrivilegedHelperTools/cc-sudo-exec at 0.15.0\n"
	tapInfo = `{"formulae":[],"casks":[{"token":"cc-sudo","version":"0.15.0"}]}`
)

type mac struct {
	doctors []string
	console string
	idle    string
	sign    error
	stamped string
	tap     error
	cask    error
	calls   [][]string
}

func newMac(doctors ...string) *mac {
	return &mac{
		doctors: doctors,
		console: `"IOConsoleLocked" = No`,
		idle:    `"HIDIdleTime" = 5000000000`,
		stamped: "0.15.0\n",
	}
}

func (m *mac) Run(_ context.Context, c shell.Command) (shell.Result, error) {
	m.calls = append(m.calls, c.Argv)
	line := strings.Join(c.Argv, " ")
	switch {
	case line == "cc-sudo doctor":
		out := m.doctors[0]
		if len(m.doctors) > 1 {
			m.doctors = m.doctors[1:]
		}
		if strings.Contains(out, "[FAIL]") {
			return shell.Result{Code: 1, Stdout: out}, &shell.ExitError{Argv: c.Argv, Code: 1}
		}
		return shell.Result{Stdout: out}, nil
	case strings.HasPrefix(line, "brew info"):
		return shell.Result{Stdout: tapInfo}, nil
	case strings.HasPrefix(line, "brew --cache"):
		return shell.Result{Stdout: "/cache/cc-sudo.zip\n"}, nil
	case strings.HasPrefix(line, "brew upgrade"):
		return shell.Result{}, m.cask
	case strings.HasPrefix(line, "ioreg -n"):
		return shell.Result{Stdout: m.console}, nil
	case strings.HasPrefix(line, "ioreg -c"):
		return shell.Result{Stdout: m.idle}, nil
	case strings.HasPrefix(line, "/usr/bin/codesign"):
		return shell.Result{}, m.sign
	case strings.HasPrefix(line, "cc-sudo run"):
		return shell.Result{}, m.tap
	case strings.HasSuffix(line, "/cc-sudo --version"):
		return shell.Result{Stdout: m.stamped}, nil
	}
	return shell.Result{}, nil
}

func (m *mac) index(prefix string) int {
	return slices.IndexFunc(m.calls, func(argv []string) bool {
		return strings.HasPrefix(strings.Join(argv, " "), prefix)
	})
}

func (m *mac) tapped() bool   { return m.index("cc-sudo run") >= 0 }
func (m *mac) upgraded() bool { return m.index("brew upgrade") >= 0 }

func TestUpgradeMovesTheVerifierOnOneTapBeforeTheCask(t *testing.T) {
	m := newMac(matched, moved, landed)
	version, err := upgrade(context.Background(), m)
	if err != nil {
		t.Fatal(err)
	}
	if version != "0.15.0" {
		t.Fatalf("version = %s", version)
	}
	sign, tap, cask := m.index("/usr/bin/codesign"), m.index("cc-sudo run"), m.index("brew upgrade")
	if sign < 0 || sign > tap || tap > cask {
		t.Fatalf("want the signature check, then the tap, then the cask: %q", m.calls)
	}
	run := m.calls[tap]
	binary := run[3]
	if !slices.Equal(run, []string{"cc-sudo", "run", "--", binary, "install"}) || !filepath.IsAbs(binary) {
		t.Fatalf("the tap covers %q", run)
	}
	extract := m.calls[m.index("ditto")]
	if extract[len(extract)-1] != filepath.Dir(binary) || extract[len(extract)-2] != "/cache/cc-sudo.zip" {
		t.Fatalf("extracted %q, ran %s", extract, binary)
	}
	if _, err := os.Stat(filepath.Dir(binary)); !errors.Is(err, os.ErrNotExist) {
		t.Fatalf("the staged binary was left behind: %v", err)
	}
}

func TestUpgradeRefusesAtSkewBecauseNoTapCanCarryIt(t *testing.T) {
	m := newMac(skewed)
	_, err := upgrade(context.Background(), m)
	if err == nil || !strings.Contains(err.Error(), "sudo cc-sudo install") {
		t.Fatalf("err = %v", err)
	}
	if m.index("brew") >= 0 || m.tapped() {
		t.Fatalf("ran past the refusal: %q", m.calls)
	}
}

func TestUpgradeRefusesWithNoVerifierVersion(t *testing.T) {
	m := newMac(missing)
	_, err := upgrade(context.Background(), m)
	if err == nil || !strings.Contains(err.Error(), "sudo cc-sudo install") {
		t.Fatalf("err = %v", err)
	}
	if m.index("brew") >= 0 || m.tapped() {
		t.Fatalf("ran past the refusal: %q", m.calls)
	}
}

func TestUpgradeAsksNothingWhenAlreadyAtTheTapsVersion(t *testing.T) {
	m := newMac(landed)
	version, err := upgrade(context.Background(), m)
	if err != nil || version != "0.15.0" {
		t.Fatalf("version %s, err %v", version, err)
	}
	if m.tapped() || m.upgraded() || m.index("ioreg") >= 0 {
		t.Fatalf("did work at the current version: %q", m.calls)
	}
}

func TestUpgradeAsksNoTapForAnOlderRelease(t *testing.T) {
	m := newMac("[ok] verifier: /Library/PrivilegedHelperTools/cc-sudo-exec at 0.16.0\n")
	_, err := upgrade(context.Background(), m)
	if err == nil || !strings.Contains(err.Error(), "no tap was asked") {
		t.Fatalf("err = %v", err)
	}
	if m.index("brew fetch") >= 0 || m.tapped() || m.upgraded() {
		t.Fatalf("ran past the refusal: %q", m.calls)
	}
}

func TestUpgradeSaysHeMustBeAtTheMac(t *testing.T) {
	locked := newMac(matched)
	locked.console = `"IOConsoleLocked" = Yes`
	loggedOut := newMac(matched)
	loggedOut.console = `"IOConsoleUsers" = ()`
	idle := newMac(matched)
	idle.idle = `"HIDIdleTime" = 8447225166083`
	for name, m := range map[string]*mac{"locked": locked, "logged out": loggedOut, "idle": idle} {
		t.Run(name, func(t *testing.T) {
			_, err := upgrade(context.Background(), m)
			if err == nil || !strings.Contains(err.Error(), "must be at this Mac") {
				t.Fatalf("err = %v", err)
			}
			if m.index("brew fetch") >= 0 || m.tapped() || m.upgraded() {
				t.Fatalf("ran past the refusal: %q", m.calls)
			}
		})
	}
}

func TestUpgradeAsksNoTapForABinaryItCannotVouchFor(t *testing.T) {
	unsigned := newMac(matched)
	unsigned.sign = &shell.ExitError{Argv: []string{"codesign"}, Code: 1}
	stale := newMac(matched)
	stale.stamped = "0.14.0\n"
	for name, m := range map[string]*mac{"unsigned": unsigned, "stale cache": stale} {
		t.Run(name, func(t *testing.T) {
			_, err := upgrade(context.Background(), m)
			if err == nil || !strings.Contains(err.Error(), "no tap was asked") {
				t.Fatalf("err = %v", err)
			}
			if m.tapped() || m.upgraded() {
				t.Fatalf("asked anyway: %q", m.calls)
			}
		})
	}
}

func TestARefusedTapLeavesTheCaskWhereItIs(t *testing.T) {
	m := newMac(matched, matched)
	m.tap = &shell.ExitError{Argv: []string{"cc-sudo"}, Code: 103}
	_, err := upgrade(context.Background(), m)
	if !errors.Is(err, m.tap) {
		t.Fatalf("err = %v", err)
	}
	if m.upgraded() {
		t.Fatal("upgraded the cask over the old verifier, the skew this verb prevents")
	}
}

func TestAnInstallThatStopsAfterTheSwapStillUpgradesTheCaskAndNamesTheFix(t *testing.T) {
	m := newMac(matched, moved, landed)
	m.tap = &shell.ExitError{Argv: []string{"cc-sudo"}, Code: 1}
	_, err := upgrade(context.Background(), m)
	if !errors.Is(err, m.tap) || !strings.Contains(err.Error(), "sudo cc-sudo install") {
		t.Fatalf("err = %v", err)
	}
	if !m.upgraded() {
		t.Fatal("left a new verifier beside the old CLI, which refuses every run")
	}
}

func TestACaskThatFailsAfterTheSwapNamesTheOutage(t *testing.T) {
	m := newMac(matched, moved)
	m.cask = &shell.ExitError{Argv: []string{"brew"}, Code: 1}
	_, err := upgrade(context.Background(), m)
	if !errors.Is(err, m.cask) || !strings.Contains(err.Error(), "brew upgrade --cask") {
		t.Fatalf("err = %v", err)
	}
}

func TestAStillSkewedMacAfterTheCaskIsAFailure(t *testing.T) {
	m := newMac(matched, moved, moved)
	_, err := upgrade(context.Background(), m)
	if err == nil || !strings.Contains(err.Error(), "after the cask upgrade") {
		t.Fatalf("err = %v", err)
	}
}
